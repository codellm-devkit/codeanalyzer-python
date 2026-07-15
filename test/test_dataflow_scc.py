"""Stage-5 gate: SCC condensation of the call-graph oracle."""

from codeanalyzer.dataflow.scc import strongly_connected_components


def test_mutual_recursion_forms_one_scc():
    nodes = ["main", "even", "odd", "leaf"]
    edges = [("main", "even"), ("even", "odd"), ("odd", "even"), ("even", "leaf")]
    sccs = strongly_connected_components(nodes, edges)
    assert ["even", "odd"] in sccs
    assert ["leaf"] in sccs and ["main"] in sccs


def test_reverse_topological_order_callees_first():
    nodes = ["a", "b", "c"]
    edges = [("a", "b"), ("b", "c")]
    sccs = strongly_connected_components(nodes, edges)
    pos = {tuple(s): i for i, s in enumerate(sccs)}
    assert pos[("c",)] < pos[("b",)] < pos[("a",)]


def test_deterministic_across_runs():
    nodes = ["m", "x", "y", "z"]
    edges = [("m", "x"), ("x", "y"), ("y", "x"), ("y", "z"), ("z", "y")]
    assert strongly_connected_components(nodes, edges) == strongly_connected_components(
        nodes, edges
    )
    # x-y-z all collapse into one SCC (x↔y, y↔z), members sorted.
    sccs = strongly_connected_components(nodes, edges)
    assert ["x", "y", "z"] in sccs
