"""Task 4 (#162): PY_USES_CONFIG and PY_READS_CONFIG_UNRESOLVED in the Neo4j
projection. Mirrors test_neo4j_config_keys.py's idioms: a catalog assertion
first, then small real-pipeline projects run through `project()`.

Every functional test analyzes at `-a 2` -- the literal tier's minimum level
-- deliberately, not `-a 4`. A `config_uses`/`config_reads_unresolved` entry's
`src`/`site` names a call site's body node, and call nodes enter `body` at L1
(before any config_use tier runs) -- so the src is already a projected
:PyBodyNode row even at the lowest level config_use can produce an edge,
independent of the separate fact that the real CLI always forces `-a 4` for
`--emit neo4j`. Running these tests at `-a 2` proves the no-dangling property
holds on its own merits rather than riding on that CLI floor.
"""
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.neo4j.project import project
from codeanalyzer.neo4j.schema import REL_TYPES
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema.assign_ids import assign_ids


def test_catalog_has_config_use_rels():
    rels = {r.type: r for r in REL_TYPES}
    uses = rels["PY_USES_CONFIG"]
    assert uses.from_labels == ["PyBodyNode"] and uses.to_labels == ["ConfigKey"]
    assert uses.properties == {"prov": "string[]"}

    unresolved = rels["PY_READS_CONFIG_UNRESOLVED"]
    assert unresolved.from_labels == ["PyApplication"] and unresolved.to_labels == ["PyExternal"]
    assert unresolved.properties == {
        "key": "string", "reason": "string", "prov": "string[]", "_k": "string",
    }


def _analyze(proj, tmp_path, **opts):
    return Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=2, no_venv=True, cache_dir=tmp_path / "c", **opts,
    )).analyze().application


def test_resolved_config_use_edge_projected_onto_existing_body_node(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "mod.py").write_text(
        "import os\n\ndef read_host():\n    return os.getenv('DB_HOST')\n"
    )
    (proj / ".env").write_text("DB_HOST=example.com\n")
    app = _analyze(proj, tmp_path)
    (edge,) = app.config_uses

    rows = project(app, "p", assign_ids(app, "p"))
    (row,) = [e for e in rows.edges if e.type == "PY_USES_CONFIG"]
    assert row.from_ref.label == "PyBodyNode" and row.from_ref.value == edge.src
    assert row.to_ref.label == "ConfigKey" and row.to_ref.value == edge.dst
    assert row.props == {"prov": ["literal"]}

    # the src id must resolve to an actual projected PyBodyNode row, not a
    # dangling reference off some other node's merge key.
    assert any(
        n.labels[0] == "PyBodyNode" and n.value == edge.src for n in rows.nodes
    ), "PY_USES_CONFIG.src does not match any projected PyBodyNode"


def test_undefined_key_read_projects_as_ghost_edge_with_key_and_reason(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "mod.py").write_text(
        "import os\n\ndef read_missing():\n    return os.getenv('MISSING')\n"
    )
    (proj / ".env").write_text("DB_HOST=example.com\n")
    app = _analyze(proj, tmp_path)
    (read,) = app.config_reads_unresolved

    rows = project(app, "p", assign_ids(app, "p"))
    (row,) = [e for e in rows.edges if e.type == "PY_READS_CONFIG_UNRESOLVED"]
    assert row.from_ref.label == "PyApplication" and row.from_ref.value == "p"
    assert row.to_ref.label == "PySymbol" and row.to_ref.value == read.callee
    assert row.props == {"key": "MISSING", "reason": "undefined-key", "prov": ["literal"]}
    assert row.key == "MISSING|undefined-key"

    assert any(
        "PyExternal" in n.labels and n.value == read.callee for n in rows.nodes
    ), "PY_READS_CONFIG_UNRESOLVED ghost target was never projected as :PyExternal"


def test_non_literal_read_omits_key_prop(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "mod.py").write_text(
        "import os\n\ndef read_dynamic(kvar):\n    return os.getenv(kvar)\n"
    )
    app = _analyze(proj, tmp_path)
    rows = project(app, "p", assign_ids(app, "p"))
    (row,) = [e for e in rows.edges if e.type == "PY_READS_CONFIG_UNRESOLVED"]
    assert "key" not in row.props
    assert row.props["reason"] == "non-literal"
    assert row.key == "|non-literal"


def test_two_unresolved_reads_same_callee_different_key_both_survive_merge(tmp_path):
    """Regression: without a relationship discriminant, two distinct
    undefined-key reads through the SAME external callee (`os.getenv`) would
    MERGE onto one relationship at load time and one key's finding would be
    silently overwritten on SET -- the same failure class PY_DDG's `_k` guards
    against for per-variable dependence edges."""
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "mod.py").write_text(
        "import os\n\n"
        "def read_one():\n    return os.getenv('MISSING_ONE')\n\n"
        "def read_two():\n    return os.getenv('MISSING_TWO')\n"
    )
    (proj / ".env").write_text("DB_HOST=example.com\n")
    app = _analyze(proj, tmp_path)
    assert len(app.config_reads_unresolved) == 2

    rows = project(app, "p", assign_ids(app, "p"))
    unresolved_edges = [e for e in rows.edges if e.type == "PY_READS_CONFIG_UNRESOLVED"]
    assert len(unresolved_edges) == 2
    assert {e.props["key"] for e in unresolved_edges} == {"MISSING_ONE", "MISSING_TWO"}
    assert {e.key for e in unresolved_edges} == {
        "MISSING_ONE|undefined-key", "MISSING_TWO|undefined-key",
    }
    # both hang off the SAME :PyExternal ghost (one os.getenv external symbol)
    assert len({e.to_ref.value for e in unresolved_edges}) == 1
