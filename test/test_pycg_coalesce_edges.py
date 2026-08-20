"""`_coalesce_edges` sums duplicate shard edges instead of raising (#133).

It built the merged edge with `source=`/`target=` and read `existing.source`,
but `PyCallEdge` declares `src`/`dst` with no aliases — so the duplicate branch,
the only branch that does any work, raised `AttributeError`. Latent because it
fires only when two shards report the same pair, which is the path large
projects take.
"""
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.semantic_analysis.pycg.pycg_analysis import PyCG


def test_duplicate_pair_sums_weight_instead_of_raising():
    edges = [
        PyCallEdge(src="a.f", dst="b.g", weight=3, prov=["pycg"]),
        PyCallEdge(src="a.f", dst="b.g", weight=2, prov=["pycg"]),
    ]
    (merged,) = PyCG._coalesce_edges(edges)
    assert (merged.src, merged.dst) == ("a.f", "b.g")
    assert merged.weight == 5


def test_distinct_pairs_are_left_alone():
    edges = [
        PyCallEdge(src="a.f", dst="b.g", weight=1, prov=["pycg"]),
        PyCallEdge(src="a.f", dst="c.h", weight=1, prov=["pycg"]),
    ]
    out = PyCG._coalesce_edges(edges)
    assert {(e.src, e.dst) for e in out} == {("a.f", "b.g"), ("a.f", "c.h")}


def test_provenance_unions_matching_merge_edges():
    """`call_graph.merge_edges` unions prov; this must not silently differ."""
    edges = [
        PyCallEdge(src="a.f", dst="b.g", weight=1, prov=["pycg"]),
        PyCallEdge(src="a.f", dst="b.g", weight=1, prov=["jedi"]),
    ]
    (merged,) = PyCG._coalesce_edges(edges)
    assert merged.prov == ["jedi", "pycg"]


def test_inputs_are_not_mutated():
    first = PyCallEdge(src="a.f", dst="b.g", weight=1, prov=["pycg"])
    PyCG._coalesce_edges([first, PyCallEdge(src="a.f", dst="b.g", weight=4, prov=["pycg"])])
    assert first.weight == 1
