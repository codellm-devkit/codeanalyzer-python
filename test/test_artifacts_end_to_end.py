"""Task 7: full pipeline over the manifests_app fixture + determinism."""
from pathlib import Path
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json

FIXTURE = Path(__file__).parent / "fixtures" / "whole_applications" / "manifests_app"


def _app(tmp_path, tag):
    return Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=tmp_path / tag,
    )).analyze().application


def test_full_surface(tmp_path):
    app = _app(tmp_path, "a")
    arts = app.artifacts
    # never-drop inventory (#157) makes the artifact set deterministic --
    # exact equality, not a subset check: every non-.py fixture file, and
    # nothing else, is inventoried (pkg/__init__.py, pkg/main.py excluded).
    assert set(arts) == {
        "pyproject.toml", "requirements-dev.txt", "setup.py", "uv.lock",
        "environment.yml", "Dockerfile", "docker-compose.yml",
        ".github/workflows/ci.yml", "data.csv", "logo.png",
        ".env", "config/settings.yml", "app.properties",
    }
    assert arts["setup.py"].extraction == "partial"      # computed install_requires
    assert arts["pyproject.toml"].extraction == "full"
    # never-drop inventory (#157 follow-up): unmatched files are captured too.
    assert arts["data.csv"].format == "text" and arts["data.csv"].roles == ["unknown"]
    assert arts["logo.png"].format == "binary" and arts["logo.png"].roles == ["unknown"]
    assert arts["logo.png"].source == "" and arts["logo.png"].sha256 != ""
    deps = {d.name: d for d in app.dependencies}
    assert deps["requests"].locked_version == "2.32.3"
    assert deps["requests"].prov == ["declared", "lockfile"]
    assert deps["pytest"].kind == "dev"
    assert deps["numpy"].spec == "=1.26"
    # setup.py is repo code to the symbol table (statically parsed, never
    # executed); its setuptools import is undeclared here by design.
    assert [u.module for u in app.unresolved_imports] == ["colorama", "setuptools"]


def test_determinism_two_runs(tmp_path):
    a = model_dump_json(_app(tmp_path, "r1"))
    b = model_dump_json(_app(tmp_path, "r2"))
    assert a == b


def test_level_invariance_artifacts_and_deps(tmp_path):
    """Artifacts, dependencies, and unresolved_imports must be identical at L1 and L4."""
    app_l1 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=tmp_path / "l1",
    )).analyze().application
    app_l4 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=4, no_venv=True, cache_dir=tmp_path / "l4",
    )).analyze().application

    # artifacts section
    assert set(app_l1.artifacts.keys()) == set(app_l4.artifacts.keys())
    for name in app_l1.artifacts:
        assert app_l1.artifacts[name] == app_l4.artifacts[name]

    # dependencies section
    deps_l1 = {d.name: d for d in app_l1.dependencies}
    deps_l4 = {d.name: d for d in app_l4.dependencies}
    assert set(deps_l1.keys()) == set(deps_l4.keys())
    for name in deps_l1:
        assert deps_l1[name] == deps_l4[name]

    # unresolved_imports section
    unres_l1 = sorted([u.module for u in app_l1.unresolved_imports])
    unres_l4 = sorted([u.module for u in app_l4.unresolved_imports])
    assert unres_l1 == unres_l4


def test_config_keys_extraction(tmp_path):
    """Verify config key extraction from .env, settings.yml, and app.properties."""
    app = _app(tmp_path, "config_test")
    arts = app.artifacts

    # .env: env namespace, exact keys
    env_keys = {k.key for k in arts[".env"].config_keys}
    assert env_keys == {"DEBUG", "DATABASE_URL", "API_KEY", "SECRET_API_TOKEN", "FLASK_ENV"}

    # All .env keys should be in env namespace
    for key in arts[".env"].config_keys:
        assert key.namespace == "env"

    # settings.yml: yaml namespace, nested + array keys with numeric indices
    yaml_keys = {k.key for k in arts["config/settings.yml"].config_keys}
    assert yaml_keys == {
        "server.host", "server.port", "server.timeouts.connect", "server.timeouts.read",
        "database.primary.url", "database.primary.pool_size",
        "database.replicas.0.url", "database.replicas.1.url",
        "logging.level", "logging.handlers.0", "logging.handlers.1",
    }

    # All yaml keys should be in yaml namespace
    for key in arts["config/settings.yml"].config_keys:
        assert key.namespace == "yaml"

    # app.properties: properties namespace
    props_keys = {k.key for k in arts["app.properties"].config_keys}
    assert props_keys == {
        "app.name", "app.version", "app.long.description",
        "server.port", "server.ssl", "db.url", "db.host", "db.port",
    }

    # All properties keys should be in properties namespace
    for key in arts["app.properties"].config_keys:
        assert key.namespace == "properties"


