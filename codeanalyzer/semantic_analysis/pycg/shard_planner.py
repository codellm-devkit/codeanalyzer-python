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

"""Coupling-aware shard planning for PyCG call-graph construction.

Sharding splits a project so PyCG can be run on each part independently; any
call edge whose caller and callee land in *different* shards becomes a ghost
node in both, so PyCG never resolves it.  The directory + file-count heuristic
is blind to coupling and can sever heavily-interacting modules.  This planner
instead partitions the **module dependency graph derived from the Jedi call
graph** (already computed at analysis level 1), so the cuts fall on the weakest
seams.

Pipeline:

1. **Module graph** — project the Jedi ``PyCallEdge`` list (callable→callable)
   down to a weighted directed graph over *modules*.  ``weight(A, B)`` is the
   number of Jedi call sites from a callable in module ``A`` to one in ``B``.
2. **SCC condensation** — modules in an import/call cycle must be co-computed
   (splitting them breaks both shards), so each strongly-connected component is
   collapsed into one indivisible unit via Tarjan's algorithm.
3. **Community detection** — Louvain modularity over the undirected weighted
   condensation groups tightly-coupled units; each community is a candidate
   shard.
4. **Budget enforcement** — communities over the per-shard file budget are
   re-partitioned (higher Louvain resolution, then a greedy first-fit fallback
   that guarantees termination); communities under budget are agglomeratively
   merged by inter-shard weight to recover cut edges and reduce shard count.

The result is a list of shards (each a list of file paths) plus a metrics
report whose headline figure is ``cut_ratio`` — the fraction of inter-module
Jedi edge weight severed by the partition.  Lower is better; it is the
estimated upper bound on PyCG edges lost to sharding.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Set, Tuple

import networkx as nx

from codeanalyzer.schema.py_schema import PyCallable, PyCallEdge, PyClass, PyModule

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Symbol-table walking: callable / class signature -> defining file
# ----------------------------------------------------------------------------

def _walk_callable_sigs(c: PyCallable) -> Iterator[str]:
    yield c.signature
    for inner in c.inner_callables.values():
        yield from _walk_callable_sigs(inner)
    for inner_cls in c.inner_classes.values():
        yield from _walk_class_sigs(inner_cls)


def _walk_class_sigs(cls: PyClass) -> Iterator[str]:
    yield cls.signature
    for method in cls.methods.values():
        yield from _walk_callable_sigs(method)
    for inner in cls.inner_classes.values():
        yield from _walk_class_sigs(inner)


def _signature_to_file(symbol_table: Dict[str, PyModule]) -> Dict[str, str]:
    """Map every callable/class signature in the project to its defining file.

    Built by walking each module's full nesting tree, recording the file that
    actually defines each callable.  Files are the partition unit because
    ``PyModule.module_name`` is only the file *stem* (``py_file.stem``), which
    collides heavily across a real project (every ``__init__.py``, ``models.py``
    …) — keying the module graph by name would collapse unrelated files into
    one node and silently drop files from shards.  ``file_path`` is unique.
    """
    sig_to_file: Dict[str, str] = {}
    for module in symbol_table.values():
        for fn in module.functions.values():
            for sig in _walk_callable_sigs(fn):
                sig_to_file[sig] = module.file_path
        for cls in module.classes.values():
            for sig in _walk_class_sigs(cls):
                sig_to_file[sig] = module.file_path
    return sig_to_file


# ----------------------------------------------------------------------------
# Result type
# ----------------------------------------------------------------------------

@dataclass
class ShardPlan:
    """Output of :func:`plan_shards`.

    ``shards`` is what the PyCG executor consumes: each inner list is the set of
    project file paths to analyse together as one shard.  ``module_shards`` is
    the same partition expressed in module names (handy for logging/tests).
    """

    shards: List[List[str]] = field(default_factory=list)
    module_shards: List[List[str]] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        m = self.metrics
        return (
            f"ShardPlan({len(self.shards)} shards, "
            f"cut_ratio={m.get('cut_ratio', 0):.3f}, "
            f"max_shard_files={int(m.get('max_shard_files', 0))}, "
            f"oversized={int(m.get('oversized_shards', 0))})"
        )


# ----------------------------------------------------------------------------
# Module dependency graph
# ----------------------------------------------------------------------------

def build_module_graph(
    symbol_table: Dict[str, PyModule],
    jedi_edges: List[PyCallEdge],
) -> nx.DiGraph:
    """Project Jedi callable→callable edges onto a weighted file DiGraph.

    Nodes are file paths (the unique, collision-free partition unit — see
    :func:`_signature_to_file`); each carries a ``module_name`` attribute for
    readable reporting.  Every project file is a node (isolated files
    included).  Edge weight is the summed Jedi weight of cross-file call sites;
    intra-file edges and edges touching external/library symbols (no
    symbol-table entry) are dropped — they cannot influence the partition.
    """
    sig_to_file = _signature_to_file(symbol_table)

    g = nx.DiGraph()
    for module in symbol_table.values():
        g.add_node(module.file_path, module_name=module.module_name)

    for edge in jedi_edges:
        src = sig_to_file.get(edge.source)
        dst = sig_to_file.get(edge.target)
        if src is None or dst is None or src == dst:
            continue
        if g.has_edge(src, dst):
            g[src][dst]["weight"] += edge.weight
        else:
            g.add_edge(src, dst, weight=edge.weight)
    return g


# ----------------------------------------------------------------------------
# Partitioning
# ----------------------------------------------------------------------------

def _undirected_weighted(g: nx.DiGraph) -> nx.Graph:
    """Collapse a directed weighted graph to undirected, summing both directions."""
    h = nx.Graph()
    h.add_nodes_from(g.nodes())
    for u, v, w in g.edges(data="weight", default=1):
        if h.has_edge(u, v):
            h[u][v]["weight"] += w
        else:
            h.add_edge(u, v, weight=w)
    return h


def _communities(h: nx.Graph, resolution: float, seed: int) -> List[Set[str]]:
    """Weighted community detection, preferring Louvain with a graceful fallback."""
    if h.number_of_nodes() == 0:
        return []
    if hasattr(nx.community, "louvain_communities"):
        return [
            set(c)
            for c in nx.community.louvain_communities(
                h, weight="weight", resolution=resolution, seed=seed
            )
        ]
    # networkx < 3.0 (Python 3.9/3.10 floor): greedy modularity has no seed.
    return [set(c) for c in nx.community.greedy_modularity_communities(h, weight="weight")]


def _greedy_bin_pack(
    units: List[Tuple[frozenset, int]], budget: int
) -> List[Set[str]]:
    """First-fit-decreasing pack (unit -> modules, size) into <= budget bins.

    Termination guarantee for the recursive splitter: a community that Louvain
    refuses to cut still gets divided here.  A single unit larger than the
    budget (an atomic SCC — a real import cycle) is emitted on its own; that is
    unavoidable without breaking edges.
    """
    bins: List[Tuple[Set[str], int]] = []
    for members, size in sorted(units, key=lambda u: u[1], reverse=True):
        placed = False
        for b in bins:
            if b[1] + size <= budget:
                b[0].update(members)
                bins[bins.index(b)] = (b[0], b[1] + size)
                placed = True
                break
        if not placed:
            bins.append((set(members), size))
    return [b[0] for b in bins]


def _split_oversized(
    h: nx.Graph,
    community: Set[str],
    unit_size: Dict[str, int],
    budget: int,
    seed: int,
    depth: int = 0,
) -> List[Set[str]]:
    """Recursively partition a community whose file count exceeds the budget."""
    size = sum(unit_size[n] for n in community)
    if size <= budget or len(community) == 1:
        # Fits, or is a single atomic SCC unit that cannot be split further.
        return [community]

    sub = h.subgraph(community)
    # Escalate resolution so Louvain cuts more aggressively each level.
    parts = _communities(sub, resolution=2.0 * (depth + 1), seed=seed)
    parts = [p for p in parts if p]

    if len(parts) <= 1:
        # Louvain could not divide it (one dense blob) — fall back to bin packing
        # so the budget is still honoured.
        units = [(frozenset([n]), unit_size[n]) for n in community]
        return _greedy_bin_pack(units, budget)

    out: List[Set[str]] = []
    for p in parts:
        out.extend(_split_oversized(h, p, unit_size, budget, seed, depth + 1))
    return out


def _merge_small(
    shards: List[Set[str]],
    h: nx.Graph,
    unit_size: Dict[str, int],
    budget: int,
) -> List[Set[str]]:
    """Agglomeratively merge under-budget shards by inter-shard weight.

    Recovers cut edges: merging two shards turns the edges between them back
    into intra-shard edges.  Greedy — repeatedly merge the heaviest-coupled
    pair that still fits the budget — which is enough to mop up the small
    fragments Louvain leaves behind without reintroducing oversized shards.
    """
    shards = [set(s) for s in shards]
    sizes = [sum(unit_size[n] for n in s) for s in shards]

    def pair_weight(a: Set[str], b: Set[str]) -> float:
        w = 0.0
        for u in a:
            for v in h.adj[u]:
                if v in b:
                    w += h[u][v]["weight"]
        return w

    while True:
        best: Optional[Tuple[int, int]] = None
        best_w = 0.0
        for i in range(len(shards)):
            for j in range(i + 1, len(shards)):
                if sizes[i] + sizes[j] > budget:
                    continue
                w = pair_weight(shards[i], shards[j])
                if w > best_w:
                    best_w, best = w, (i, j)
        if best is None:
            break
        i, j = best
        shards[i] |= shards[j]
        sizes[i] += sizes[j]
        del shards[j]
        del sizes[j]

    # Final consolidation: first-fit pack any shards that still fit together.
    # Merging zero-coupling shards (e.g. isolated leaf modules) costs no cut
    # weight and avoids spawning a PyCG process per trivial file.  Packing
    # never severs an edge, so it is strictly safe; the budget still bounds
    # per-shard PyCG divergence risk.
    units = [(frozenset(s), sz) for s, sz in zip(shards, sizes)]
    return _greedy_bin_pack(units, budget)


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------

def plan_shards(
    symbol_table: Dict[str, PyModule],
    jedi_edges: List[PyCallEdge],
    budget: int = 100,
    seed: int = 42,
    merge_small: bool = True,
) -> ShardPlan:
    """Partition a project into coupling-aware PyCG shards.

    Args:
        symbol_table: The level-1 symbol table (``file_path -> PyModule``).
        jedi_edges: Jedi-provenance call edges from the level-1 call graph.
        budget: Maximum number of files per shard.
        seed: Determinism seed for Louvain.
        merge_small: Agglomeratively merge under-budget shards to cut shard
            count and recover edges.

    Returns:
        A :class:`ShardPlan`.  Shards never silently drop files: every project
        module appears in exactly one shard (an atomic SCC larger than *budget*
        yields a single oversized shard, flagged in ``metrics``).
    """
    g = build_module_graph(symbol_table, jedi_edges)
    total_weight = float(sum(w for _, _, w in g.edges(data="weight", default=1)))

    # SCC condensation: each strongly-connected component is one atomic unit.
    condensation = nx.condensation(g)  # DAG; node attr 'members' = set of modules
    mapping: Dict[str, int] = condensation.graph["mapping"]  # module -> scc id
    unit_members: Dict[int, Set[str]] = {
        scc: set(condensation.nodes[scc]["members"]) for scc in condensation.nodes
    }
    unit_size: Dict[int, int] = {scc: len(m) for scc, m in unit_members.items()}

    # Weighted undirected graph over SCC units.
    hu = nx.Graph()
    hu.add_nodes_from(condensation.nodes())
    for u, v, w in g.edges(data="weight", default=1):
        su, sv = mapping[u], mapping[v]
        if su == sv:
            continue
        if hu.has_edge(su, sv):
            hu[su][sv]["weight"] += w
        else:
            hu.add_edge(su, sv, weight=w)

    # Community detection over units, then budget enforcement.
    communities = _communities(hu, resolution=1.0, seed=seed)
    unit_shards: List[Set[str]] = []  # sets of SCC ids
    for community in communities:
        unit_shards.extend(
            _split_oversized(hu, community, unit_size, budget, seed)
        )

    if merge_small:
        unit_shards = _merge_small(unit_shards, hu, unit_size, budget)

    # Expand SCC units back to file paths (graph nodes are files).
    file_shards: List[List[str]] = []
    for units in unit_shards:
        files: Set[str] = set()
        for scc in units:
            files |= unit_members[scc]
        if files:
            file_shards.append(sorted(files))

    # Parallel view in module names (file stems) for human-readable reporting.
    module_shards = [
        [g.nodes[f].get("module_name", f) for f in files] for files in file_shards
    ]

    # Metrics: how much Jedi edge weight does this partition sever?
    shard_of: Dict[str, int] = {}
    for idx, files in enumerate(file_shards):
        for f in files:
            shard_of[f] = idx
    cut_weight = 0.0
    for u, v, w in g.edges(data="weight", default=1):
        if shard_of.get(u) != shard_of.get(v):
            cut_weight += w

    sizes = [len(s) for s in module_shards] or [0]
    metrics = {
        "modules": float(g.number_of_nodes()),
        "module_edges": float(g.number_of_edges()),
        "total_edge_weight": total_weight,
        "cut_weight": cut_weight,
        "cut_ratio": (cut_weight / total_weight) if total_weight else 0.0,
        "num_shards": float(len(module_shards)),
        "max_shard_files": float(max(sizes)),
        "oversized_shards": float(sum(1 for s in sizes if s > budget)),
    }

    plan = ShardPlan(shards=file_shards, module_shards=module_shards, metrics=metrics)
    logger.info("Shard planner: %s", plan)
    return plan
