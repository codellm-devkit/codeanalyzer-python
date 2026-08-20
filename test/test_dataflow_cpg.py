"""Stage-3 (v2) gate: the CPG overlay projection of the level-3 graphs.

Projected off each callable's v2 ``body``/``cfg``/``cdg``/``ddg`` (populated by
``emit_l3_body`` at ``-a 3``), the Neo4j overlay must satisfy:

- ``PyBodyNode`` row count equals the total number of ``body`` nodes across all
  callables — the count-parity / two-projection-agreement assertion;
- every ``PY_CFG_NEXT``/``PY_CDG``/``PY_DDG`` edge endpoint that is a PyBodyNode
  references an emitted PyBodyNode id (the no-dangling gate);
- every emitted PyBodyNode is owned by its callable via ``PY_HAS_BODY_NODE``;
- the Cypher snapshot renders and contains the overlay's vocabulary.

Parameter/summary edges (``PY_PARAM_IN``/``PY_PARAM_OUT``/``PY_SUMMARY``) are an
L4/SDG concern and are intentionally absent here. Loading into a live Neo4j is
exercised by the (container-gated) bolt tests; these stay fast and deterministic.
"""
import pytest

from codeanalyzer.neo4j import project
from codeanalyzer.neo4j.cypher import render_cypher
from codeanalyzer.semantic_analysis.call_graph import iter_callables_in_symbol_table

from sample_graph_app import make_sample_app

CPG_EDGE_TYPES = {"PY_CFG_NEXT", "PY_CDG", "PY_DDG"}


@pytest.fixture(scope="module")
def sample():
    return make_sample_app()  # (app, sig_to_id)


@pytest.fixture(scope="module")
def rows(sample):
    app, sig_to_id = sample
    return project(app, "dataflow-fixture", sig_to_id)


def test_cfg_node_count_matches_the_body_section(sample, rows):
    app, _sig_to_id = sample
    expected = sum(
        len(c.body or {}) for c in iter_callables_in_symbol_table(app.symbol_table)
    )
    emitted = [n for n in rows.nodes if "PyBodyNode" in n.labels]
    assert expected > 0
    assert len(emitted) == expected


def test_no_dangling_cpg_edge_endpoints(rows):
    cfg_ids = {n.value for n in rows.nodes if "PyBodyNode" in n.labels}
    cpg_edges = [e for e in rows.edges if e.type in CPG_EDGE_TYPES]
    assert cpg_edges, "no CPG edges projected"
    for e in cpg_edges:
        if e.from_ref.label == "PyBodyNode":
            assert e.from_ref.value in cfg_ids, e
        if e.to_ref.label == "PyBodyNode":
            assert e.to_ref.value in cfg_ids, e


def test_every_callable_with_graphs_owns_its_cfg_nodes(rows):
    has_edges = [e for e in rows.edges if e.type == "PY_HAS_BODY_NODE"]
    owned = {e.to_ref.value for e in has_edges}
    cfg_ids = {n.value for n in rows.nodes if "PyBodyNode" in n.labels}
    assert owned == cfg_ids, "every CFGNode must be owned by its callable"


def test_cypher_snapshot_renders_the_overlay(rows):
    cypher = render_cypher(rows, "dataflow-fixture")
    assert ":PyBodyNode" in cypher
    for t in CPG_EDGE_TYPES:
        assert t in cypher, f"{t} missing from the snapshot"
