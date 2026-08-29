"""Task 6: artifact/dependency rows in the Neo4j projection."""
from codeanalyzer.neo4j.schema import NODE_LABELS, REL_TYPES


def test_catalog_has_neutral_vocabulary():
    labels = {n.label: n for n in NODE_LABELS}
    assert labels["Artifact"].key == "id" and labels["Package"].key == "id"
    assert labels["Artifact"].properties["text_truncated"] == "boolean"
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
    pyproject_node = next(
        n for n in rows.nodes
        if n.labels[0] == "Artifact" and n.value == "can://artifact/p/pyproject.toml"
    )
    assert pyproject_node.props["text_truncated"] is False
    rel_types = {e.type for e in rows.edges}
    assert {"HAS_ARTIFACT", "DECLARES_DEPENDENCY", "PY_PROVIDES"} <= rel_types


def test_locks_and_provides_dedup_across_multi_manifest_declarations(tmp_path):
    """A package declared in 2+ manifests yields one PyDependency record per
    manifest (Task 5's `build_dependency_view`), so DECLARES_DEPENDENCY --
    correctly -- fires once per manifest. LOCKS/PY_PROVIDES are per-PACKAGE
    facts, not per-declaration, and must not duplicate just because the
    package happens to be declared twice."""
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "requirements.txt").write_text("requests==2.31.0\n")
    (proj / "requirements-dev.txt").write_text("requests==2.31.0\n")
    (proj / "poetry.lock").write_text('[[package]]\nname = "requests"\nversion = "2.31.0"\n')
    (proj / "app.py").write_text("import requests\n")
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions
    app = Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=2, no_venv=True, cache_dir=tmp_path / "c",
    )).analyze().application
    assert len(app.dependencies) == 2, "fixture must produce two declarations of one package"

    from codeanalyzer.neo4j.project import project
    from codeanalyzer.schema.assign_ids import assign_ids
    rows = project(app, "p", assign_ids(app, "p"))

    locks = [e for e in rows.edges if e.type == "LOCKS"]
    provides = [e for e in rows.edges if e.type == "PY_PROVIDES"]
    declares = [e for e in rows.edges if e.type == "DECLARES_DEPENDENCY"]

    assert len(locks) == 1, f"expected exactly one LOCKS row, got {len(locks)}"
    assert locks[0].from_ref.value == "can://artifact/p/poetry.lock"
    assert locks[0].to_ref.value == "pkg:pypi/requests"

    assert len(provides) == 1, f"expected exactly one PY_PROVIDES row, got {len(provides)}"
    assert provides[0].from_ref.value == "pkg:pypi/requests"

    assert len(declares) == 2, "one DECLARES_DEPENDENCY row per declaring manifest"


def test_declares_dependency_distinct_by_kind_within_one_manifest(tmp_path):
    """One manifest re-declaring the same package under two kinds (e.g.
    requests in [project.dependencies] AND again under
    [project.optional-dependencies]) yields two PyDependency records with
    identical DECLARES_DEPENDENCY endpoints (same manifest, same package) and
    no discriminant -- without `key=d.kind` the Cypher/Bolt MERGE collapses
    them into one relationship."""
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests"]\n'
        '[project.optional-dependencies]\nextra = ["requests"]\n'
    )
    (proj / "app.py").write_text("import requests\n")
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions
    app = Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=2, no_venv=True, cache_dir=tmp_path / "c",
    )).analyze().application
    assert len(app.dependencies) == 2, "fixture must produce two declarations of one package"

    from codeanalyzer.neo4j.project import project
    from codeanalyzer.schema.assign_ids import assign_ids
    rows = project(app, "p", assign_ids(app, "p"))

    declares = [e for e in rows.edges if e.type == "DECLARES_DEPENDENCY"]
    assert len(declares) == 2, "one DECLARES_DEPENDENCY row per declaration, not collapsed"
    keys = {e.key for e in declares}
    assert keys == {"runtime", "optional"}, f"expected two distinct kind keys, got {keys}"
