"""Schema conformance test (no container needed). Projects the sample app and
asserts that the real emitter only ever produces node labels, relationship types
and properties that the catalog (``codeanalyzer/neo4j/catalog.py``) declares.
This is the anti-drift guard: if ``project.py`` grows a label or property that
``catalog.py`` doesn't declare, this fails — keeping the published
``schema.neo4j.json`` honest. It also checks the checked-in ``schema.neo4j.json``
is regenerated (run ``codeanalyzer --emit schema > schema.neo4j.json``).
"""
import json
from pathlib import Path

from codeanalyzer.neo4j import NODE_LABELS, REL_TYPES, build_schema_document, project
from codeanalyzer.neo4j.catalog import MARKER_LABELS
from codeanalyzer.neo4j.cypher import render_cypher

from sample_graph_app import make_sample_app

_BY_LABEL = {n.label: n for n in NODE_LABELS}
_MERGE_OF = {n.label: n.merge_label for n in NODE_LABELS}
_REL_BY_TYPE = {r.type: r for r in REL_TYPES}
_MARKERS = set(MARKER_LABELS)


def _specific_label(labels):
    """The specific (catalog) label for a node row: the non-merge, non-marker label."""
    merge = labels[0]
    if merge != "PySymbol":
        return merge
    for label in labels:
        if label != "PySymbol" and label not in _MARKERS:
            return label
    return "PySymbol"


def _merge_labels_for(specifics):
    return {_MERGE_OF[s] for s in specifics}


def test_every_emitted_node_label_and_property_is_declared():
    rows = project(make_sample_app(), "sample-app")
    assert rows.nodes, "projection produced no nodes"
    for node in rows.nodes:
        specific = _specific_label(node.labels)
        decl = _BY_LABEL.get(specific)
        assert decl is not None, f"undeclared node label: {':'.join(node.labels)}"
        assert node.labels[0] == decl.merge_label
        for label in node.labels:
            ok = label == decl.merge_label or label == specific or label in _MARKERS
            assert ok, f"unexpected label '{label}' on {specific}"
        for key in node.props:
            assert key in decl.properties, f"undeclared property '{specific}.{key}'"


def test_every_emitted_relationship_type_property_and_endpoint_is_declared():
    rows = project(make_sample_app(), "sample-app")
    assert rows.edges, "projection produced no edges"
    for edge in rows.edges:
        decl = _REL_BY_TYPE.get(edge.type)
        assert decl is not None, f"undeclared relationship type: {edge.type}"
        assert edge.from_ref.label in _merge_labels_for(decl.from_labels), (
            f"bad source {edge.from_ref.label} for {edge.type}"
        )
        assert edge.to_ref.label in _merge_labels_for(decl.to_labels), (
            f"bad target {edge.to_ref.label} for {edge.type}"
        )
        for key in edge.props:
            assert key in decl.properties, f"undeclared property on {edge.type}.{key}"


def test_all_catalog_node_kinds_and_relationships_are_exercised():
    """Guards the fixture itself: every catalog label/rel should appear at least
    once, so the conformance asserts above actually cover the whole schema."""
    rows = project(make_sample_app(), "sample-app")
    seen_labels = {_specific_label(n.labels) for n in rows.nodes}
    seen_rels = {e.type for e in rows.edges}
    assert {n.label for n in NODE_LABELS} <= seen_labels
    assert {r.type for r in REL_TYPES} <= seen_rels


def test_render_cypher_is_deterministic_and_self_contained():
    app = make_sample_app()
    a = render_cypher(project(app, "sample-app"), "sample-app")
    b = render_cypher(project(make_sample_app(), "sample-app"), "sample-app")
    assert a == b, "cypher rendering must be deterministic"
    assert "CREATE CONSTRAINT" in a
    assert "DETACH DELETE" in a
    assert "MERGE (n:PySymbol {signature: row.k})" in a


def test_checked_in_schema_matches_catalog():
    """Run `codeanalyzer --emit schema > schema.neo4j.json` if this fails."""
    on_disk_path = Path(__file__).resolve().parents[1] / "schema.neo4j.json"
    assert on_disk_path.exists(), "schema.neo4j.json is missing — regenerate it"
    on_disk = json.loads(on_disk_path.read_text())
    fresh = build_schema_document()
    assert on_disk == fresh
