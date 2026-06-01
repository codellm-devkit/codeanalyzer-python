################################################################################
# Copyright IBM Corporation 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""CodeQL module for analyzing Python code using CodeQL.

This module provides functionality to create and manage CodeQL databases
for Python projects and execute queries against them.
"""

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from pandas import DataFrame

from codeanalyzer.schema.py_schema import PyCallEdge, PyCallsite, PyModule
from codeanalyzer.semantic_analysis.call_graph import iter_callables_in_symbol_table
from codeanalyzer.semantic_analysis.codeql.codeql_query_runner import CodeQLQueryRunner
from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
from codeanalyzer.schema.py_schema import (
    TaintAnalysisConfig,
    TaintNodeRef,
    TaintSourceConfig,
    TaintSinkConfig,
    PyTaintAnalysisResult,
    PyTaintSource,
    PyTaintSink,
    PyTaintFlow,
)
from codeanalyzer.utils import logger


class _CallableResolver:
    """Maps a CodeQL endpoint ``(file, start_line, name, arity)`` to a Jedi
    ``PyCallable``.

    Resolution ladder:
      1. exact ``(abs_path, start_line)`` — the precise join;
      2. on miss, candidates sharing ``(abs_path, short_name)``: a single
         candidate is taken directly; otherwise prefer those whose
         parameter count equals the CodeQL positional arity, then the
         nearest ``start_line``;
      3. no name match -> ``None`` (caller row skipped / callee becomes
         a ghost node).

    Step 2 recovers edges the ``(file, line)`` join silently drops when
    CodeQL and Jedi disagree on a definition's start line (e.g. decorator
    handling). Jedi's ``parameters`` counts every declared slot (incl.
    ``*args``/``**kwargs``/keyword-only) whereas CodeQL's arity is
    positional only, so the arity filter is exact for plain signatures
    and otherwise yields to the nearest-line tiebreak.
    """

    def __init__(self) -> None:
        self._by_loc: Dict[Tuple[str, int], Any] = {}
        self._by_name: Dict[Tuple[str, str], List[Any]] = {}

    @staticmethod
    def _abs(path: str) -> str:
        try:
            return str(Path(path).resolve())
        except (OSError, RuntimeError):
            return path

    @classmethod
    def from_symbol_table(
        cls, symbol_table: Dict[str, PyModule]
    ) -> "_CallableResolver":
        resolver = cls()
        for c in iter_callables_in_symbol_table(symbol_table):
            abs_path = cls._abs(c.path)
            resolver._by_loc[(abs_path, c.start_line)] = c
            resolver._by_name.setdefault((abs_path, c.name), []).append(c)
        return resolver

    def resolve(
        self, file: str, start_line: int, name: str, arity: int
    ) -> Any:
        exact = self._by_loc.get((file, start_line))
        if exact is not None:
            return exact
        if not name:
            return None
        candidates = self._by_name.get((file, name))
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        arity_matched = [c for c in candidates if len(c.parameters) == arity]
        pool = arity_matched or candidates
        return min(pool, key=lambda c: abs(c.start_line - start_line))


class CodeQL:
    """A class for building the application view of a Python application using CodeQL.

    Args:
        project_dir (str or Path): The path to the root of the Python project.

    Attributes:
        db_path (Path): The path to the CodeQL database.
        temp_db (TemporaryDirectory or None): The temporary directory object if a temporary database was created.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        db_path: Path,
        codeql_bin: Union[str, Path, None] = None,
        codeql_packs_dir: Union[str, Path, None] = None,
        taint_config: Optional[TaintAnalysisConfig] = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.db_path = db_path
        self.codeql_bin = codeql_bin
        self.codeql_packs_dir = codeql_packs_dir
        self.taint_config = taint_config
        self._cached_df: "DataFrame | None" = None

    def _query_call_edges(self) -> DataFrame:
        """Runs the CodeQL query that emits one row per resolved call site.

        The query is written against CodeQL's Python library (``import python``).
        It returns physical location handles for both endpoints so the
        downstream post-processor can join into Jedi's existing
        ``PyCallable.signature`` space via ``(file_path, start_line)`` —
        no signature normalization required.

        Filters:
          * Caller must be a ``Function`` (skip module-level / class-body
            calls — they have no ``PyCallable`` to anchor to).
          * Callee may resolve to anything (in-source or library stub);
            non-application callees become **ghost** nodes downstream so
            RPC / third-party / framework edges are preserved.

        Returns:
            DataFrame: one row per resolved (caller, callee, call-site)
            triple. Duplicate ``(caller_file, caller_start_line,
            callee_file, callee_start_line)`` tuples represent multiple
            call sites in the same caller targeting the same callee and
            are coalesced into a single ``PyCallEdge`` (weight = count)
            by the post-processor.
        """
        query = [
            "/**",
            " * @name Python call-graph edges",
            " * @description One row per resolved call site: caller, callee,",
            " *              and the call-expression location.",
            " * @kind table",
            " * @id py/codeanalyzer/call-graph-edges",
            " */",
            "import python",
            # ``FunctionValue`` / ``ClassValue`` / the ``pointsTo`` predicate
            # live in ObjectAPI, which ``import python`` only brings in as a
            # private import — they aren't re-exported. Pull them in
            # explicitly.
            "import semmle.python.objects.ObjectAPI",
            "",
            # ``Value.getACall()`` is the modern call-resolution API in
            # codeql/python-all 7.x — it returns the ``CallNode`` (CFG)
            # whose target was resolved to that ``Value``. Cleaner than
            # poking at ``pointsTo`` directly.
            # ``callee`` is bound to the FunctionValue's scope so the
            # endpoint emits the same Function-level facts (name, arity,
            # location) the post-processor needs for the name+arity
            # fallback when the (file, start_line) join misses.
            "from CallNode call, Function caller, FunctionValue calleeVal, Function callee",
            "where",
            "  call.getScope() = caller and",
            "  callee = calleeVal.getScope() and",
            "  (",
            # Direct function / bound-method call:  foo()  or  obj.foo()
            "    call = calleeVal.getACall()",
            "    or",
            # Constructor call:  A(...)  resolves to a ClassValue; the actual
            # callee is the class's __init__ (via MRO lookup so subclasses
            # without an explicit __init__ still resolve to the inherited one).
            "    exists(ClassValue clsVal |",
            "      call = clsVal.getACall() and",
            '      clsVal.lookup("__init__") = calleeVal',
            "    )",
            "  )",
            "select",
            # --- Caller endpoint --- (joins to PyCallable: exact by
            #     (file, start_line), else by (file, name) + arity)
            "  caller.getLocation().getFile().getAbsolutePath(),",
            "  caller.getLocation().getStartLine(),",
            "  caller.getQualifiedName(),",
            "  caller.getName(),",
            "  count(caller.getArg(_)),",
            # --- Callee endpoint --- (file/line may live in a library stub;
            #     post-processor classifies as in-source or ghost)
            "  callee.getLocation().getFile().getAbsolutePath(),",
            "  callee.getLocation().getStartLine(),",
            "  calleeVal.getQualifiedName(),",
            "  callee.getName(),",
            "  count(callee.getArg(_)),",
            # --- Call-site location --- (for PyCallsite augmentation)
            "  call.getLocation().getStartLine(),",
            "  call.getLocation().getStartColumn(),",
            "  call.getLocation().getEndLine(),",
            "  call.getLocation().getEndColumn()",
            # ``is_constructor`` is derived in the post-processor by
            # checking whether ``callee_qname`` ends in ``.__init__``;
            # avoids QL's restrictive ``if-then-else`` typing here.
        ]
        if self._cached_df is not None:
            return self._cached_df

        query_string = "\n".join(query)

        with CodeQLQueryRunner(
            self.db_path,
            codeql_bin=self.codeql_bin,
            codeql_packs_dir=self.codeql_packs_dir,
        ) as runner:
            df: DataFrame = runner.execute(
                query_string,
                column_names=[
                    "caller_file",
                    "caller_start_line",
                    "caller_qname",
                    "caller_name",
                    "caller_arity",
                    "callee_file",
                    "callee_start_line",
                    "callee_qname",
                    "callee_name",
                    "callee_arity",
                    "call_start_line",
                    "call_start_column",
                    "call_end_line",
                    "call_end_column",
                ],
            )
        self._cached_df = df
        return df

    @staticmethod
    def _build_callable_resolver(
        symbol_table: Dict[str, PyModule],
    ) -> _CallableResolver:
        """Build the endpoint -> ``PyCallable`` resolver from Jedi.

        Paths are resolved so they match CodeQL's ``getAbsolutePath()``
        regardless of symlinks or the current working directory.
        """
        return _CallableResolver.from_symbol_table(symbol_table)

    @staticmethod
    def _build_callsite_location_index(
        symbol_table: Dict[str, PyModule],
    ) -> Dict[Tuple[str, int], PyCallsite]:
        """Build ``(absolute_file_path, start_line) -> PyCallsite`` from the symbol table.

        Iterates every ``PyCallsite`` in every ``PyCallable.call_sites`` list so
        that taint sources and sinks can be resolved to the rich call-site objects
        already captured during syntactic analysis (receiver type, argument types,
        callee signature, …).

        Paths are resolved to absolute form to match CodeQL's ``getAbsolutePath()``.
        When two call sites share the same (file, start_line) the first one wins
        (ambiguity is rare and an approximation is acceptable here).
        """
        index: Dict[Tuple[str, int], PyCallsite] = {}
        for callable_ in iter_callables_in_symbol_table(symbol_table):
            try:
                abs_path = str(Path(callable_.path).resolve())
            except (OSError, RuntimeError):
                abs_path = callable_.path
            for cs in callable_.call_sites:
                key = (abs_path, cs.start_line)
                if key not in index:
                    index[key] = cs
        return index

    def _iter_resolved_rows(
        self, symbol_table: Dict[str, PyModule]
    ) -> "Iterator[Tuple[str, str, Any]]":
        """Yield ``(source_sig, target_sig, row)`` for every CodeQL row.

        Rows whose caller can't be matched to a ``PyCallable`` in the
        symbol table are skipped. Callee misses fall back to
        ``row.callee_qname`` (ghost). Used by both edge construction and
        call-site augmentation so a single CodeQL query feeds both.
        """
        df = self._query_call_edges()
        if df.empty:
            return
        resolver = self._build_callable_resolver(symbol_table)

        skipped_unknown_caller = 0
        ghost_callees = 0
        for row in df.itertuples(index=False):
            caller = resolver.resolve(
                row.caller_file,
                int(row.caller_start_line),
                row.caller_name,
                int(row.caller_arity),
            )
            if caller is None:
                skipped_unknown_caller += 1
                continue

            callee = resolver.resolve(
                row.callee_file,
                int(row.callee_start_line),
                row.callee_name,
                int(row.callee_arity),
            )
            if callee is not None:
                target_sig = callee.signature
            else:
                target_sig = row.callee_qname
                ghost_callees += 1

            yield caller.signature, target_sig, row

        if skipped_unknown_caller:
            logger.debug(
                f"CodeQL: skipped {skipped_unknown_caller} rows whose caller "
                f"was not in Jedi's symbol table."
            )
        if ghost_callees:
            logger.debug(
                f"CodeQL: {ghost_callees} rows resolved to ghost (external) callees."
            )

    def build_call_graph_edges(
        self, symbol_table: Dict[str, PyModule]
    ) -> List[PyCallEdge]:
        """Run the CodeQL query and turn each row into a ``PyCallEdge``.

        Edges are coalesced on ``(source, target)`` — ``weight`` is the
        number of distinct call sites in the caller targeting the callee.
        Provenance is always ``["codeql"]``; combine with Jedi-derived
        edges via ``call_graph.merge_edges``.
        """
        edge_counts: Counter = Counter()
        for source_sig, target_sig, _row in self._iter_resolved_rows(symbol_table):
            edge_counts[(source_sig, target_sig)] += 1

        return [
            PyCallEdge(
                source=src,
                target=dst,
                weight=count,
                provenance=["codeql"],
            )
            for (src, dst), count in edge_counts.items()
        ]

    def augment_call_sites(self, symbol_table: Dict[str, PyModule]) -> int:
        """Backfill ``PyCallsite.callee_signature`` using CodeQL resolution.

        Walks every CodeQL row, locates the matching ``PyCallsite`` inside
        the caller's ``PyCallable.call_sites`` by call-expression line range
        (``start_line``, ``end_line``), and fills in ``callee_signature``
        **only when Jedi left it empty**. Existing Jedi-resolved signatures
        are kept (Jedi sees lexical context CodeQL can't, e.g. closures).

        Match is by line range — column matching is brittle across the two
        tools' 0- vs 1-based conventions. Ambiguity on a single line
        (e.g. ``a.b().c()``) resolves to the first matching site, which is
        an acceptable approximation given how rarely Jedi misses callees
        on chained call lines.

        Returns:
            Number of ``PyCallsite`` entries augmented.
        """
        resolver = self._build_callable_resolver(symbol_table)
        df = self._query_call_edges()
        if df.empty:
            return 0

        augmented = 0
        for row in df.itertuples(index=False):
            caller = resolver.resolve(
                row.caller_file,
                int(row.caller_start_line),
                row.caller_name,
                int(row.caller_arity),
            )
            if caller is None:
                continue

            callee = resolver.resolve(
                row.callee_file,
                int(row.callee_start_line),
                row.callee_name,
                int(row.callee_arity),
            )
            resolved_sig = callee.signature if callee is not None else row.callee_qname

            call_start = int(row.call_start_line)
            call_end = int(row.call_end_line)
            for site in caller.call_sites:
                if site.start_line != call_start or site.end_line != call_end:
                    continue
                if not site.callee_signature:
                    site.callee_signature = resolved_sig
                    augmented += 1
                break

        if augmented:
            logger.debug(
                f"CodeQL: augmented {augmented} PyCallsite.callee_signature entries."
            )
        return augmented

    def analyze_taint_flows(
        self,
        config_override: Optional[TaintAnalysisConfig] = None,
        symbol_table: Optional[Dict[str, PyModule]] = None,
    ) -> PyTaintAnalysisResult:
        """Perform taint analysis with configurable sources/sinks/sanitizers.

        Args:
            config_override: Optional configuration to override instance config.
            symbol_table: Optional symbol table produced by analysis level 1.
                When provided, taint sources and sinks are resolved to the
                matching ``PyCallsite`` objects already captured during syntactic
                analysis (giving access to receiver type, argument types, callee
                signature, …).  If a match cannot be found a new ``PyCallsite``
                is constructed from the CodeQL location data as a fallback.

        Returns:
            PyTaintAnalysisResult: Complete taint analysis results

        Raises:
            ValueError: If no taint configuration is available
        """
        config = config_override or self.taint_config

        if not config:
            raise ValueError("No taint configuration provided. Pass config to __init__ or analyze_taint_flows()")

        logger.info("Starting taint analysis...")
        logger.debug(f"Configuration: {len(config.sources)} sources, "
                     f"{len(config.sinks)} sinks, {len(config.sanitizers)} sanitizers")

        query_string = TaintQueryGenerator.generate_query(config)
        return self._execute_taint_query(query_string, symbol_table)

    def _execute_taint_query(
        self,
        query_string: str,
        symbol_table: Optional[Dict[str, PyModule]] = None,
    ) -> PyTaintAnalysisResult:
        """Execute a pre-built CodeQL taint query and return structured results.

        Handles database execution, result parsing, and best-effort symbol-table
        linkage for source/sink call sites.  ``file_path`` is always populated
        on every ``PyCallsite`` from the CodeQL row data.

        Args:
            query_string: Complete CodeQL query ready for execution.
            symbol_table: Optional symbol table for enriching call-site objects
                with receiver type, argument types, etc.
        """
        # Build callsite index from symbol table for best-effort linkage
        callsite_index: Dict[Tuple[str, int], PyCallsite] = (
            self._build_callsite_location_index(symbol_table)
            if symbol_table is not None
            else {}
        )
        if callsite_index:
            logger.debug(f"Built callsite index with {len(callsite_index)} entries from symbol table")

        column_names = TaintQueryGenerator.get_column_names()

        logger.debug("Executing CodeQL taint analysis query...")
        with CodeQLQueryRunner(
            self.db_path,
            codeql_bin=self.codeql_bin,
            codeql_packs_dir=self.codeql_packs_dir,
        ) as runner:
            result_df = runner.execute(query_string, column_names)

        logger.info(f"Query returned {len(result_df)} taint flows")

        flows = []
        sources_dict: Dict[str, PyTaintSource] = {}
        sinks_dict: Dict[str, PyTaintSink] = {}
        n_callsite_hits = 0

        for _, row in result_df.iterrows():
            source_key = f"{row['source_file']}:{row['source_start_line']}"
            if source_key not in sources_dict:
                src_cs_key = (row["source_file"], int(row["source_start_line"]))
                if src_cs_key in callsite_index:
                    source_call_site = callsite_index[src_cs_key].model_copy(
                        update={"file_path": row["source_file"]}
                    )
                    n_callsite_hits += 1
                else:
                    source_call_site = PyCallsite(
                        method_name=row["source_expr"] or row["source_function"],
                        receiver_expr=None,
                        start_line=int(row["source_start_line"]),
                        end_line=int(row["source_end_line"]),
                        start_column=int(row["source_start_col"]),
                        end_column=int(row["source_end_col"]),
                        file_path=row["source_file"],
                    )
                source = PyTaintSource(
                    source_type=row["source_type"],
                    call_site=source_call_site,
                    description=f"Untrusted data from {row['source_type']} "
                                f"in {row['source_qualified_function']} "
                                f"({row['source_file']}:{row['source_start_line']})",
                )
                sources_dict[source_key] = source

            sink_key = f"{row['sink_file']}:{row['sink_start_line']}"
            if sink_key not in sinks_dict:
                snk_cs_key = (row["sink_file"], int(row["sink_start_line"]))
                if snk_cs_key in callsite_index:
                    sink_call_site = callsite_index[snk_cs_key].model_copy(
                        update={"file_path": row["sink_file"]}
                    )
                    n_callsite_hits += 1
                else:
                    sink_call_site = PyCallsite(
                        method_name=row["sink_expr"] or row["sink_function"],
                        receiver_expr=None,
                        start_line=int(row["sink_start_line"]),
                        end_line=int(row["sink_end_line"]),
                        start_column=int(row["sink_start_col"]),
                        end_column=int(row["sink_end_col"]),
                        file_path=row["sink_file"],
                    )
                sink = PyTaintSink(
                    sink_type=row["sink_type"],
                    call_site=sink_call_site,
                    severity=row["severity"],
                    vulnerability_type=row["vulnerability_type"],
                    description=f"Potential {row['vulnerability_type']} vulnerability "
                                f"in {row['sink_qualified_function']} "
                                f"({row['sink_file']}:{row['sink_start_line']})",
                )
                sinks_dict[sink_key] = sink

            flow = PyTaintFlow(
                flow_id=row["flow_id"],
                source=sources_dict[source_key],
                sink=sinks_dict[sink_key],
                path=[],
                vulnerability_type=row["vulnerability_type"],
                severity=row["severity"],
                confidence="medium",
                description=row["message"],
            )
            flows.append(flow)

        n_critical = sum(1 for f in flows if f.severity == "critical")
        n_high = sum(1 for f in flows if f.severity == "high")
        logger.info(f"Taint analysis complete: {len(flows)} flows, "
                    f"{n_critical} critical, {n_high} high")
        if callsite_index:
            logger.debug(f"Symbol-table callsite linkage: {n_callsite_hits} of "
                         f"{len(sources_dict) + len(sinks_dict)} source/sink nodes "
                         f"resolved to existing PyCallsite objects")

        return PyTaintAnalysisResult(
            project_path=str(self.project_dir),
            flows=flows,
            analysis_timestamp=datetime.now(timezone.utc).isoformat(),
            codeql_database_path=str(self.db_path),
        )

    # ------------------------------------------------------------------
    # Focused taint analysis — config-build helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_source_configs(
        sources: List[Union["PyTaintSource", TaintNodeRef]],
    ) -> List[TaintSourceConfig]:
        """Convert a mixed list of PyTaintSource / TaintNodeRef to TaintSourceConfig entries.

        ``PyTaintSource`` items are grouped by their ``source_type`` and their
        call-site location (including column when available) becomes a
        ``TaintNodeRef``.  Raw ``TaintNodeRef`` items are grouped under the
        label ``"pinned_source"``.
        """
        groups: Dict[str, List[TaintNodeRef]] = {}
        for item in sources:
            if isinstance(item, PyTaintSource):
                cs = item.call_site
                ref = TaintNodeRef(
                    file_path=cs.file_path,
                    start_line=cs.start_line,
                    start_column=cs.start_column,
                )
                groups.setdefault(item.source_type, []).append(ref)
            else:
                groups.setdefault("pinned_source", []).append(item)
        return [
            TaintSourceConfig(
                name=f"_focused_{source_type}",
                description=f"Focused query: pinned {source_type} call sites",
                source_type=source_type,
                locations=refs,
            )
            for source_type, refs in groups.items()
        ]

    @staticmethod
    def _build_sink_configs(
        sinks: List[Union["PyTaintSink", TaintNodeRef]],
    ) -> List[TaintSinkConfig]:
        """Convert a mixed list of PyTaintSink / TaintNodeRef to TaintSinkConfig entries.

        ``PyTaintSink`` items are grouped by their ``(sink_type, vulnerability_type,
        severity)`` triple.  Raw ``TaintNodeRef`` items are grouped under
        ``sink_type="pinned_sink"``.
        """
        groups: Dict[tuple, List[TaintNodeRef]] = {}
        for item in sinks:
            if isinstance(item, PyTaintSink):
                cs = item.call_site
                ref = TaintNodeRef(
                    file_path=cs.file_path,
                    start_line=cs.start_line,
                    start_column=cs.start_column,
                )
                key = (item.sink_type, item.vulnerability_type or item.sink_type, item.severity)
                groups.setdefault(key, []).append(ref)
            else:
                key = ("pinned_sink", "pinned_sink", "medium")
                groups.setdefault(key, []).append(item)
        return [
            TaintSinkConfig(
                name=f"_focused_sink_{i}",
                description=f"Focused query: pinned {key[1]} call sites",
                sink_type=key[0],
                vulnerability_type=key[1],
                severity=key[2],
                locations=refs,
            )
            for i, (key, refs) in enumerate(groups.items())
        ]

    # ------------------------------------------------------------------
    # Focused taint analysis APIs
    # ------------------------------------------------------------------

    def analyze_taint_flows_from_sources(
        self,
        sources: List[Union[PyTaintSource, TaintNodeRef]],
        config_override: Optional[TaintAnalysisConfig] = None,
        symbol_table: Optional[Dict[str, PyModule]] = None,
    ) -> PyTaintAnalysisResult:
        """Compute taint flows originating from one or more specific call sites.

        Generates a single focused CodeQL query that OR-combines all supplied
        source locations — no post-hoc filtering.  All configured sinks and
        sanitizers remain active; only the source side is narrowed.

        ``PyTaintSource`` items (from a prior ``analyze_taint_flows()`` call)
        are accepted alongside raw ``TaintNodeRef`` instances, so call-site
        data from any analysis tool can be passed directly without
        constructing full schema objects.

        Args:
            sources: One or more source call sites to pin.  Each entry is
                either a ``PyTaintSource`` (carries ``source_type`` and precise
                column info) or a ``TaintNodeRef`` (location only; labelled
                ``"pinned_source"`` in results).
            config_override: Configuration supplying sinks and sanitizers
                (defaults to ``self.taint_config`` when omitted).
            symbol_table: Optional symbol table for call-site linkage.

        Returns:
            ``PyTaintAnalysisResult`` containing only flows that originate at
            one of the pinned source locations.
        """
        if not sources:
            raise ValueError("sources must not be empty.")
        base = config_override or self.taint_config
        if not base:
            raise ValueError("No taint configuration available.")
        logger.info(
            f"Focused taint analysis from {len(sources)} source(s): "
            + ", ".join(
                f"{s.call_site.file_path}:{s.call_site.start_line}"
                if isinstance(s, PyTaintSource) else f"{s.file_path}:{s.start_line}"
                for s in sources
            )
        )
        focused_config = base.model_copy(update={
            "sources": CodeQL._build_source_configs(sources),
            "include_remote_flow_source": False,
        })
        return self._execute_taint_query(TaintQueryGenerator.generate_query(focused_config), symbol_table)

    def analyze_taint_flows_from_source(
        self,
        source: Union[PyTaintSource, TaintNodeRef],
        config_override: Optional[TaintAnalysisConfig] = None,
        symbol_table: Optional[Dict[str, PyModule]] = None,
    ) -> PyTaintAnalysisResult:
        """Single-source convenience wrapper around ``analyze_taint_flows_from_sources``."""
        return self.analyze_taint_flows_from_sources([source], config_override, symbol_table)

    def analyze_taint_flows_to_sinks(
        self,
        sinks: List[Union[PyTaintSink, TaintNodeRef]],
        config_override: Optional[TaintAnalysisConfig] = None,
        symbol_table: Optional[Dict[str, PyModule]] = None,
    ) -> PyTaintAnalysisResult:
        """Compute taint flows reaching one or more specific sink call sites.

        Generates a single focused CodeQL query that OR-combines all supplied
        sink locations — no post-hoc filtering.  All configured sources and
        sanitizers remain active; built-in CodeQL sink classes are suppressed
        so only the pinned locations act as sinks.

        Args:
            sinks: One or more sink call sites to pin.  Each entry is either
                a ``PyTaintSink`` or a ``TaintNodeRef``.
            config_override: Configuration supplying sources and sanitizers
                (defaults to ``self.taint_config`` when omitted).
            symbol_table: Optional symbol table for call-site linkage.

        Returns:
            ``PyTaintAnalysisResult`` containing only flows that reach one of
            the pinned sink locations.
        """
        if not sinks:
            raise ValueError("sinks must not be empty.")
        base = config_override or self.taint_config
        if not base:
            raise ValueError("No taint configuration available.")
        logger.info(
            f"Focused taint analysis to {len(sinks)} sink(s): "
            + ", ".join(
                f"{s.call_site.file_path}:{s.call_site.start_line}"
                if isinstance(s, PyTaintSink) else f"{s.file_path}:{s.start_line}"
                for s in sinks
            )
        )
        focused_config = base.model_copy(update={
            "sinks": CodeQL._build_sink_configs(sinks),
            "disabled_builtin_sinks": TaintQueryGenerator.builtin_sink_names(),
        })
        return self._execute_taint_query(TaintQueryGenerator.generate_query(focused_config), symbol_table)

    def analyze_taint_flows_to_sink(
        self,
        sink: Union[PyTaintSink, TaintNodeRef],
        config_override: Optional[TaintAnalysisConfig] = None,
        symbol_table: Optional[Dict[str, PyModule]] = None,
    ) -> PyTaintAnalysisResult:
        """Single-sink convenience wrapper around ``analyze_taint_flows_to_sinks``."""
        return self.analyze_taint_flows_to_sinks([sink], config_override, symbol_table)

    def analyze_taint_flow_paths(
        self,
        sources: List[Union[PyTaintSource, TaintNodeRef]],
        sinks: List[Union[PyTaintSink, TaintNodeRef]],
        config_override: Optional[TaintAnalysisConfig] = None,
        symbol_table: Optional[Dict[str, PyModule]] = None,
    ) -> PyTaintAnalysisResult:
        """Compute taint flows between specific source and sink call sites.

        Generates a single focused CodeQL query that pins both sides — no
        post-hoc filtering.

        Args:
            sources: One or more source call sites (``PyTaintSource`` or
                ``TaintNodeRef``).
            sinks: One or more sink call sites (``PyTaintSink`` or
                ``TaintNodeRef``).
            config_override: Configuration supplying sanitizers (defaults to
                ``self.taint_config`` when omitted).
            symbol_table: Optional symbol table for call-site linkage.

        Returns:
            ``PyTaintAnalysisResult`` containing only flows from one of the
            pinned sources to one of the pinned sinks.
        """
        if not sources:
            raise ValueError("sources must not be empty.")
        if not sinks:
            raise ValueError("sinks must not be empty.")
        base = config_override or self.taint_config
        if not base:
            raise ValueError("No taint configuration available.")
        logger.info(
            f"Focused taint analysis: {len(sources)} source(s) → {len(sinks)} sink(s)"
        )
        focused_config = base.model_copy(update={
            "sources": CodeQL._build_source_configs(sources),
            "sinks": CodeQL._build_sink_configs(sinks),
            "include_remote_flow_source": False,
            "disabled_builtin_sinks": TaintQueryGenerator.builtin_sink_names(),
        })
        return self._execute_taint_query(TaintQueryGenerator.generate_query(focused_config), symbol_table)
