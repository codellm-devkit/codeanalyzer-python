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

"""Adapters between the persisted call-graph schema and ``networkx``.

The schema persists the call graph as ``List[PyCallEdge]`` with signatures
referencing ``PyCallable`` entries already in the symbol table. These
helpers rehydrate it into a ``networkx.DiGraph`` for in-process queries
(paths, callers, callees) and reduce a built ``DiGraph`` back to the
serializable edge list.
"""

from collections import Counter
from typing import Dict, Iterator, List, Tuple

import networkx as nx

from codeanalyzer.schema.py_schema import (
    PyApplication,
    PyCallable,
    PyCallEdge,
    PyClass,
    PyModule,
)


def _walk_class_callables(cls: PyClass) -> Iterator[PyCallable]:
    for method in cls.methods.values():
        yield from _walk_callable(method)
    for inner in cls.inner_classes.values():
        yield from _walk_class_callables(inner)


def _walk_callable(c: PyCallable) -> Iterator[PyCallable]:
    yield c
    for inner in c.inner_callables.values():
        yield from _walk_callable(inner)
    for inner_cls in c.inner_classes.values():
        yield from _walk_class_callables(inner_cls)


def _walk_module_callables(module: PyModule) -> Iterator[PyCallable]:
    for fn in module.functions.values():
        yield from _walk_callable(fn)
    for cls in module.classes.values():
        yield from _walk_class_callables(cls)


def iter_callables_in_symbol_table(
    symbol_table: Dict[str, PyModule],
) -> Iterator[PyCallable]:
    """Yield every ``PyCallable`` in a symbol table, recursively."""
    for module in symbol_table.values():
        yield from _walk_module_callables(module)


def _walk_classes_in_class(cls: PyClass) -> Iterator[PyClass]:
    yield cls
    for inner in cls.inner_classes.values():
        yield from _walk_classes_in_class(inner)
    # Classes can live inside methods (e.g. a factory method that defines
    # a helper class). Recurse through every method's callable subtree.
    for method in cls.methods.values():
        yield from _walk_classes_in_callable(method)


def _walk_classes_in_callable(c: PyCallable) -> Iterator[PyClass]:
    for inner_cls in c.inner_classes.values():
        yield from _walk_classes_in_class(inner_cls)
    for inner in c.inner_callables.values():
        yield from _walk_classes_in_callable(inner)


def iter_classes_in_symbol_table(
    symbol_table: Dict[str, PyModule],
) -> Iterator[PyClass]:
    """Yield every ``PyClass`` in a symbol table, recursively — including
    inner classes, classes nested in functions, and classes nested in
    class methods."""
    for module in symbol_table.values():
        for cls in module.classes.values():
            yield from _walk_classes_in_class(cls)
        for fn in module.functions.values():
            yield from _walk_classes_in_callable(fn)


def iter_callables(app: PyApplication) -> Iterator[PyCallable]:
    """Yield every ``PyCallable`` in the application, recursively."""
    yield from iter_callables_in_symbol_table(app.symbol_table)


def callables_by_signature(app: PyApplication) -> Dict[str, PyCallable]:
    """Flat ``signature -> PyCallable`` index for O(1) node lookup."""
    return {c.signature: c for c in iter_callables(app)}


def to_digraph(app: PyApplication) -> nx.DiGraph:
    """Build a ``networkx.DiGraph`` from a ``PyApplication``.

    Nodes are keyed by ``PyCallable.signature``. Nodes for in-source
    callables carry a ``callable`` attribute holding the full
    ``PyCallable`` and ``ghost=False``. Endpoints referenced by edges
    but absent from the symbol table — RPC targets, third-party
    libraries, framework callbacks, dynamically resolved callees — are
    added as **ghost** nodes (``callable=None``, ``ghost=True``) so the
    edges are preserved.

    Edges carry ``type``, ``weight``, and ``provenance`` attributes.
    """
    g = nx.DiGraph()
    by_sig = callables_by_signature(app)
    for sig, c in by_sig.items():
        g.add_node(sig, callable=c, ghost=False)
    for e in app.call_graph:
        for sig in (e.source, e.target):
            if sig not in g.nodes:
                g.add_node(sig, callable=None, ghost=True)
        g.add_edge(
            e.source,
            e.target,
            type=e.type,
            weight=e.weight,
            provenance=list(e.provenance),
        )
    return g


