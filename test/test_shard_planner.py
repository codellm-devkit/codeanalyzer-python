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


def _cut_ratio(g: nx.DiGraph, file_shards: List[List[str]]) -> float:
    # Graph nodes are file paths; partitions are lists of file paths.
    shard_of = {f: i for i, files in enumerate(file_shards) for f in files}
    total = cut = 0.0
    for u, v, w in g.edges(data="weight", default=1):
        total += w
        if shard_of.get(u) != shard_of.get(v):
            cut += w
    return cut / total if total else 0.0


# ----------------------------------------------------------------------------
# build_module_graph
# ----------------------------------------------------------------------------

def test_module_graph_keys_nodes_by_file_not_name():
    # Two distinct files share the stem "models" (module_name collision); the
    # graph must keep them as separate nodes keyed by path, not collapse them.
    st = {
        "/pkg_a/models.py": _module("models", "/pkg_a/models.py", ["f"]),
        "/pkg_b/models.py": _module("models", "/pkg_b/models.py", ["h"]),
    }
    edges = [
        _edge("models.f", "models.h", 3),  # but which file? mapped by definition
    ]
    g = build_module_graph(st, edges)
    assert set(g.nodes) == {"/pkg_a/models.py", "/pkg_b/models.py"}
    assert g.nodes["/pkg_a/models.py"]["module_name"] == "models"


def test_module_graph_projects_callables_to_files():
    st = {
        "/a.py": _module("a", "/a.py", ["f", "g"]),
        "/b.py": _module("b", "/b.py", ["h"]),
    }
    edges = [
        _edge("a.f", "b.h", 3),   # cross-file -> kept
        _edge("a.f", "a.g", 5),   # intra-file -> dropped
        _edge("a.g", "ext.lib.x"),  # external target -> dropped
    ]
    g = build_module_graph(st, edges)
    assert set(g.nodes) == {"/a.py", "/b.py"}
    assert g.has_edge("/a.py", "/b.py") and g["/a.py"]["/b.py"]["weight"] == 3
    assert g.number_of_edges() == 1


def test_isolated_files_are_nodes():
    st = {"/a.py": _module("a", "/a.py", ["f"]), "/b.py": _module("b", "/b.py", ["g"])}
    g = build_module_graph(st, [])  # no edges
    assert set(g.nodes) == {"/a.py", "/b.py"}
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
    # Naive baseline: one shard per top-level directory (e.g. /pkg_a/...).
    naive = {}
    for m in st.values():
        top = m.file_path.split("/")[1]
        naive.setdefault(top, []).append(m.file_path)
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


def test_no_file_dropped_on_stem_collision():
    # Regression: module_name is only the file stem, so many files collide on
    # name (every __init__.py, models.py, ...). Keying by name would drop all
    # but one per stem. Every FILE must land in exactly one shard.
    st, edges = {}, []
    for pkg in ("a", "b", "c", "d"):
        for stem in ("__init__", "models", "views"):
            path = f"/{pkg}/{stem}.py"
            st[path] = _module(stem, path, ["f"])
    plan = plan_shards(st, edges, budget=5)
    assigned = sorted(f for shard in plan.shards for f in shard)
    assert assigned == sorted(st.keys())          # all 12 files present
    assert len(assigned) == len(set(assigned))    # none duplicated


def test_empty_project():
    plan = plan_shards({}, [], budget=4)
    assert plan.shards == []
    assert plan.metrics["cut_ratio"] == 0.0
