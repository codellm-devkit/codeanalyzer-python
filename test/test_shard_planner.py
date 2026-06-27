"""Unit tests for the Jedi-driven PyCG shard planner.

These exercise the pure partitioning logic (no PyCG required): module-graph
construction, SCC atomicity, budget enforcement, and the cut-ratio metric.
"""
from typing import List

import networkx as nx
import pytest

from codeanalyzer.schema.py_schema import PyCallEdge, PyCallable, PyClass, PyModule
from codeanalyzer.semantic_analysis.pycg.shard_planner import (
    build_module_graph,
    plan_shards,
)


# ----------------------------------------------------------------------------
# Builders
# ----------------------------------------------------------------------------

def _module(name: str, file_path: str, func_names: List[str]) -> PyModule:
    fns = {
        fn: PyCallable(signature=f"{name}.{fn}", name=fn, path=file_path)
        for fn in func_names
    }
    return PyModule(file_path=file_path, module_name=name, functions=fns)


def _edge(src: str, dst: str, w: int = 1) -> PyCallEdge:
    return PyCallEdge(source=src, target=dst, weight=w, provenance=["jedi"])


def _cut_ratio(g: nx.DiGraph, module_shards: List[List[str]]) -> float:
    shard_of = {m: i for i, mods in enumerate(module_shards) for m in mods}
    total = cut = 0.0
    for u, v, w in g.edges(data="weight", default=1):
        total += w
        if shard_of.get(u) != shard_of.get(v):
            cut += w
    return cut / total if total else 0.0


# ----------------------------------------------------------------------------
# build_module_graph
# ----------------------------------------------------------------------------

def test_module_graph_projects_callables_to_modules():
    st = {
        "/a.py": _module("a", "/a.py", ["f", "g"]),
        "/b.py": _module("b", "/b.py", ["h"]),
    }
    edges = [
        _edge("a.f", "b.h", 3),   # cross-module -> kept
        _edge("a.f", "a.g", 5),   # intra-module -> dropped
        _edge("a.g", "ext.lib.x"),  # external target -> dropped
    ]
    g = build_module_graph(st, edges)
    assert set(g.nodes) == {"a", "b"}
    assert g.has_edge("a", "b") and g["a"]["b"]["weight"] == 3
    assert not g.has_edge("a", "a")
    assert g.number_of_edges() == 1


def test_isolated_modules_are_nodes():
    st = {"/a.py": _module("a", "/a.py", ["f"]), "/b.py": _module("b", "/b.py", ["g"])}
    g = build_module_graph(st, [])  # no edges
    assert set(g.nodes) == {"a", "b"}
    assert g.number_of_edges() == 0


# ----------------------------------------------------------------------------
# plan_shards
# ----------------------------------------------------------------------------

def _coupled_clusters_project():
    """pkg_a <-> pkg_b heavy cross-package coupling + isolated leaves."""
    st, edges = {}, []
    for i in range(4):
        st[f"/pkg_a/m{i}.py"] = _module(f"pkg_a.m{i}", f"/pkg_a/m{i}.py", ["f"])
        st[f"/pkg_b/m{i}.py"] = _module(f"pkg_b.m{i}", f"/pkg_b/m{i}.py", ["g"])
    for i in range(4):
        edges.append(_edge(f"pkg_a.m{i}.f", f"pkg_b.m{i}.g", 10))
        edges.append(_edge(f"pkg_b.m{i}.g", f"pkg_a.m{i}.f", 8))
    for i in range(3):
        edges.append(_edge(f"pkg_a.m{i}.f", f"pkg_a.m{i+1}.f", 2))
    return st, edges


def test_budget_is_respected():
    st, edges = _coupled_clusters_project()
    plan = plan_shards(st, edges, budget=4)
    assert plan.metrics["max_shard_files"] <= 4
    assert plan.metrics["oversized_shards"] == 0


def test_every_module_assigned_exactly_once():
    st, edges = _coupled_clusters_project()
    plan = plan_shards(st, edges, budget=4)
    assigned = [m for shard in plan.module_shards for m in shard]
    assert sorted(assigned) == sorted(m.module_name for m in st.values())
    assert len(assigned) == len(set(assigned))  # no module duplicated


def test_beats_naive_per_package_cut_ratio():
    st, edges = _coupled_clusters_project()
    g = build_module_graph(st, edges)
    naive = {}
    for m in st.values():
        naive.setdefault(m.module_name.split(".")[0], []).append(m.module_name)
    naive_ratio = _cut_ratio(g, list(naive.values()))

    plan = plan_shards(st, edges, budget=4)
    assert plan.metrics["cut_ratio"] < naive_ratio


def test_import_cycle_is_never_split():
    # util.x <-> helpers.y form a cross-package cycle; must co-locate even
    # though they live in different top-level packages.
    st = {
        "/util/x.py": _module("util.x", "/util/x.py", ["a"]),
        "/helpers/y.py": _module("helpers.y", "/helpers/y.py", ["b"]),
    }
    edges = [_edge("util.x.a", "helpers.y.b", 5), _edge("helpers.y.b", "util.x.a", 5)]
    plan = plan_shards(st, edges, budget=10)
    shard_with_util = next(s for s in plan.module_shards if "util.x" in s)
    assert "helpers.y" in shard_with_util


def test_oversized_atomic_cycle_is_flagged_not_dropped():
    # A single import cycle of 6 modules with a budget of 3 cannot be split
    # without breaking edges; it must survive as one oversized shard.
    st, edges = {}, []
    names = [f"cyc.m{i}" for i in range(6)]
    for i, n in enumerate(names):
        st[f"/cyc/m{i}.py"] = _module(n, f"/cyc/m{i}.py", ["f"])
    for i in range(6):  # ring -> one big SCC
        edges.append(_edge(f"{names[i]}.f", f"{names[(i + 1) % 6]}.f", 1))
    plan = plan_shards(st, edges, budget=3)
    assert plan.metrics["oversized_shards"] >= 1
    assigned = [m for shard in plan.module_shards for m in shard]
    assert sorted(assigned) == sorted(names)  # nothing dropped


def test_determinism():
    st, edges = _coupled_clusters_project()
    a = plan_shards(st, edges, budget=4)
    b = plan_shards(st, edges, budget=4)
    norm = lambda p: sorted(sorted(s) for s in p.module_shards)
    assert norm(a) == norm(b)


def test_empty_project():
    plan = plan_shards({}, [], budget=4)
    assert plan.shards == []
    assert plan.metrics["cut_ratio"] == 0.0