def from_digraph(g: nx.DiGraph) -> list:
    """Reduce a ``DiGraph`` to the persisted ``List[PyCallEdge]`` form.

    Only edges are extracted; nodes are not serialized here — they are
    expected to already exist as ``PyCallable`` entries in the symbol
    table. Edge attributes default to ``CALL_DEP`` / weight 1 / empty
    provenance when missing.
    """
    edges = []
    for src, dst, data in g.edges(data=True):
        edges.append(
            PyCallEdge(
                source=src,
                target=dst,
                type=data.get("type", "CALL_DEP"),
                weight=int(data.get("weight", 1)),
                provenance=list(data.get("provenance", [])),
            )
        )
    return edges


def jedi_call_graph_edges(
    symbol_table: Dict[str, PyModule],
) -> List[PyCallEdge]:
    """Derive ``PyCallEdge`` entries from Jedi's per-callable ``call_sites``.

    For every ``PyCallable`` in the symbol table, each ``PyCallsite`` whose
    ``callee_signature`` is resolved (non-empty) contributes an edge
    ``caller.signature -> site.callee_signature``. Sites where Jedi failed
    to resolve the callee (``callee_signature`` is ``None`` or empty) are
    skipped — they have no anchor to put on the graph.

    Edges are coalesced on ``(source, target)``: ``weight`` is the count of
    matching sites. Provenance is always ``["jedi"]``; combine with
    PyCG-derived edges via ``merge_edges``.
    """
    counts: Counter = Counter()
    for caller in iter_callables_in_symbol_table(symbol_table):
        for site in caller.call_sites:
            if not site.callee_signature:
                continue
            counts[(caller.signature, site.callee_signature)] += 1

    return [
        PyCallEdge(source=src, target=dst, weight=n, provenance=["jedi"])
        for (src, dst), n in counts.items()
    ]


def resolve_unresolved_constructors(symbol_table: Dict[str, PyModule]) -> int:
    """Fill in ``PyCallsite.callee_signature`` for unresolved constructor sites.

    When Jedi fails to resolve a constructor call (commonly
    for classes nested inside functions or methods, where static-analysis
    points-to is weakest), Jedi still flags the site as
    ``is_constructor_call=True`` with ``method_name`` set to the class's
    short name. This pass does the resolution heuristically:

    1. Build a ``short_name -> [PyClass]`` index from all classes in the
       symbol table.
    2. For each unresolved constructor site under a caller ``C``, look up
       candidates by ``site.method_name`` and prefer the class whose
       ``signature`` is the longest prefix-ancestor of ``C.signature`` —
       this approximates Python's LEGB scoping for nested classes.
    3. Set ``callee_signature = f"{class.signature}.__init__"``.

    Returns the number of sites resolved. Best-effort; sites with no
    matching class or ambiguous candidates with no scope tiebreaker are
    left as-is.
    """
    by_name: Dict[str, List[PyClass]] = {}
    for cls in iter_classes_in_symbol_table(symbol_table):
        by_name.setdefault(cls.name, []).append(cls)

    resolved = 0
    for caller in iter_callables_in_symbol_table(symbol_table):
        for site in caller.call_sites:
            if not site.is_constructor_call or site.callee_signature:
                continue
            candidates = by_name.get(site.method_name)
            if not candidates:
                continue

            # Prefer the class whose signature is the longest prefix of
            # the caller's signature (closest enclosing scope).
            def scope_score(c: PyClass, _caller_sig: str = caller.signature) -> int:
                cls_sig = c.signature
                parent_sig = cls_sig.rsplit(".", 1)[0] if "." in cls_sig else ""
                # Score = length of parent_sig if it's a prefix of caller's
                # signature, else -1 (not in scope, lowest priority).
                if parent_sig and _caller_sig.startswith(parent_sig):
                    return len(parent_sig)
                # Module-level class (parent_sig is the module path) — give
                # it a base score so it still wins over no match.
                return 0 if not parent_sig else -1

            best = max(candidates, key=scope_score)
            if scope_score(best) < 0:
                # No candidate is reachable from caller's scope.
                continue

            site.callee_signature = f"{best.signature}.__init__"
            resolved += 1

    return resolved


def merge_edges(*edge_lists: list) -> list:
    """Merge multiple ``List[PyCallEdge]`` into one.

    Edges with the same ``(source, target)`` are coalesced: weights sum,
    provenance is the sorted union. Useful for combining edges produced
    by different backends (e.g. Jedi + PyCG).
    """
    by_key: Dict[Tuple[str, str], PyCallEdge] = {}
    for edges in edge_lists:
        for e in edges:
            k = (e.source, e.target)
            if k in by_key:
                cur = by_key[k]
                cur.weight += e.weight
                cur.provenance = sorted(set(cur.provenance) | set(e.provenance))
            else:
                by_key[k] = e.model_copy()
    return list(by_key.values())
