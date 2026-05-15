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
    PyTaintAnalysisResult,
    PyTaintSource,
    PyTaintSink,
    PyTaintFlow,
)
from codeanalyzer.utils import logger


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
            "from CallNode call, Function caller, FunctionValue calleeVal",
            "where",
            "  call.getScope() = caller and",
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
            # --- Caller endpoint --- (joins to PyCallable via file + start_line)
            "  caller.getLocation().getFile().getAbsolutePath(),",
            "  caller.getLocation().getStartLine(),",
            "  caller.getQualifiedName(),",
            # --- Callee endpoint --- (file/line may live in a library stub;
            #     post-processor classifies as in-source or ghost)
            "  calleeVal.getScope().getLocation().getFile().getAbsolutePath(),",
            "  calleeVal.getScope().getLocation().getStartLine(),",
            "  calleeVal.getQualifiedName(),",
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
                    "callee_file",
                    "callee_start_line",
                    "callee_qname",
                    "call_start_line",
                    "call_start_column",
                    "call_end_line",
                    "call_end_column",
                ],
            )
        self._cached_df = df
        return df

    @staticmethod
    def _build_callable_location_index(
        symbol_table: Dict[str, PyModule],
    ) -> Dict[Tuple[str, int], "PyCallable"]:
        """Build ``(absolute_file_path, start_line) -> PyCallable`` from Jedi.

        Paths are resolved so they match CodeQL's ``getAbsolutePath()``
        regardless of symlinks or the current working directory.
        """
        from codeanalyzer.schema.py_schema import PyCallable  # local to avoid cycle

        index: Dict[Tuple[str, int], PyCallable] = {}
        for c in iter_callables_in_symbol_table(symbol_table):
            try:
                abs_path = str(Path(c.path).resolve())
            except (OSError, RuntimeError):
                abs_path = c.path
            index[(abs_path, c.start_line)] = c
        return index

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
        location_index = self._build_callable_location_index(symbol_table)

        skipped_unknown_caller = 0
        ghost_callees = 0
        for row in df.itertuples(index=False):
            caller_key = (row.caller_file, int(row.caller_start_line))
            caller = location_index.get(caller_key)
            if caller is None:
                skipped_unknown_caller += 1
                continue

            callee_key = (row.callee_file, int(row.callee_start_line))
            callee = location_index.get(callee_key)
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
        location_index = self._build_callable_location_index(symbol_table)
        df = self._query_call_edges()
        if df.empty:
            return 0

        augmented = 0
        for row in df.itertuples(index=False):
            caller_key = (row.caller_file, int(row.caller_start_line))
            caller = location_index.get(caller_key)
            if caller is None:
                continue

            callee_key = (row.callee_file, int(row.callee_start_line))
            callee = location_index.get(callee_key)
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

        # Build callsite index from symbol table for best-effort linkage
        callsite_index: Dict[Tuple[str, int], PyCallsite] = (
            self._build_callsite_location_index(symbol_table)
            if symbol_table is not None
            else {}
        )
        if callsite_index:
            logger.debug(f"Built callsite index with {len(callsite_index)} entries from symbol table")

        query_string = TaintQueryGenerator.generate_query(config)
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
                # Try to resolve from symbol table; fall back to constructing new
                src_cs_key = (row["source_file"], int(row["source_start_line"]))
                source_call_site = callsite_index.get(src_cs_key) or PyCallsite(
                    method_name=row["source_expr"] or row["source_function"],
                    receiver_expr=None,
                    start_line=int(row["source_start_line"]),
                    end_line=int(row["source_end_line"]),
                    start_column=int(row["source_start_col"]),
                    end_column=int(row["source_end_col"]),
                )
                if src_cs_key in callsite_index:
                    n_callsite_hits += 1
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
                # Try to resolve from symbol table; fall back to constructing new
                snk_cs_key = (row["sink_file"], int(row["sink_start_line"]))
                sink_call_site = callsite_index.get(snk_cs_key) or PyCallsite(
                    method_name=row["sink_expr"] or row["sink_function"],
                    receiver_expr=None,
                    start_line=int(row["sink_start_line"]),
                    end_line=int(row["sink_end_line"]),
                    start_column=int(row["sink_start_col"]),
                    end_column=int(row["sink_end_col"]),
                )
                if snk_cs_key in callsite_index:
                    n_callsite_hits += 1
                sink = PyTaintSink(
                    sink_type=row["sink_type"],
                    call_site=sink_call_site,
                    severity=row["severity"],
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
