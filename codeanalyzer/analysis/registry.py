################################################################################
# Copyright IBM Corporation 2026
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

"""Analysis-pass discovery, ordering, and execution.

* ``discover_passes`` — instantiate every in-tree built-in pass plus
  every out-of-tree pass registered under the
  ``codeanalyzer.analysis_passes`` entry-point group.
* ``order_passes`` — topologically sort by declared
  ``requires``/``provides`` capabilities; an unsatisfied requirement or
  a dependency cycle raises ``PassOrderingError``.
* ``run_pipeline`` — build the context, run the ordered passes, merge
  each result into the running ``PyApplication`` so downstream passes
  see upstream contributions, and return the enriched application.

Pass output is intentionally **not** cached: core caches only the symbol
table and base call graph, and re-runs the pipeline on every
``analyze()``, so synthetic edges/entrypoints can never go stale when an
extension changes.
"""

from __future__ import annotations

import sys
from collections import defaultdict, deque
from typing import Dict, List

from codeanalyzer.analysis._pass import (
    ANALYSIS_PASS_ENTRYPOINT_GROUP,
    AnalysisContext,
    AnalysisPass,
)
from codeanalyzer.schema.py_schema import PyApplication
from codeanalyzer.semantic_analysis.call_graph import (
    iter_classes_in_symbol_table,
    merge_edges,
)
from codeanalyzer.utils import logger

#: In-tree passes that ship with core. Concrete framework finders (Flask,
#: Django, ...) are appended here as they land; the list is empty until
#: then. Out-of-tree passes arrive via entry points.
BUILTIN_PASS_FACTORIES: List[type] = []


class PassOrderingError(RuntimeError):
    """Raised when pass ``requires``/``provides`` cannot be satisfied."""


def _iter_entry_points():
    """Yield entry points in the analysis-pass group, version-portably."""
    from importlib.metadata import entry_points

    if sys.version_info >= (3, 10):
        yield from entry_points(group=ANALYSIS_PASS_ENTRYPOINT_GROUP)
    else:  # pragma: no cover - legacy interpreters
        yield from entry_points().get(ANALYSIS_PASS_ENTRYPOINT_GROUP, [])


def discover_passes() -> List[AnalysisPass]:
    """Instantiate all built-in and entry-point-registered passes.

    A broken or non-conforming entry point is logged and skipped rather
    than aborting the whole analysis.
    """
    passes: List[AnalysisPass] = []

    for factory in BUILTIN_PASS_FACTORIES:
        passes.append(factory())

    for ep in _iter_entry_points():
        try:
            obj = ep.load()
            instance = obj() if isinstance(obj, type) else obj
        except Exception as e:  # noqa: BLE001 - extension code is untrusted
            logger.warning(
                f"Skipping analysis pass '{ep.name}' "
                f"({getattr(ep, 'value', ep)}): failed to load: {e}"
            )
            continue
        if not isinstance(instance, AnalysisPass):
            logger.warning(
                f"Skipping entry point '{ep.name}': "
                f"{type(instance).__name__} is not an AnalysisPass"
            )
            continue
        passes.append(instance)

    return passes


def order_passes(passes: List[AnalysisPass]) -> List[AnalysisPass]:
    """Topologically sort ``passes`` by ``requires``/``provides``.

    Edge ``B -> A`` exists when ``A`` requires a capability ``B``
    provides (``A`` must run after ``B``). Ties are broken by ``name``
    for determinism. Raises ``PassOrderingError`` on an unsatisfied
    requirement or a cycle.
    """
    if not passes:
        return []

    providers: Dict[str, List[AnalysisPass]] = defaultdict(list)
    for p in passes:
        for cap in p.provides:
            providers[cap].append(p)

    ids = {id(p): p for p in passes}
    # successors[x] = passes that must run after x; indegree counts deps.
    successors: Dict[int, List[int]] = defaultdict(list)
    indegree: Dict[int, int] = {id(p): 0 for p in passes}

    for p in passes:
        for cap in p.requires:
            if cap not in providers:
                raise PassOrderingError(
                    f"Pass '{p.name or type(p).__name__}' requires "
                    f"capability '{cap}', which no installed pass provides."
                )
            for dep in providers[cap]:
                if dep is p:
                    continue
                successors[id(dep)].append(id(p))
                indegree[id(p)] += 1

    ready = deque(
        sorted(
            (pid for pid, d in indegree.items() if d == 0),
            key=lambda pid: ids[pid].name or type(ids[pid]).__name__,
        )
    )
    ordered: List[AnalysisPass] = []
    while ready:
        pid = ready.popleft()
        ordered.append(ids[pid])
        newly_ready = []
        for succ in successors[pid]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                newly_ready.append(succ)
        for s in sorted(
            newly_ready, key=lambda x: ids[x].name or type(ids[x]).__name__
        ):
            ready.append(s)

    if len(ordered) != len(passes):
        stuck = [
            ids[pid].name or type(ids[pid]).__name__
            for pid, d in indegree.items()
            if d > 0
        ]
        raise PassOrderingError(
            f"Cyclic requires/provides dependency among passes: {stuck}"
        )

    return ordered


def make_default_context(app: PyApplication) -> AnalysisContext:
    """Build the context shared by every pass.

    ``external_bindings`` is empty (no routing pre-pass is wired in core
    yet). ``resolve_base_chain`` walks the symbol table best-effort.
    """
    by_sig = {}
    by_short: Dict[str, list] = defaultdict(list)
    for cls in iter_classes_in_symbol_table(app.symbol_table):
        by_sig[cls.signature] = cls
        by_short[cls.name].append(cls)

    def resolve_base_chain(fqcn: str) -> List[str]:
        chain: List[str] = []
        seen: set = set()

        def visit(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            chain.append(name)
            cls = by_sig.get(name)
            if cls is None:
                short = name.rsplit(".", 1)[-1]
                candidates = by_short.get(short, [])
                cls = candidates[0] if len(candidates) == 1 else None
            if cls is None:
                return
            for base in cls.base_classes:
                if base in by_sig:
                    visit(base)
                else:
                    short = base.rsplit(".", 1)[-1]
                    candidates = by_short.get(short, [])
                    if len(candidates) == 1:
                        visit(candidates[0].signature)
                    else:
                        chain.append(base)

        visit(fqcn)
        return chain

    return AnalysisContext(
        external_bindings={},
        resolve_base_chain=resolve_base_chain,
    )


def run_pipeline(app: PyApplication) -> PyApplication:
    """Discover, order, and run every analysis pass over ``app``.

    Mutates and returns ``app``: each pass's entrypoints are appended to
    ``app.entrypoints`` (keyed by ``PyEntrypoint.framework``) and its
    synthetic edges are folded into ``app.call_graph`` via ``merge_edges``
    *before* the next pass runs, so passes compose.
    """
    passes = discover_passes()
    if not passes:
        return app

    ordered = order_passes(passes)
    ctx = make_default_context(app)
    logger.info(
        "Running analysis passes: "
        + ", ".join(p.name or type(p).__name__ for p in ordered)
    )

    for p in ordered:
        pname = p.name or type(p).__name__
        try:
            result = p.run(app, ctx)
        except Exception as e:  # noqa: BLE001 - extension code is untrusted
            logger.warning(f"Analysis pass '{pname}' failed, skipping: {e}")
            continue

        for ep in result.entrypoints:
            app.entrypoints.setdefault(ep.framework, []).append(ep)
        if result.call_edges:
            app.call_graph = merge_edges(app.call_graph, result.call_edges)

    return app