def test_config_keys_references(tmp_path):
    """Verify reference extraction from config values."""
    app = _app(tmp_path, "refs_test")

    # .env: should find ${DATABASE_PASSWORD} reference
    env_refs = []
    for key in app.artifacts[".env"].config_keys:
        env_refs.extend(key.references)
    assert "${DATABASE_PASSWORD}" in env_refs

    # app.properties: should find %(db.host)s and %(db.port)s references
    props_refs = set()
    for key in app.artifacts["app.properties"].config_keys:
        props_refs.update(key.references)
    assert "%(db.host)s" in props_refs
    assert "%(db.port)s" in props_refs

    # settings.yml: no references expected
    yaml_refs = []
    for key in app.artifacts["config/settings.yml"].config_keys:
        yaml_refs.extend(key.references)
    assert len(yaml_refs) == 0


def test_config_keys_values(tmp_path):
    """Verify value extraction when artifact_text=True."""
    app = _app(tmp_path, "values_test")

    # With artifact_text=True (default), values should be populated
    env_api_key = next((k for k in app.artifacts[".env"].config_keys if k.key == "API_KEY"), None)
    assert env_api_key is not None
    assert env_api_key.value == "${DATABASE_PASSWORD}"

    secret_token = next((k for k in app.artifacts[".env"].config_keys if k.key == "SECRET_API_TOKEN"), None)
    assert secret_token is not None
    assert secret_token.value == "sk-12345abcdef-super-secret"


def test_config_keys_value_gating(tmp_path):
    """Verify value gating when artifact_text=False."""
    # Run with artifact_text=False
    app = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=tmp_path / "no_text",
        artifact_text=False,
    )).analyze().application

    # Keys, namespaces, references should still be present
    env_keys = {k.key for k in app.artifacts[".env"].config_keys}
    assert env_keys == {"DEBUG", "DATABASE_URL", "API_KEY", "SECRET_API_TOKEN", "FLASK_ENV"}

    # But values should all be None
    for key in app.artifacts[".env"].config_keys:
        assert key.value is None

    # References should still be extracted
    env_refs = []
    for key in app.artifacts[".env"].config_keys:
        env_refs.extend(key.references)
    assert "${DATABASE_PASSWORD}" in env_refs


def test_config_keys_spans(tmp_path):
    """Verify that spans slice source text correctly to the values."""
    app = _app(tmp_path, "spans_test")
    env_source = app.artifacts[".env"].source

    # DATABASE_URL has value "postgresql://localhost/mydb # connection string"
    db_url_key = next((k for k in app.artifacts[".env"].config_keys if k.key == "DATABASE_URL"), None)
    assert db_url_key is not None
    assert db_url_key.span is not None

    # The line containing DATABASE_URL should include the value
    line_num = db_url_key.span.start[0]
    end_line_num = db_url_key.span.end[0]
    assert line_num == end_line_num  # span on same line

    # Extract the line and verify it contains both key and value
    lines = env_source.split('\n')
    line_text = lines[line_num - 1]  # line numbers are 1-indexed
    assert "DATABASE_URL" in line_text
    assert "postgresql://localhost/mydb" in line_text


def test_config_keys_extraction_level(tmp_path):
    """Verify config_keys extracted at L1 and identical at L4."""
    app_l1 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=tmp_path / "config_l1",
    )).analyze().application
    app_l4 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=4, no_venv=True, cache_dir=tmp_path / "config_l4",
    )).analyze().application

    # Config keys should be identical at both levels
    for art_name in [".env", "config/settings.yml", "app.properties"]:
        l1_keys = sorted([k.key for k in app_l1.artifacts[art_name].config_keys])
        l4_keys = sorted([k.key for k in app_l4.artifacts[art_name].config_keys])
        assert l1_keys == l4_keys


def test_config_keys_extraction_full(tmp_path):
    """Verify extraction='full' on the three config files."""
    app = _app(tmp_path, "extraction_test")

    # All three new config files should have extraction='full'
    assert app.artifacts[".env"].extraction == "full"
    assert app.artifacts["config/settings.yml"].extraction == "full"
    assert app.artifacts["app.properties"].extraction == "full"
