"""Task 1: artifact/dependency schema models and id constructors."""
from codeanalyzer.schema import model_validate_json, model_dump_json
from codeanalyzer.schema.ids import artifact_id, purl_pypi
from codeanalyzer.schema.py_schema import (
    PyApplication, PyArtifact, PyDependency, PyImportBinding,
)


def test_artifact_id_is_language_neutral():
    assert artifact_id("myapp", "deploy/docker-compose.yml") == \
        "can://artifact/myapp/deploy/docker-compose.yml"


def test_purl_pypi():
    assert purl_pypi("pyyaml") == "pkg:pypi/pyyaml"


def test_models_round_trip():
    art = PyArtifact(
        id=artifact_id("a", "pyproject.toml"), path="pyproject.toml",
        format="toml", roles=["dependency-manifest"], size_bytes=10,
        sha256="ab" * 32, source="[project]\n",
    )
    dep = PyDependency(
        name="requests", spec=">=2.31", kind="runtime",
        declared_in=art.id, provides_imports=["requests"], prov=["declared"],
    )
    imp = PyImportBinding(module="yaml", bound_to="pyyaml", prov=["heuristic"])
    app = PyApplication.builder().symbol_table({}).call_graph([]).build()
    app.artifacts = {art.path: art}
    app.dependencies = [dep]
    app.unresolved_imports = [imp]
    back = model_validate_json(PyApplication, model_dump_json(app))
    assert back.artifacts["pyproject.toml"].kind == "artifact"
    assert back.artifacts["pyproject.toml"].extraction == "none"
    assert back.artifacts["pyproject.toml"].text_truncated is False
    assert back.dependencies[0].locked_version is None
    assert back.unresolved_imports[0].bound_to == "pyyaml"


def test_defaults_empty_on_old_payload():
    app = PyApplication.builder().symbol_table({}).call_graph([]).build()
    back = model_validate_json(PyApplication, model_dump_json(app))
    assert back.artifacts == {} and back.dependencies == [] and back.unresolved_imports == []
