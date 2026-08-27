"""Task 6: artifact/dependency rows in the Neo4j projection."""
from codeanalyzer.neo4j.schema import NODE_LABELS, REL_TYPES


def test_catalog_has_neutral_vocabulary():
    labels = {n.label: n for n in NODE_LABELS}
    assert labels["Artifact"].key == "id" and labels["Package"].key == "id"
    rels = {r.type for r in REL_TYPES}
    assert {"HAS_ARTIFACT", "DECLARES_DEPENDENCY", "LOCKS",
            "PY_PROVIDES", "PY_UNRESOLVED_IMPORT"} <= rels


def test_rows_projected(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\ndependencies = ["requests"]\n')
    (proj / "app.py").write_text("import requests\nimport colorama\nrequests.get('u')\n")
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions
    app = Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=2, no_venv=True, cache_dir=tmp_path / "c",
    )).analyze().application

    from codeanalyzer.neo4j.project import project
    from codeanalyzer.schema.assign_ids import assign_ids
    rows = project(app, "p", assign_ids(app, "p"))

    nodes = {(n.labels[0], n.value) for n in rows.nodes}
    assert ("Artifact", "can://artifact/p/pyproject.toml") in nodes
    assert ("Package", "pkg:pypi/requests") in nodes
    rel_types = {e.type for e in rows.edges}
    assert {"HAS_ARTIFACT", "DECLARES_DEPENDENCY", "PY_PROVIDES"} <= rel_types
