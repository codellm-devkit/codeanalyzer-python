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


def test_config_uses_full(tmp_path):
    """Verify config-use edges at -a 4: all five fixture shapes (#162 Task 5)."""
    app = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=4, no_venv=True, cache_dir=tmp_path / "config_uses",
    )).analyze().application

    # Find the config_reader module in the symbol table
    config_reader_mod = None
    for mod in app.symbol_table.values():
        if "config_reader" in mod.id:
            config_reader_mod = mod
            break
    assert config_reader_mod is not None, "pkg/config_reader.py not in symbol table"

    # Expected edges at -a 4 (all three tiers resolved):
    # 1. Direct literal: get_database_url() -> os.getenv("DATABASE_URL")
    # 2. Variable-closing: get_api_key() -> os.getenv(key_name="API_KEY")
    # 3. Param-passed: get_secret_token() -> _read_config("SECRET_API_TOKEN")
    # 4. Multi-def unresolved: get_config_multi_def() -> unresolved (two defs)
    # 5. Undefined-key: get_missing_config() -> unresolved (key not in .env)

    # Collect all config_uses edges
    uses = app.config_uses
    uses_by_dst = {}
    for e in uses:
        if e.dst not in uses_by_dst:
            uses_by_dst[e.dst] = []
        uses_by_dst[e.dst].append(e)

    # Collect all config_reads_unresolved
    unresolved = app.config_reads_unresolved
    unresolved_by_reason = {}
    for r in unresolved:
        if r.reason not in unresolved_by_reason:
            unresolved_by_reason[r.reason] = []
        unresolved_by_reason[r.reason].append(r)

    # Expected resolved keys from .env (env namespace):
    # DATABASE_URL (literal), API_KEY (dataflow), SECRET_API_TOKEN (interproc)
    expected_resolved_keys = {"DATABASE_URL", "API_KEY", "SECRET_API_TOKEN"}
    actual_resolved_keys = set()
    for src, edges in uses_by_dst.items():
        if "/@external/" not in src:  # ConfigKey ids don't have /@external/
            # Extract the key name from the ConfigKey id
            # ConfigKey id format: can://python/<app>/<namespace>/<key>
            parts = src.split("/")
            if len(parts) > 0:
                key_name = parts[-1]
                actual_resolved_keys.add(key_name)

    assert actual_resolved_keys >= expected_resolved_keys, \
        f"Expected at least {expected_resolved_keys}, got {actual_resolved_keys}"

    # Unresolved reads:
    # Shape 4: multi-def (reason: non-literal)
    # Shape 5: undefined-key (reason: undefined-key)
    assert "non-literal" in unresolved_by_reason, \
        f"Expected 'non-literal' unresolved, got reasons: {list(unresolved_by_reason.keys())}"
    assert "undefined-key" in unresolved_by_reason, \
        f"Expected 'undefined-key' unresolved, got reasons: {list(unresolved_by_reason.keys())}"

    # Verify that undefined-key has a key value and non-literal does not
    for r in unresolved_by_reason.get("undefined-key", []):
        assert r.key == "NOT_DEFINED_ANYWHERE", \
            f"Expected key 'NOT_DEFINED_ANYWHERE', got {r.key}"

    for r in unresolved_by_reason.get("non-literal", []):
        # Non-literal reasons should have no key (or key is None)
        assert r.key is None, f"Expected key=None for non-literal, got {r.key}"


def test_config_uses_tier_visibility(tmp_path):
    """Verify config-use edges only appear at appropriate levels (#162)."""
    # Literal tier visible at -a 2+
    app_l2 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=2, no_venv=True, cache_dir=tmp_path / "tier_l2",
    )).analyze().application
    uses_l2 = {e.dst for e in app_l2.config_uses}

    # Variable-closing visible at -a 3+
    app_l3 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=3, no_venv=True, cache_dir=tmp_path / "tier_l3",
    )).analyze().application
    uses_l3 = {e.dst for e in app_l3.config_uses}

    # Interproc visible at -a 4
    app_l4 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=4, no_venv=True, cache_dir=tmp_path / "tier_l4",
    )).analyze().application
    uses_l4 = {e.dst for e in app_l4.config_uses}

    # Monotonicity: L2 ⊆ L3 ⊆ L4
    assert uses_l2 <= uses_l3, \
        f"L2 edges should be subset of L3: {uses_l2 - uses_l3} in L2 but not L3"
    assert uses_l3 <= uses_l4, \
        f"L3 edges should be subset of L4: {uses_l3 - uses_l4} in L3 but not L4"


def test_config_uses_determinism(tmp_path):
    """Verify config-use edges are deterministic across two identical runs."""
    import json

    app1 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=4, no_venv=True, cache_dir=tmp_path / "det1",
    )).analyze().application
    app2 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=4, no_venv=True, cache_dir=tmp_path / "det2",
    )).analyze().application

    # Serialize and compare config_uses
    uses1 = json.dumps(
        [{"src": e.src, "dst": e.dst, "prov": sorted(e.prov)} for e in sorted(app1.config_uses, key=lambda x: (x.src, x.dst))],
        sort_keys=True,
    )
    uses2 = json.dumps(
        [{"src": e.src, "dst": e.dst, "prov": sorted(e.prov)} for e in sorted(app2.config_uses, key=lambda x: (x.src, x.dst))],
        sort_keys=True,
    )
    assert uses1 == uses2, "config_uses differ between runs"

    # Serialize and compare config_reads_unresolved
    unres1 = json.dumps(
        [{"site": r.site, "callee": r.callee, "key": r.key, "reason": r.reason, "prov": sorted(r.prov)}
         for r in sorted(app1.config_reads_unresolved, key=lambda x: (x.site, x.reason))],
        sort_keys=True,
    )
    unres2 = json.dumps(
        [{"site": r.site, "callee": r.callee, "key": r.key, "reason": r.reason, "prov": sorted(r.prov)}
         for r in sorted(app2.config_reads_unresolved, key=lambda x: (x.site, x.reason))],
        sort_keys=True,
    )
    assert unres1 == unres2, "config_reads_unresolved differ between runs"
