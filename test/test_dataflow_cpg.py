"""Stage-8b gate: the CPG projection of the level-3 graphs.

- CFGNode row count equals the JSON section's node count (CFG + parameter
  nodes) — the contract's count-parity assertion;
- every CFG_NEXT/CDG/DDG/PARAM_IN/PARAM_OUT/SUMMARY edge endpoint references
  an emitted CFGNode id (deferred-edge/no-dangling gate);
- the Cypher snapshot renders and contains the overlay's vocabulary.

Loading into a live Neo4j is exercised by the (container-gated) bolt tests;
these stay fast and deterministic.
"""

from pathlib import Path

import pytest

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.neo4j import project
from codeanalyzer.neo4j.cypher import render_cypher
from codeanalyzer.options import AnalysisOptions

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "dataflow"

CPG_EDGE_TYPES = {"CFG_NEXT", "CDG", "DDG", "PARAM_IN", "PARAM_OUT", "SUMMARY"}


@pytest.fixture(scope="module")
def level3_app(tmp_path_factory):
    cache = tmp_path_factory.mktemp("dataflow-cpg-cache")
    options = AnalysisOptions(
        input=FIXTURE, analysis_level=3, no_venv=True, cache_dir=cache
    )
    with Codeanalyzer(options) as analyzer:
        return analyzer.analyze()


@pytest.fixture(scope="module")
def rows(level3_app):
    return project(level3_app, "dataflow-fixture")


def test_cfg_node_count_matches_the_json_section(level3_app, rows):
    expected = sum(
        len(fg.cfg.nodes if fg.cfg else []) + len(fg.param_nodes or [])
        for fg in level3_app.program_graphs.functions.values()
    )
    emitted = [n for n in rows.nodes if "CFGNode" in n.labels]
    assert expected > 0
    assert len(emitted) == expected


def test_no_dangling_cpg_edge_endpoints(rows):
    cfg_ids = {n.value for n in rows.nodes if "CFGNode" in n.labels}
    cpg_edges = [e for e in rows.edges if e.type in CPG_EDGE_TYPES]
    assert cpg_edges, "no CPG edges projected"
    for e in cpg_edges:
        if e.from_ref.label == "CFGNode":
            assert e.from_ref.value in cfg_ids, e
        if e.to_ref.label == "CFGNode":
            assert e.to_ref.value in cfg_ids, e


def test_every_callable_with_graphs_owns_its_cfg_nodes(level3_app, rows):
    has_edges = [e for e in rows.edges if e.type == "HAS_CFG_NODE"]
    owned = {e.to_ref.value for e in has_edges}
    cfg_ids = {n.value for n in rows.nodes if "CFGNode" in n.labels}
    assert owned == cfg_ids, "every CFGNode must be owned by its callable"


def test_cypher_snapshot_renders_the_overlay(level3_app, rows):
    cypher = render_cypher(rows, "dataflow-fixture")
    assert ":CFGNode" in cypher
    for t in CPG_EDGE_TYPES:
        assert t in cypher, f"{t} missing from the snapshot"
