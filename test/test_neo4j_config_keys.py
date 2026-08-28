"""Task 4: neutral ConfigKey nodes via DEFINES_CONFIG in the Neo4j projection."""
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.neo4j.project import project
from codeanalyzer.neo4j.schema import CONSTRAINTS, NODE_LABELS, REL_TYPES
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema.assign_ids import assign_ids


def test_catalog_has_config_key_label_and_rel():
    labels = {n.label: n for n in NODE_LABELS}
    config_key = labels["ConfigKey"]
    assert config_key.key == "id" and config_key.merge_label == "ConfigKey"
    assert config_key.properties == {
        "id": "string", "key": "string", "namespace": "string",
        "value": "string", "references": "string[]",
        "start_line": "integer", "end_line": "integer",
    }
    rels = {r.type: r for r in REL_TYPES}
    defines = rels["DEFINES_CONFIG"]
    assert defines.from_labels == ["Artifact"] and defines.to_labels == ["ConfigKey"]


def test_constraint_present_for_config_key():
    assert (
        "CREATE CONSTRAINT configkey_id IF NOT EXISTS FOR (x:ConfigKey) "
        "REQUIRE x.id IS UNIQUE"
    ) in CONSTRAINTS


def _analyze(proj, tmp_path, **opts):
    return Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=1, no_venv=True, cache_dir=tmp_path / "c", **opts,
    )).analyze().application


def test_config_key_node_and_edge_projected(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests"]\n\n[tool.demo]\nkey = "val"\n'
    )
    app = _analyze(proj, tmp_path)
    rows = project(app, "p", assign_ids(app, "p"))

    manifest_id = app.artifacts["pyproject.toml"].id
    ck = next(
        k for k in app.artifacts["pyproject.toml"].config_keys if k.key == "tool.demo.key"
    )

    node = next(n for n in rows.nodes if n.labels[0] == "ConfigKey" and n.value == ck.id)
    assert node.props["key"] == "tool.demo.key"
    assert node.props["namespace"] == "toml"
    assert node.props["value"] == "val"
    assert node.props["references"] == []  # kept (present-but-empty), not pruned

    edge = next(
        e for e in rows.edges if e.type == "DEFINES_CONFIG" and e.to_ref.value == ck.id
    )
    assert edge.from_ref.label == "Artifact" and edge.from_ref.value == manifest_id
    assert edge.to_ref.label == "ConfigKey"


def test_config_key_value_omitted_when_model_value_none(tmp_path):
    """spec 2026-08-28 constraint 2: --no-artifact-text drops values (and
    source) together -- keys/namespace/span/references are still extracted
    from the real on-disk text either way."""
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[tool.demo]\nkey = "val"\n')
    app = _analyze(proj, tmp_path, artifact_text=False)
    ck = app.artifacts["pyproject.toml"].config_keys[0]
    assert ck.key == "tool.demo.key" and ck.value is None  # sanity: still extracted

    rows = project(app, "p", assign_ids(app, "p"))
    node = next(n for n in rows.nodes if n.labels[0] == "ConfigKey" and n.value == ck.id)
    assert "value" not in node.props


def test_config_key_references_and_span_projected(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / ".env").write_text("DATABASE_URL=${DB_HOST}\n")
    app = _analyze(proj, tmp_path)
    ck = app.artifacts[".env"].config_keys[0]
    assert ck.references == ["${DB_HOST}"]

    rows = project(app, "p", assign_ids(app, "p"))
    node = next(n for n in rows.nodes if n.labels[0] == "ConfigKey" and n.value == ck.id)
    assert node.props["references"] == ["${DB_HOST}"]
    assert node.props["start_line"] == 1 and node.props["end_line"] == 1
