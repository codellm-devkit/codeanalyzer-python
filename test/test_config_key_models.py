"""Task 1: PyConfigKey model and config_key_id helper."""
from codeanalyzer.schema import model_validate_json, model_dump_json
from codeanalyzer.schema.ids import artifact_id, config_key_id
from codeanalyzer.schema.py_schema import PyApplication, PyArtifact, PyConfigKey, Span


def test_config_key_id_shape():
    assert config_key_id("can://artifact/a/pyproject.toml", "project.name") == \
        "can://artifact/a/pyproject.toml@key/project.name"


def test_config_key_id_numeric_array_segment():
    assert config_key_id("can://artifact/a/compose.yml", "services.web.ports.0") == \
        "can://artifact/a/compose.yml@key/services.web.ports.0"


def test_models_round_trip():
    art = PyArtifact(
        id=artifact_id("a", "config.yaml"), path="config.yaml",
        format="yaml", roles=["unknown"], size_bytes=10,
        sha256="ab" * 32, source="db:\n  host: localhost\n",
    )
    key = PyConfigKey(
        id=config_key_id(art.id, "db.host"), key="db.host", namespace="yaml",
        value="localhost",
        span=Span(start=(2, 7), end=(2, 16), bytes=(10, 19)),
        references=["${VAR}"],
    )
    art.config_keys = [key]
    app = PyApplication.builder().symbol_table({}).call_graph([]).build()
    app.artifacts = {art.path: art}
    back = model_validate_json(PyApplication, model_dump_json(app))
    ck = back.artifacts["config.yaml"].config_keys[0]
    assert ck.id == f"{art.id}@key/db.host"
    assert ck.key == "db.host"
    assert ck.namespace == "yaml"
    assert ck.value == "localhost"
    assert ck.span.start == (2, 7) and ck.span.end == (2, 16)
    assert ck.references == ["${VAR}"]


def test_defaults_empty_on_old_payload():
    """A PyArtifact payload written before #152 has no `config_keys` key at
    all -- must still validate, defaulting to an empty list."""
    art = PyArtifact(
        id=artifact_id("a", "x.txt"), path="x.txt", format="text",
        size_bytes=0, sha256="0" * 64,
    )
    app = PyApplication.builder().symbol_table({}).call_graph([]).build()
    app.artifacts = {art.path: art}
    back = model_validate_json(PyApplication, model_dump_json(app))
    assert back.artifacts["x.txt"].config_keys == []


def test_config_key_optional_fields_default():
    key = PyConfigKey(id="x@key/a", key="a", namespace="env")
    assert key.value is None
    assert key.span is None
    assert key.references == []
