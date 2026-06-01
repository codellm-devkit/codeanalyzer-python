"""
Unit tests for taint analysis functionality.
Tests the taint analysis feature at analysis level 3.

Tests are organized into two groups:
1. Infrastructure tests (no CodeQL required) - always run
2. Integration tests (require CodeQL) - skipped if CodeQL unavailable
"""

import pytest
from pathlib import Path
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options.options import AnalysisOptions
from codeanalyzer.schema.py_schema import PyTaintAnalysisResult
from codeanalyzer.config.taint_config_defaults import get_default_taint_config
from codeanalyzer.config.taint_config_loader import TaintConfigLoader
from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL


# Test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "taint_analysis"


@pytest.fixture
def sql_injection_app():
    """Path to SQL injection test app."""
    return FIXTURES_DIR / "sql_injection_app"


@pytest.fixture
def command_injection_app():
    """Path to command injection test app."""
    return FIXTURES_DIR / "command_injection_app"


@pytest.fixture
def path_traversal_app():
    """Path to path traversal test app."""
    return FIXTURES_DIR / "path_traversal_app"


@pytest.fixture
def flask_app():
    """Path to Flask test app."""
    return FIXTURES_DIR / "flask_app"


@pytest.fixture
def sanitizer_app():
    """Path to sanitizer test app."""
    return FIXTURES_DIR / "sanitizer_app"


@pytest.fixture
def ssti_app():
    """Path to SSTI test app."""
    return FIXTURES_DIR / "ssti_app"


@pytest.fixture
def deserialization_app():
    """Path to unsafe deserialization test app."""
    return FIXTURES_DIR / "deserialization_app"


@pytest.fixture
def ssrf_app():
    """Path to SSRF test app."""
    return FIXTURES_DIR / "ssrf_app"


@pytest.fixture
def default_taint_config():
    """Get default taint configuration."""
    return get_default_taint_config()


# ============================================================================
# Infrastructure Tests (no CodeQL required)
# ============================================================================

class TestTaintAnalysisConfiguration:
    """Tests for taint analysis configuration."""

    def test_default_configuration(self, default_taint_config):
        """Test default taint configuration."""
        assert len(default_taint_config.sources) > 0
        # Sinks list is intentionally empty — all sinks are covered by CodeQL's built-in
        # security models (LdapInjection, Xxe, SSRF, SSTI, UnsafeDeserialization, …)
        # imported in the generated query rather than enumerated here.
        assert isinstance(default_taint_config.sinks, list)
        assert len(default_taint_config.sanitizers) > 0

        # Verify all sources are enabled by default
        enabled_sources = [s for s in default_taint_config.sources if s.enabled]
        assert len(enabled_sources) == len(default_taint_config.sources)

    def test_custom_configuration_yaml(self, sql_injection_app, tmp_path):
        """Test custom taint configuration from YAML."""
        # Create custom config with only SQL injection sinks
        config_content = """
sources:
  - source_type: "user_input"
    name: "user_input"
    description: "User input from input() function"
    pattern: 'API::builtin("input").getACall()'
    enabled: true

sinks:
  - sink_type: "sql_execute"
    name: "sql_execute"
    description: "SQL query execution"
    pattern: 'API::moduleImport("sqlite3").getMember("execute").getACall()'
    vulnerability_type: "SQL Injection"
    severity: "critical"
    enabled: true

sanitizers:
  - sanitizer_type: "parameterized_query"
    name: "parameterized_query"
    description: "Parameterized SQL queries"
    pattern: 'API::moduleImport("sqlite3").getMember("execute").getACall()'
    enabled: true
"""
        config_file = tmp_path / "custom_taint_config.yaml"
        config_file.write_text(config_content)

        # Load custom config
        loader = TaintConfigLoader()
        config = loader.load_config(config_file, use_defaults=False)

        assert len(config.sources) == 1
        assert len(config.sinks) == 1
        assert len(config.sanitizers) == 1
        assert config.sources[0].name == "user_input"
        assert config.sinks[0].vulnerability_type == "SQL Injection"

    def test_query_contains_all_builtin_imports(self, default_taint_config):
        """Generated query must import all 20 CodeQL security customization modules."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        query = TaintQueryGenerator.generate_query(default_taint_config)
        expected_modules = [
            "LdapInjectionCustomizations",
            "XxeCustomizations",
            "ServerSideRequestForgeryCustomizations",
            "TemplateInjectionCustomizations",
            "UnsafeDeserializationCustomizations",
            "UrlRedirectCustomizations",
            "LogInjectionCustomizations",
            "NoSqlInjectionCustomizations",
            "XpathInjectionCustomizations",
            "TarSlipCustomizations",
            "HttpHeaderInjectionCustomizations",
            "CookieInjectionCustomizations",
            "PolynomialReDoSCustomizations",
            # CleartextStorageCustomizations and CleartextLoggingCustomizations are
            # intentionally excluded: they use SensitiveDataSource (not RemoteFlowSource)
            # and produce false positives when combined with general user-input sources.
        ]
        for mod in expected_modules:
            assert mod in query, f"Generated query is missing import for {mod}"

    def test_query_contains_all_builtin_sinks(self, default_taint_config):
        """Generated query must include instanceof checks for all built-in sink classes."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        query = TaintQueryGenerator.generate_query(default_taint_config)
        expected_sinks = [
            "LdapInjection::DnSink",
            "LdapInjection::FilterSink",
            "Xxe::Sink",
            "ServerSideRequestForgery::Sink",
            "TemplateInjection::Sink",
            "UnsafeDeserialization::Sink",
            "UrlRedirect::Sink",
            "LogInjection::Sink",
            "NoSqlInjection::StringSink",
            "NoSqlInjection::DictSink",
            "XpathInjection::Sink",
            "TarSlip::Sink",
            "HttpHeaderInjection::Sink",
            "CookieInjection::Sink",
            "PolynomialReDoS::Sink",
            # CleartextStorage::Sink and CleartextLogging::Sink are intentionally excluded:
            # these use SensitiveDataSource internally and produce false positives when
            # combined with general user-input sources in a unified query.
        ]
        for sink in expected_sinks:
            assert sink in query, f"Generated query is missing instanceof check for {sink}"


class TestTaintAnalysisEdgeCases:
    """Tests for edge cases and error handling."""

    def test_disabled_sources_and_sinks(self, tmp_path):
        """Test configuration with disabled sources and sinks."""
        # Create config with all items disabled (include required fields)
        config_content = """
sources:
  - source_type: "user_input"
    name: "user_input"
    description: "User input"
    pattern: 'API::builtin("input").getACall()'
    enabled: false

sinks:
  - sink_type: "sql_execution"
    name: "sql_execute"
    description: "SQL execution"
    pattern: 'API::moduleImport("sqlite3").getMember("execute").getACall()'
    vulnerability_type: "SQL Injection"
    severity: "critical"
    enabled: false

sanitizers: []
"""
        config_file = tmp_path / "disabled_config.yaml"
        config_file.write_text(config_content)

        loader = TaintConfigLoader()
        config = loader.load_config(config_file, use_defaults=False)

        # Filter should remove disabled items
        filtered_config = loader._filter_disabled(config)
        assert len(filtered_config.sources) == 0
        assert len(filtered_config.sinks) == 0


# ============================================================================
# Extensibility mechanism unit tests (no CodeQL required)
# ============================================================================

class TestTaintConfigExtensibility:
    """Tests for the taint config extensibility mechanism: merge, disabled sinks,
    use_defaults, and validate_config integration."""

    # ------------------------------------------------------------------
    # Scalar merge correctness
    # ------------------------------------------------------------------

    def test_merge_scalars_custom_wins(self):
        """Custom config scalars always override base — was broken before fix."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        base = TaintAnalysisConfig(max_path_length=15, group_by_vulnerability=False, confidence_threshold="low")
        custom = TaintAnalysisConfig(max_path_length=5, group_by_vulnerability=True, confidence_threshold="high")
        merged = TaintConfigLoader._merge_configs(base, custom)
        assert merged.max_path_length == 5
        assert merged.group_by_vulnerability is True
        assert merged.confidence_threshold == "high"

    def test_merge_scalars_custom_default_value_still_wins(self):
        """Custom config with value == schema default (e.g. max_path_length=10) must win.
        Previously a sentinel comparison '!= 10' silently ignored this case."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        base = TaintAnalysisConfig(max_path_length=20, confidence_threshold="low")
        custom = TaintAnalysisConfig(max_path_length=10, confidence_threshold="medium")
        merged = TaintConfigLoader._merge_configs(base, custom)
        assert merged.max_path_length == 10, "max_path_length=10 must not be silently discarded"
        assert merged.confidence_threshold == "medium", "confidence_threshold='medium' must not be silently discarded"

    def test_merge_additive_booleans(self):
        """include_implicit_flows and include_safe_flows use OR (enabling is additive)."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        base = TaintAnalysisConfig(include_implicit_flows=True, include_safe_flows=False)
        custom = TaintAnalysisConfig(include_implicit_flows=False, include_safe_flows=True)
        merged = TaintConfigLoader._merge_configs(base, custom)
        assert merged.include_implicit_flows is True   # OR(True, False)
        assert merged.include_safe_flows is True        # OR(False, True)

    def test_merge_exclude_lists_combined(self):
        """exclude_files and exclude_functions are unioned across base and custom."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        base = TaintAnalysisConfig(exclude_files=["tests/**"], exclude_functions=["myapp.utils.safe"])
        custom = TaintAnalysisConfig(exclude_files=["vendor/**"], exclude_functions=["myapp.debug.dump"])
        merged = TaintConfigLoader._merge_configs(base, custom)
        assert "tests/**" in merged.exclude_files
        assert "vendor/**" in merged.exclude_files
        assert "myapp.utils.safe" in merged.exclude_functions
        assert "myapp.debug.dump" in merged.exclude_functions

    # ------------------------------------------------------------------
    # disabled_builtin_sinks
    # ------------------------------------------------------------------

    def test_disabled_builtin_sinks_removes_from_query(self):
        """Sinks listed in disabled_builtin_sinks must not appear in generated query."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        config = TaintAnalysisConfig(disabled_builtin_sinks=["PolynomialReDoS::Sink", "CookieInjection::Sink"])
        query = TaintQueryGenerator.generate_query(config)
        assert "PolynomialReDoS::Sink" not in query
        assert "CookieInjection::Sink" not in query
        assert "SqlInjection::Sink" in query  # others remain

    def test_disabled_builtin_sinks_empty_keeps_all(self):
        """Empty disabled_builtin_sinks list keeps all 20 built-in sinks in query."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        config = TaintAnalysisConfig()
        query = TaintQueryGenerator.generate_query(config)
        for name in TaintQueryGenerator.builtin_sink_names():
            assert name in query, f"Expected {name} in query with no disabled sinks"

    def test_disabled_builtin_sinks_merged_from_both_sides(self):
        """disabled_builtin_sinks from base and custom are unioned on merge."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        base = TaintAnalysisConfig(disabled_builtin_sinks=["CookieInjection::Sink"])
        custom = TaintAnalysisConfig(disabled_builtin_sinks=["PolynomialReDoS::Sink"])
        merged = TaintConfigLoader._merge_configs(base, custom)
        assert "CookieInjection::Sink" in merged.disabled_builtin_sinks
        assert "PolynomialReDoS::Sink" in merged.disabled_builtin_sinks

    def test_disabled_builtin_sinks_survives_filter_disabled(self):
        """_filter_disabled must carry disabled_builtin_sinks through unchanged."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        config = TaintAnalysisConfig(disabled_builtin_sinks=["TarSlip::Sink"])
        filtered = TaintConfigLoader._filter_disabled(config)
        assert "TarSlip::Sink" in filtered.disabled_builtin_sinks

    def test_disabled_builtin_sinks_from_yaml(self, tmp_path):
        """disabled_builtin_sinks loaded from YAML file is honoured in query."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        yaml_content = """
disabled_builtin_sinks:
  - PolynomialReDoS::Sink
  - HttpHeaderInjection::Sink
sources: []
sinks: []
sanitizers: []
"""
        config_file = tmp_path / "cfg.yaml"
        config_file.write_text(yaml_content)
        config = TaintConfigLoader.load_config(config_file, use_defaults=False)
        assert "PolynomialReDoS::Sink" in config.disabled_builtin_sinks
        query = TaintQueryGenerator.generate_query(config)
        assert "PolynomialReDoS::Sink" not in query
        assert "HttpHeaderInjection::Sink" not in query

    # ------------------------------------------------------------------
    # use_defaults flag / three modes
    # ------------------------------------------------------------------

    def test_use_defaults_false_no_custom_gives_empty_config(self):
        """use_defaults=False with no config_path produces empty sources/sinks/sanitizers."""
        config = TaintConfigLoader.load_config(use_defaults=False)
        assert len(config.sources) == 0
        assert len(config.sinks) == 0
        assert len(config.sanitizers) == 0

    def test_use_defaults_true_gives_default_sources(self):
        """use_defaults=True (default) loads default sources and sanitizers."""
        config = TaintConfigLoader.load_config(use_defaults=True)
        assert len(config.sources) > 0
        assert len(config.sanitizers) > 0

    def test_use_defaults_false_with_custom_config_is_custom_only(self, tmp_path):
        """Mode 2: --no-taint-defaults → only custom sources/sinks, no defaults."""
        yaml_content = """
sources:
  - name: only_source
    description: "Only this source"
    pattern: 'API::builtin("input").getACall()'
    source_type: user_input
    enabled: true
sinks: []
sanitizers: []
"""
        config_file = tmp_path / "custom_only.yaml"
        config_file.write_text(yaml_content)
        config = TaintConfigLoader.load_config(config_file, use_defaults=False)
        assert len(config.sources) == 1
        assert config.sources[0].name == "only_source"

    def test_use_defaults_true_with_custom_config_is_union(self, tmp_path):
        """Mode 3: --taint-defaults + --taint-config → union of defaults and custom."""
        yaml_content = """
sources:
  - name: extra_source
    description: "Additional source"
    pattern: 'API::builtin("input").getACall()'
    source_type: user_input
    enabled: true
sinks: []
sanitizers: []
"""
        config_file = tmp_path / "extra.yaml"
        config_file.write_text(yaml_content)
        config = TaintConfigLoader.load_config(config_file, use_defaults=True)
        names = [s.name for s in config.sources]
        assert "extra_source" in names
        assert len(config.sources) > 1  # defaults present too

    # ------------------------------------------------------------------
    # validate_config integration
    # ------------------------------------------------------------------

    def test_validate_config_warns_no_sources(self):
        """validate_config returns an issue when no sources are configured."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        config = TaintAnalysisConfig(sources=[], sinks=[], sanitizers=[])
        issues = TaintConfigLoader.validate_config(config)
        assert any("No taint sources" in i for i in issues)

    def test_validate_config_returns_issues_for_empty_pattern(self):
        """validate_config catches empty pattern strings."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSourceConfig
        config = TaintAnalysisConfig(
            sources=[TaintSourceConfig(name="bad", description="d", pattern="   ", source_type="t")]
        )
        issues = TaintConfigLoader.validate_config(config)
        assert any("Empty pattern" in i for i in issues)

    def test_validate_config_returns_issues_for_duplicates(self):
        """validate_config catches duplicate source names."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSourceConfig
        src = TaintSourceConfig(name="dup", description="d", pattern="API::builtin(\"x\")", source_type="t")
        config = TaintAnalysisConfig(sources=[src, src])
        issues = TaintConfigLoader.validate_config(config)
        assert any("Duplicate" in i for i in issues)

    # ------------------------------------------------------------------
    # builtin_sink_names helper
    # ------------------------------------------------------------------

    def test_builtin_sink_names_complete(self):
        """builtin_sink_names() returns exactly 20 entries matching BUILTIN_SINKS."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        names = TaintQueryGenerator.builtin_sink_names()
        assert len(names) == TaintQueryGenerator.builtin_sink_count()
        assert "SqlInjection::Sink" in names
        assert "UnsafeDeserialization::Sink" in names
        assert "TemplateInjection::Sink" in names


# ============================================================================
# Integration Tests (require CodeQL databases)
# ============================================================================

class TestTaintAnalysisBasic:
    """Basic taint analysis tests using pre-built CodeQL databases."""

    def test_sql_injection_detection(self, sql_injection_db, codeql_packs_dir):
        """Test detection of SQL injection vulnerabilities.

        sql_injection_app has 3 vulnerable cursor.execute() calls (direct concat,
        format string, f-string) plus sys.argv → execute. CodeQL's SqlInjection::Sink
        model detects all of them. Expect at least 6 critical SQL Injection flows.
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        config = get_default_taint_config()
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "sql_injection_app",
            db_path=sql_injection_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )

        result = codeql.analyze_taint_flows()

        assert result is not None
        assert isinstance(result, PyTaintAnalysisResult)
        assert len(result.flows) >= 6, (
            f"Expected at least 6 SQL injection flows, got {len(result.flows)}"
        )
        sql_flows = [f for f in result.flows if f.vulnerability_type == "SQL Injection"]
        assert len(sql_flows) >= 6, (
            f"Expected at least 6 SQL Injection flows, got {len(sql_flows)}"
        )
        assert all(f.severity == "critical" for f in sql_flows), (
            "All SQL Injection flows should be critical severity"
        )

    def test_command_injection_detection(self, command_injection_db, codeql_packs_dir):
        """Test detection of command injection vulnerabilities.

        command_injection_app has os.system, subprocess.call, subprocess.run calls
        with user input. CodeQL's CommandInjection::Sink model detects them.
        Expect at least 10 flows (9 critical command injection + 1 high path).
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        config = get_default_taint_config()
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "command_injection_app",
            db_path=command_injection_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )

        result = codeql.analyze_taint_flows()

        assert result is not None
        assert isinstance(result, PyTaintAnalysisResult)
        assert len(result.flows) >= 10, (
            f"Expected at least 10 flows from command_injection_app, got {len(result.flows)}"
        )
        cmd_flows = [f for f in result.flows if f.vulnerability_type == "Command Injection"]
        assert len(cmd_flows) >= 5, (
            f"Expected at least 5 Command Injection flows, got {len(cmd_flows)}"
        )
        critical_flows = [f for f in result.flows if f.severity == "critical"]
        assert len(critical_flows) >= 9, (
            f"Expected at least 9 critical flows, got {len(critical_flows)}"
        )

    def test_path_traversal_detection(self, path_traversal_db, codeql_packs_dir):
        """Test detection of path traversal vulnerabilities.

        path_traversal_app has multiple open() calls with user-controlled paths.
        CodeQL's PathInjection::Sink model detects them. Expect at least 9 high flows.
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        config = get_default_taint_config()
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "path_traversal_app",
            db_path=path_traversal_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )

        result = codeql.analyze_taint_flows()

        assert result is not None
        assert isinstance(result, PyTaintAnalysisResult)
        assert len(result.flows) >= 9, (
            f"Expected at least 9 path traversal flows, got {len(result.flows)}"
        )
        path_flows = [f for f in result.flows if f.vulnerability_type == "Path Traversal"]
        assert len(path_flows) >= 9, (
            f"Expected at least 9 Path Traversal flows, got {len(path_flows)}"
        )
        assert all(f.severity == "high" for f in path_flows), (
            "All Path Traversal flows should be high severity"
        )



class TestTaintAnalysisFlowStructure:
    """Tests for taint flow structure and metadata."""

    def test_flow_has_required_fields(self, sql_injection_db, codeql_packs_dir):
        """All detected flows must have valid structural fields, typed source/sink locations.

        Combines field-presence, severity/confidence enum checks, source location,
        and sink location into one CodeQL run against sql_injection_app.
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        config = get_default_taint_config()
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "sql_injection_app",
            db_path=sql_injection_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )

        result = codeql.analyze_taint_flows()

        assert len(result.flows) >= 6, f"Expected at least 6 flows, got {len(result.flows)}"
        assert all(f.vulnerability_type == "SQL Injection" for f in result.flows), (
            "All flows from sql_injection_app should be SQL Injection"
        )
        for flow in result.flows:
            assert flow.flow_id, "flow_id must be non-empty"
            assert flow.source is not None and flow.sink is not None
            assert flow.severity in ("critical", "high", "medium", "low"), (
                f"severity must be a valid level, got {flow.severity!r}"
            )
            assert flow.confidence in ("high", "medium", "low"), (
                f"confidence must be a valid level, got {flow.confidence!r}"
            )
            # Source location
            assert flow.source.source_type, "source_type must be non-empty"
            assert flow.source.call_site is not None, "source call_site must be set"
            assert flow.source.call_site.start_line > 0, "source start_line must be positive"
            # Sink location and type
            assert flow.sink.sink_type == "sql_execution", (
                f"Expected sql_execution sink_type, got {flow.sink.sink_type!r}"
            )
            assert flow.sink.call_site is not None, "sink call_site must be set"
            assert flow.sink.call_site.start_line > 0, "sink start_line must be positive"


class TestTaintAnalysisConfiguration_Integration:
    """Integration tests for taint analysis configuration."""

    def test_custom_config_with_no_matching_sinks_returns_zero_flows(self, sql_injection_db, codeql_packs_dir):
        """A config restricted to eval() sinks must find 0 flows in sql_injection_app.

        sql_injection_app has no eval() calls, so an eval-only config (no built-in
        sink models) must produce an empty result.  This verifies that the config
        actually controls what is detected rather than always using defaults.
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSourceConfig, TaintSinkConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        minimal_config = TaintAnalysisConfig(
            sources=[TaintSourceConfig(
                name="user_input", source_type="user_input",
                description="User input",
                pattern='API::builtin("input").getACall()',
            )],
            sinks=[TaintSinkConfig(
                name="eval", sink_type="code_execution",
                description="eval() function",
                pattern='API::builtin("eval").getACall()',
                vulnerability_type="Code Injection", severity="critical",
                argument_index=0,
            )],
            sanitizers=[],
            # Disable all built-in sinks so only the explicit eval() sink is active.
            disabled_builtin_sinks=TaintQueryGenerator.builtin_sink_names(),
        )
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "sql_injection_app",
            db_path=sql_injection_db,
            codeql_packs_dir=codeql_packs_dir,
        )
        result = codeql.analyze_taint_flows(config_override=minimal_config)
        assert len(result.flows) == 0, (
            f"eval-only config should find 0 flows in sql_injection_app "
            f"(no eval() calls), got {len(result.flows)}"
        )


class TestTaintAnalysisSanitizers_Integration:
    """Integration tests for sanitizer detection."""

    def test_sanitizer_app_runs_successfully(self, sanitizer_db, codeql_packs_dir):
        """Test that taint analysis runs on sanitizer app and detects some flows.

        sanitizer_app has both safe (sanitized) and unsafe code. The unsafe code
        should produce at least 3 flows (2 critical, 1 high).
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        config = get_default_taint_config()
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "sanitizer_app",
            db_path=sanitizer_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )

        result = codeql.analyze_taint_flows()

        assert result is not None
        assert isinstance(result, PyTaintAnalysisResult)
        assert len(result.flows) >= 3, (
            f"sanitizer_app should have at least 3 flows (unsafe code), got {len(result.flows)}"
        )



class TestTaintAnalysisMultipleVulnerabilities:
    """Tests for detecting multiple vulnerability types."""

    def test_flask_app_analysis(self, flask_db, codeql_packs_dir):
        """Test taint analysis on Flask web application detects multiple vuln types.

        flask_app has SQL injection, command injection, and path traversal vulnerabilities.
        Expect at least 11 flows (8 critical, 3 high) across multiple vulnerability types.
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        config = get_default_taint_config()
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "flask_app",
            db_path=flask_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )

        result = codeql.analyze_taint_flows()

        assert result is not None
        assert isinstance(result, PyTaintAnalysisResult)
        assert len(result.flows) >= 11, (
            f"Expected at least 11 flows from flask_app, got {len(result.flows)}"
        )
        # Flask app should have multiple vulnerability types
        vuln_types = {f.vulnerability_type for f in result.flows}
        assert len(vuln_types) >= 2, (
            f"Expected at least 2 vulnerability types, got {vuln_types}"
        )
        # Should have both critical and high severity flows
        critical_flows = [f for f in result.flows if f.severity == "critical"]
        high_flows = [f for f in result.flows if f.severity == "high"]
        assert len(critical_flows) >= 8, (
            f"Expected at least 8 critical flows, got {len(critical_flows)}"
        )
        assert len(high_flows) >= 3, (
            f"Expected at least 3 high flows, got {len(high_flows)}"
        )



class TestTaintAnalysisIntegration_Codeanalyzer:
    """Integration tests using the full Codeanalyzer pipeline."""

    def test_analysis_level_1_no_taint(self, sql_injection_app, tmp_path):
        """Test that analysis level 1 doesn't perform taint analysis."""
        options = AnalysisOptions(
            input=sql_injection_app,
            analysis_level=1,
            using_codeql=False,
            output=tmp_path,
            taint_config=None
        )

        with Codeanalyzer(options) as analyzer:
            result = analyzer.analyze()

        # Level 1 should not have taint analysis
        assert result.taint_analysis is None

    def test_analysis_level_3_requires_codeql(self, sql_injection_app, tmp_path):
        """Test that analysis level 3 with CodeQL performs taint analysis and detects flows.

        Uses sql_injection_app which has known SQL injection vulnerabilities.
        Expects at least 6 critical SQL Injection flows in the output.
        """
        import shutil
        if not shutil.which("codeql"):
            pytest.skip("CodeQL not available")

        options = AnalysisOptions(
            input=sql_injection_app,
            analysis_level=3,
            using_codeql=True,
            output=tmp_path,
            taint_config=None
        )

        with Codeanalyzer(options) as analyzer:
            result = analyzer.analyze()

        # Level 3 should have taint analysis
        assert result.taint_analysis is not None
        assert isinstance(result.taint_analysis, PyTaintAnalysisResult)
        # Should detect SQL injection flows
        assert len(result.taint_analysis.flows) >= 6, (
            f"Expected at least 6 SQL injection flows, got {len(result.taint_analysis.flows)}"
        )
        sql_flows = [
            f for f in result.taint_analysis.flows
            if f.vulnerability_type == "SQL Injection"
        ]
        assert len(sql_flows) >= 6, (
            f"Expected at least 6 SQL Injection flows, got {len(sql_flows)}"
        )
        assert all(f.severity == "critical" for f in sql_flows), (
            "All SQL Injection flows should be critical severity"
        )


# ============================================================================
# Integration Tests — New Vulnerability Types (require CodeQL)
# ============================================================================

class TestTaintAnalysisNewVulnerabilityTypes:
    """Integration tests for vulnerability types added via the expanded built-in CodeQL models."""

    def test_ssti_detection(self, ssti_db, codeql_packs_dir):
        """Server-Side Template Injection must be detected in ssti_app fixture."""
        import shutil
        if not shutil.which("codeql"):
            pytest.skip("CodeQL not available")
        if codeql_packs_dir is None:
            pytest.skip("CodeQL packs not available")

        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "ssti_app",
            db_path=ssti_db,
            codeql_packs_dir=codeql_packs_dir,
        )
        from codeanalyzer.config.taint_config_defaults import get_default_taint_config as _get_cfg
        result = codeql.analyze_taint_flows(config_override=_get_cfg())

        ssti_flows = [f for f in result.flows if "Template Injection" in f.vulnerability_type]
        assert len(ssti_flows) >= 1, (
            f"Expected at least 1 SSTI flow, got {len(ssti_flows)}. "
            f"All flows: {[f.vulnerability_type for f in result.flows]}"
        )
        assert all(f.severity == "critical" for f in ssti_flows), (
            "All SSTI flows should be critical severity"
        )

    def test_unsafe_deserialization_detection(self, deserialization_db, codeql_packs_dir):
        """Unsafe Deserialization must be detected in deserialization_app fixture."""
        import shutil
        if not shutil.which("codeql"):
            pytest.skip("CodeQL not available")
        if codeql_packs_dir is None:
            pytest.skip("CodeQL packs not available")

        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "deserialization_app",
            db_path=deserialization_db,
            codeql_packs_dir=codeql_packs_dir,
        )
        from codeanalyzer.config.taint_config_defaults import get_default_taint_config as _get_cfg
        result = codeql.analyze_taint_flows(config_override=_get_cfg())

        deser_flows = [f for f in result.flows if "Deserialization" in f.vulnerability_type]
        assert len(deser_flows) >= 1, (
            f"Expected at least 1 Unsafe Deserialization flow, got {len(deser_flows)}. "
            f"All flows: {[f.vulnerability_type for f in result.flows]}"
        )
        assert all(f.severity == "critical" for f in deser_flows), (
            "All Unsafe Deserialization flows should be critical severity"
        )

    def test_ssrf_detection(self, ssrf_db, codeql_packs_dir):
        """Server-Side Request Forgery must be detected in ssrf_app fixture."""
        import shutil
        if not shutil.which("codeql"):
            pytest.skip("CodeQL not available")
        if codeql_packs_dir is None:
            pytest.skip("CodeQL packs not available")

        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "ssrf_app",
            db_path=ssrf_db,
            codeql_packs_dir=codeql_packs_dir,
        )
        from codeanalyzer.config.taint_config_defaults import get_default_taint_config as _get_cfg
        result = codeql.analyze_taint_flows(config_override=_get_cfg())

        ssrf_flows = [f for f in result.flows if "Request Forgery" in f.vulnerability_type]
        assert len(ssrf_flows) >= 1, (
            f"Expected at least 1 SSRF flow, got {len(ssrf_flows)}. "
            f"All flows: {[f.vulnerability_type for f in result.flows]}"
        )


class TestFocusedTaintAPIs_Unit:
    """Unit tests (no CodeQL) for TaintNodeRef, location-based config, and focused query generation."""

    # ---- helpers -------------------------------------------------------

    def _make_source(self, file_path="/abs/app/views.py", line=20, col=-1, source_type="web_request"):
        from codeanalyzer.schema.py_schema import PyTaintSource, PyCallsite
        return PyTaintSource(
            source_type=source_type,
            call_site=PyCallsite(
                method_name="request.args.get",
                start_line=line,
                end_line=line,
                start_column=col,
                file_path=file_path,
            ),
        )

    def _make_sink(self, file_path="/abs/app/views.py", line=35, col=-1,
                   sink_type="sql_execution", severity="critical",
                   vulnerability_type="SQL Injection"):
        from codeanalyzer.schema.py_schema import PyTaintSink, PyCallsite
        return PyTaintSink(
            sink_type=sink_type,
            severity=severity,
            vulnerability_type=vulnerability_type,
            call_site=PyCallsite(
                method_name="cursor.execute",
                start_line=line,
                end_line=line,
                start_column=col,
                file_path=file_path,
            ),
        )

    def _focused_source_query(self, sources, base_config=None):
        from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        cfg = base_config or get_default_taint_config()
        focused = cfg.model_copy(update={
            "sources": CodeQL._build_source_configs(sources),
            "include_remote_flow_source": False,
        })
        return TaintQueryGenerator.generate_query(focused)

    def _focused_sink_query(self, sinks, base_config=None):
        from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        cfg = base_config or get_default_taint_config()
        focused = cfg.model_copy(update={
            "sinks": CodeQL._build_sink_configs(sinks),
            "disabled_builtin_sinks": TaintQueryGenerator.builtin_sink_names(),
        })
        return TaintQueryGenerator.generate_query(focused)

    # ---- TaintNodeRef construction -------------------------------------

    def test_taint_node_ref_requires_file_and_line(self):
        """TaintNodeRef must require file_path and start_line."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        ref = TaintNodeRef(file_path="/abs/app.py", start_line=42)
        assert ref.file_path == "/abs/app.py"
        assert ref.start_line == 42
        assert ref.start_column == -1

    def test_taint_node_ref_with_column(self):
        """TaintNodeRef with start_column stores the column for sub-line precision."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        ref = TaintNodeRef(file_path="/abs/app.py", start_line=10, start_column=8)
        assert ref.start_column == 8

    # ---- TaintSourceConfig / TaintSinkConfig with locations -----------

    def test_source_config_with_locations_no_pattern(self):
        """TaintSourceConfig accepts locations as a replacement for pattern."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, TaintSourceConfig
        ref = TaintNodeRef(file_path="/abs/app.py", start_line=42)
        sc = TaintSourceConfig(name="x", description="d", source_type="web_request", locations=[ref])
        assert sc.pattern is None
        assert len(sc.locations) == 1

    def test_source_config_requires_pattern_or_locations(self):
        """TaintSourceConfig must raise ValueError when neither pattern nor locations is given."""
        from codeanalyzer.schema.py_schema import TaintSourceConfig
        with pytest.raises(ValueError, match="pattern.*locations|locations.*pattern"):
            TaintSourceConfig(name="bad", description="d", source_type="t")

    def test_sink_config_requires_pattern_or_locations(self):
        """TaintSinkConfig must raise ValueError when neither pattern nor locations is given."""
        from codeanalyzer.schema.py_schema import TaintSinkConfig
        with pytest.raises(ValueError):
            TaintSinkConfig(name="bad", description="d", sink_type="sql_execution",
                            vulnerability_type="SQL Injection", severity="critical")

    # ---- generate_query: location-based source ------------------------

    def test_location_source_in_query_contains_file_and_line(self):
        """A TaintSourceConfig with locations must embed file and line in the query."""
        source = self._make_source(file_path="/myapp/views.py", line=42, source_type="web_request")
        query = self._focused_source_query([source])
        assert 'getAbsolutePath() = "/myapp/views.py"' in query
        assert "getStartLine() = 42" in query
        assert 'sourceType = "web_request"' in query

    def test_location_source_does_not_use_isinstance(self):
        """Location-pinned source predicate must not reference RemoteFlowSource or other classes."""
        source = self._make_source()
        query = self._focused_source_query([source])
        assert "node instanceof RemoteFlowSource" not in query
        assert "instanceof" not in query.split("predicate isConfiguredSource")[1].split("predicate isConfiguredSink")[0]

    def test_location_source_with_column_adds_column_constraint(self):
        """When start_column >= 0, the query includes a getStartColumn() constraint."""
        source = self._make_source(file_path="/app.py", line=10, col=8)
        query = self._focused_source_query([source])
        assert "getStartColumn() = 8" in query

    def test_location_source_without_column_omits_column_constraint(self):
        """When start_column == -1 (default), the isConfiguredSource predicate has no getStartColumn()."""
        source = self._make_source(file_path="/app.py", line=10, col=-1)
        query = self._focused_source_query([source])
        # Isolate just the isConfiguredSource predicate body
        src_pred = query.split("predicate isConfiguredSource")[1].split("predicate isConfiguredSink")[0]
        assert "getStartColumn()" not in src_pred

    def test_focused_source_query_keeps_builtin_sinks(self):
        """Pinning the source side must not suppress built-in sink classes."""
        source = self._make_source(file_path="/routes.py", line=15)
        query = self._focused_source_query([source])
        assert "SqlInjection::Sink" in query

    # ---- generate_query: location-based sink --------------------------

    def test_location_sink_in_query_contains_file_line_and_metadata(self):
        """A TaintSinkConfig with locations must embed file, line, and metadata."""
        sink = self._make_sink(file_path="/db/queries.py", line=88,
                               sink_type="sql_execution", severity="critical",
                               vulnerability_type="SQL Injection")
        query = self._focused_sink_query([sink])
        assert 'getAbsolutePath() = "/db/queries.py"' in query
        assert "getStartLine() = 88" in query
        assert 'sinkType = "sql_execution"' in query
        assert 'severity = "critical"' in query
        assert 'vulnerabilityType = "SQL Injection"' in query

    def test_location_sink_does_not_use_isinstance(self):
        """Location-pinned sink predicate must not reference SqlInjection::Sink or other classes."""
        sink = self._make_sink()
        query = self._focused_sink_query([sink])
        # Only the source predicate section should have no instanceof in it either, but
        # specifically verify no class-based instanceof in the sink predicate.
        assert "SqlInjection::Sink" not in query

    def test_location_sink_with_column_adds_column_constraint(self):
        """When start_column >= 0, the sink predicate includes a getStartColumn() constraint."""
        sink = self._make_sink(file_path="/db.py", line=5, col=4)
        query = self._focused_sink_query([sink])
        assert "getStartColumn() = 4" in query

    def test_focused_sink_query_keeps_remote_flow_source(self):
        """Pinning the sink side must not suppress RemoteFlowSource on the source side."""
        sink = self._make_sink(file_path="/db.py", line=50)
        query = self._focused_sink_query([sink])
        assert "node instanceof RemoteFlowSource" in query

    # ---- multiple locations OR-combined --------------------------------

    def test_multiple_sources_or_combined_in_query(self):
        """Two source locations must both appear in the generated predicate."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        src_a = self._make_source(file_path="/a.py", line=1)
        src_b = TaintNodeRef(file_path="/b.py", start_line=2)
        query = self._focused_source_query([src_a, src_b])
        assert 'getAbsolutePath() = "/a.py"' in query
        assert 'getAbsolutePath() = "/b.py"' in query

    def test_multiple_sinks_or_combined_in_query(self):
        """Two sink locations must both appear in the generated predicate."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        sink_a = self._make_sink(file_path="/db1.py", line=10)
        sink_b = TaintNodeRef(file_path="/db2.py", start_line=20)
        query = self._focused_sink_query([sink_a, sink_b])
        assert 'getAbsolutePath() = "/db1.py"' in query
        assert 'getAbsolutePath() = "/db2.py"' in query

    # ---- both sides pinned (analyze_taint_flow_paths) -----------------

    def test_both_sides_pinned_excludes_remote_flow_source_and_builtin_sinks(self):
        """Pinning both source and sink must exclude RemoteFlowSource and built-in sink classes."""
        from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        source = self._make_source(file_path="/views.py", line=10)
        sink = self._make_sink(file_path="/models.py", line=99)
        cfg = get_default_taint_config()
        focused = cfg.model_copy(update={
            "sources": CodeQL._build_source_configs([source]),
            "sinks": CodeQL._build_sink_configs([sink]),
            "include_remote_flow_source": False,
            "disabled_builtin_sinks": TaintQueryGenerator.builtin_sink_names(),
        })
        query = TaintQueryGenerator.generate_query(focused)
        assert 'getAbsolutePath() = "/views.py"' in query
        assert "getStartLine() = 10" in query
        assert 'getAbsolutePath() = "/models.py"' in query
        assert "getStartLine() = 99" in query
        assert "node instanceof RemoteFlowSource" not in query
        assert "node instanceof SqlInjection::Sink" not in query

    # ---- _build_source_configs / _build_sink_configs ------------------

    def test_build_source_configs_groups_by_source_type(self):
        """PyTaintSource items with the same source_type share one TaintSourceConfig."""
        from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
        s1 = self._make_source(file_path="/a.py", line=1, source_type="web_request")
        s2 = self._make_source(file_path="/b.py", line=2, source_type="web_request")
        s3 = self._make_source(file_path="/c.py", line=3, source_type="user_input")
        configs = CodeQL._build_source_configs([s1, s2, s3])
        assert len(configs) == 2
        web_cfg = next(c for c in configs if c.source_type == "web_request")
        assert len(web_cfg.locations) == 2

    def test_build_source_configs_preserves_column(self):
        """Column information from PyTaintSource.call_site must be carried into TaintNodeRef."""
        from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
        source = self._make_source(file_path="/app.py", line=5, col=12)
        configs = CodeQL._build_source_configs([source])
        assert configs[0].locations[0].start_column == 12

    def test_build_source_configs_taint_node_ref_labelled_pinned_source(self):
        """Raw TaintNodeRef items are labelled 'pinned_source' in the config."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
        ref = TaintNodeRef(file_path="/x.py", start_line=99)
        configs = CodeQL._build_source_configs([ref])
        assert configs[0].source_type == "pinned_source"

    def test_build_sink_configs_groups_by_type_triple(self):
        """PyTaintSink items with the same (sink_type, vuln_type, severity) share one config."""
        from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
        s1 = self._make_sink(file_path="/db1.py", line=1)
        s2 = self._make_sink(file_path="/db2.py", line=2)
        configs = CodeQL._build_sink_configs([s1, s2])
        assert len(configs) == 1
        assert len(configs[0].locations) == 2

    def test_build_sink_configs_fallback_vulnerability_type(self):
        """When PyTaintSink.vulnerability_type is None, sink_type is used as the fallback."""
        from codeanalyzer.schema.py_schema import PyTaintSink, PyCallsite
        from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
        sink = PyTaintSink(
            sink_type="sql_execution",
            severity="high",
            vulnerability_type=None,
            call_site=PyCallsite(method_name="execute", start_line=5, end_line=5, file_path="/x.py"),
        )
        configs = CodeQL._build_sink_configs([sink])
        assert configs[0].vulnerability_type == "sql_execution"



class TestFocusedTaintAPIs_Integration:
    """Integration tests for the three focused taint APIs using the flask_app fixture.

    The flask_app contains SQL injection, command injection, path traversal, XSS,
    and SSTI — all originating from web_request (Flask request.args / URL params).

    Each focused test first runs analyze_taint_flows() to obtain real
    PyTaintSource / PyTaintSink instances with populated file_path fields,
    then calls the focused API with those concrete callsite objects.
    """

    def _skip_if_no_codeql(self, codeql_packs_dir):
        import shutil
        if not shutil.which("codeql"):
            pytest.skip("CodeQL not available")
        if codeql_packs_dir is None:
            pytest.skip("CodeQL packs not available")

    def test_analyze_from_sources_singular_returns_flows_for_that_source(
        self, flask_codeql, flask_full_taint_result, codeql_packs_dir
    ):
        """analyze_taint_flows_from_sources([source]) must return only flows from that callsite."""
        self._skip_if_no_codeql(codeql_packs_dir)
        assert len(flask_full_taint_result.flows) >= 1, "Full analysis must find at least one flow"
        first_source = flask_full_taint_result.flows[0].source
        assert first_source.call_site.file_path, "file_path must be set"

        focused = flask_codeql.analyze_taint_flows_from_sources(
            [first_source], config_override=get_default_taint_config()
        )
        assert len(focused.flows) >= 1
        src_key = (first_source.call_site.file_path, first_source.call_site.start_line)
        assert all(
            (f.source.call_site.file_path, f.source.call_site.start_line) == src_key
            for f in focused.flows
        ), "All focused flows must originate from the pinned source call site"

    def test_analyze_to_sinks_singular_returns_flows_for_that_sink(
        self, flask_codeql, flask_full_taint_result, codeql_packs_dir
    ):
        """analyze_taint_flows_to_sinks([sink]) must return only flows reaching that callsite."""
        self._skip_if_no_codeql(codeql_packs_dir)
        sql_flow = next(
            (f for f in flask_full_taint_result.flows if f.vulnerability_type == "SQL Injection"),
            None,
        )
        assert sql_flow is not None, "flask_app must have at least one SQL Injection flow"
        target_sink = sql_flow.sink

        focused = flask_codeql.analyze_taint_flows_to_sinks(
            [target_sink], config_override=get_default_taint_config()
        )
        assert len(focused.flows) >= 1
        snk_key = (target_sink.call_site.file_path, target_sink.call_site.start_line)
        assert all(
            (f.sink.call_site.file_path, f.sink.call_site.start_line) == snk_key
            for f in focused.flows
        ), "All focused flows must reach the pinned sink call site"

    def test_analyze_flow_paths_returns_flows_between_source_and_sink(
        self, flask_codeql, flask_full_taint_result, codeql_packs_dir
    ):
        """analyze_taint_flow_paths([source], [sink]) must return only flows between those callsites."""
        self._skip_if_no_codeql(codeql_packs_dir)
        sql_flow = next(
            (f for f in flask_full_taint_result.flows if f.vulnerability_type == "SQL Injection"),
            None,
        )
        assert sql_flow is not None
        pinned_source, pinned_sink = sql_flow.source, sql_flow.sink

        focused = flask_codeql.analyze_taint_flow_paths(
            [pinned_source], [pinned_sink], config_override=get_default_taint_config()
        )
        assert len(focused.flows) >= 1
        src_key = (pinned_source.call_site.file_path, pinned_source.call_site.start_line)
        snk_key = (pinned_sink.call_site.file_path, pinned_sink.call_site.start_line)
        for f in focused.flows:
            assert (f.source.call_site.file_path, f.source.call_site.start_line) == src_key
            assert (f.sink.call_site.file_path, f.sink.call_site.start_line) == snk_key

    def test_analyze_from_sources_taint_node_ref_accepted(
        self, flask_codeql, flask_full_taint_result, codeql_packs_dir
    ):
        """analyze_taint_flows_from_sources must accept TaintNodeRef without bootstrapping."""
        self._skip_if_no_codeql(codeql_packs_dir)
        from codeanalyzer.schema.py_schema import TaintNodeRef
        first_source = flask_full_taint_result.flows[0].source
        ref = TaintNodeRef(
            file_path=first_source.call_site.file_path,
            start_line=first_source.call_site.start_line,
        )
        focused = flask_codeql.analyze_taint_flows_from_sources(
            [ref], config_override=get_default_taint_config()
        )
        assert len(focused.flows) >= 1
        src_key = (first_source.call_site.file_path, first_source.call_site.start_line)
        assert all(
            (f.source.call_site.file_path, f.source.call_site.start_line) == src_key
            for f in focused.flows
        ), "TaintNodeRef must pin correctly without a full PyTaintSource"


# ============================================================================
# New coverage additions
# ============================================================================

class TestQueryGeneratorInternals:
    """Unit tests for TaintQueryGenerator code paths not covered elsewhere.

    Focuses on the pattern-helper else-branches, location-based sanitizer
    predicates, empty-predicate guards, config-sig structure, special
    characters in file paths, and the sanitizer predicate presence toggle.
    """

    # ------------------------------------------------------------------
    # A1-A5: Pattern-helper else-branches
    # ------------------------------------------------------------------

    def test_pattern_to_source_node_non_getcall_appends_assource(self):
        """Pattern without .getACall() suffix must be returned with .asSource() appended."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        pattern = 'API::moduleImport("flask").getMember("request").getMember("args")'
        result = TaintQueryGenerator._pattern_to_source_node(pattern)
        assert result == f"{pattern}.asSource()"

    def test_pattern_to_sink_node_non_getcall_uses_getparameter_assink(self):
        """Pattern without .getACall() must use .getParameter(N).asSink() directly."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        pattern = 'API::moduleImport("sqlite3").getMember("execute")'
        result = TaintQueryGenerator._pattern_to_sink_node(pattern, 0)
        assert result == f"{pattern}.getParameter(0).asSink()"

    def test_pattern_to_default_sink_node_non_getcall_uses_assink(self):
        """Pattern without .getACall() and no argument_index must use .asSink()."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        pattern = 'API::moduleImport("sqlite3").getMember("execute")'
        result = TaintQueryGenerator._pattern_to_default_sink_node(pattern)
        assert result == f"{pattern}.asSink()"

    def test_pattern_to_default_sink_node_getcall_uses_getanarg(self):
        """Pattern ending in .getACall() and no argument_index must use .getAnArg()."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        pattern = 'API::moduleImport("foo").getMember("bar").getACall()'
        result = TaintQueryGenerator._pattern_to_default_sink_node(pattern)
        assert ".getAnArg()" in result
        assert ".asSink()" not in result

    def test_pattern_to_sanitizer_node_non_getcall_appends_assource(self):
        """Pattern without .getACall() must be returned with .asSource() appended."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        pattern = 'API::moduleImport("html").getMember("escape")'
        result = TaintQueryGenerator._pattern_to_sanitizer_node(pattern)
        assert result == f"{pattern}.asSource()"

    # ------------------------------------------------------------------
    # A4 in generated query: argument_index=None + .getACall() → .getAnArg()
    # ------------------------------------------------------------------

    def test_sink_predicate_argument_index_none_generates_getanarg_in_query(self):
        """`argument_index=None` with a .getACall() pattern must produce .getAnArg() in the query."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSinkConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        config = TaintAnalysisConfig(
            sinks=[TaintSinkConfig(
                name="s",
                description="d",
                pattern='API::moduleImport("foo").getMember("bar").getACall()',
                sink_type="foo_sink",
                vulnerability_type="Test Vuln",
                severity="low",
                argument_index=None,
            )]
        )
        query = TaintQueryGenerator.generate_query(config)
        assert ".getAnArg()" in query

    # ------------------------------------------------------------------
    # A6: Location-based sanitizer clause
    # ------------------------------------------------------------------

    def test_location_sanitizer_clause_in_query(self):
        """TaintSanitizerConfig with locations must embed file/line in the generated query."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, TaintSanitizerConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        ref = TaintNodeRef(file_path="/abs/san.py", start_line=15)
        config = get_default_taint_config().model_copy(update={
            "sanitizers": [TaintSanitizerConfig(
                name="loc_san", description="d", locations=[ref], sanitizes=["xss"],
            )]
        })
        query = TaintQueryGenerator.generate_query(config)
        assert "predicate isConfiguredSanitizer" in query
        assert 'getAbsolutePath() = "/abs/san.py"' in query
        assert "getStartLine() = 15" in query

    def test_location_sanitizer_with_column_adds_column_constraint(self):
        """Location-based sanitizer with start_column >= 0 must add getStartColumn()."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, TaintSanitizerConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        ref = TaintNodeRef(file_path="/abs/san.py", start_line=15, start_column=4)
        config = get_default_taint_config().model_copy(update={
            "sanitizers": [TaintSanitizerConfig(
                name="loc_san", description="d", locations=[ref], sanitizes=["xss"],
            )]
        })
        query = TaintQueryGenerator.generate_query(config)
        assert "getStartColumn() = 4" in query

    # ------------------------------------------------------------------
    # A7-A8: Empty-predicate guards
    # ------------------------------------------------------------------

    def test_empty_source_config_with_remote_disabled_generates_none(self):
        """sources=[] + include_remote_flow_source=False must produce none() in source predicate."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        config = TaintAnalysisConfig(sources=[], include_remote_flow_source=False)
        query = TaintQueryGenerator.generate_query(config)
        src_pred = query.split("predicate isConfiguredSource")[1].split("predicate isConfiguredSink")[0]
        assert "none()" in src_pred
        assert "RemoteFlowSource" not in src_pred

    def test_all_builtin_sinks_disabled_no_user_sinks_generates_none(self):
        """All builtins disabled + empty user sinks must produce none() in sink predicate."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        config = get_default_taint_config().model_copy(update={
            "sinks": [],
            "disabled_builtin_sinks": TaintQueryGenerator.builtin_sink_names(),
        })
        query = TaintQueryGenerator.generate_query(config)
        sink_pred = query.split("predicate isConfiguredSink")[1].split("private module")[0]
        assert "none()" in sink_pred

    # ------------------------------------------------------------------
    # A9: ConfigSig with and without isBarrier
    # ------------------------------------------------------------------

    def test_config_sig_with_sanitizers_includes_isbarrier(self):
        """_generate_config_sig(has_sanitizers=True) must include isBarrier block."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        block = TaintQueryGenerator._generate_config_sig(has_sanitizers=True)
        assert "isBarrier" in block
        assert "isConfiguredSanitizer" in block

    def test_config_sig_without_sanitizers_excludes_isbarrier(self):
        """_generate_config_sig(has_sanitizers=False) must not include isBarrier."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        block = TaintQueryGenerator._generate_config_sig(has_sanitizers=False)
        assert "isBarrier" not in block
        assert "isConfiguredSanitizer" not in block

    # ------------------------------------------------------------------
    # A10: Special characters in file paths
    # ------------------------------------------------------------------

    def test_location_clause_escapes_backslash_in_path(self):
        """Backslashes in a TaintNodeRef file_path must be doubled in the generated QL string."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, TaintSourceConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        win_path = r"C:\Users\dev\app.py"
        ref = TaintNodeRef(file_path=win_path, start_line=1)
        config = get_default_taint_config().model_copy(update={
            "sources": [TaintSourceConfig(
                name="win", description="d", source_type="t", locations=[ref],
            )],
            "include_remote_flow_source": False,
        })
        query = TaintQueryGenerator.generate_query(config)
        # Each \ must appear as \\ in the QL string literal
        assert win_path.replace("\\", "\\\\") in query

    def test_location_clause_escapes_double_quote_in_path(self):
        """Double quotes in a TaintNodeRef file_path must be escaped in the QL string."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, TaintSourceConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        quoted_path = '/abs/my"app.py'
        ref = TaintNodeRef(file_path=quoted_path, start_line=1)
        config = get_default_taint_config().model_copy(update={
            "sources": [TaintSourceConfig(
                name="q", description="d", source_type="t", locations=[ref],
            )],
            "include_remote_flow_source": False,
        })
        query = TaintQueryGenerator.generate_query(config)
        assert '\\"' in query

    # ------------------------------------------------------------------
    # A11: Sanitizer predicate presence toggle
    # ------------------------------------------------------------------

    def test_generate_query_includes_sanitizer_predicate_when_sanitizers_present(self):
        """Full query must contain predicate isConfiguredSanitizer when sanitizers are configured."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        # Default config has 6 sanitizers
        assert len(get_default_taint_config().sanitizers) > 0
        query = TaintQueryGenerator.generate_query(get_default_taint_config())
        assert "predicate isConfiguredSanitizer" in query

    def test_generate_query_excludes_sanitizer_predicate_when_no_sanitizers(self):
        """Full query must NOT contain predicate isConfiguredSanitizer when sanitizers list is empty."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        config = TaintAnalysisConfig(sanitizers=[])
        query = TaintQueryGenerator.generate_query(config)
        assert "predicate isConfiguredSanitizer" not in query


class TestFocusedTaintAPIs_ErrorPaths:
    """Unit tests for error-path and input-shape behavior of the focused taint APIs.

    None of these tests require CodeQL — they only exercise argument validation
    and the _build_*_configs helpers.
    """

    # ------------------------------------------------------------------
    # B3-B5: ValueError guards
    # ------------------------------------------------------------------

    def test_analyze_taint_flows_raises_when_no_config(self):
        """analyze_taint_flows() with no config on the instance and no override must raise."""
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        with pytest.raises(ValueError, match="No taint configuration"):
            codeql.analyze_taint_flows()

    def test_analyze_from_sources_raises_on_empty_list(self):
        """analyze_taint_flows_from_sources([]) must raise ValueError immediately."""
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        with pytest.raises(ValueError, match="empty"):
            codeql.analyze_taint_flows_from_sources([])

    def test_analyze_to_sinks_raises_on_empty_list(self):
        """analyze_taint_flows_to_sinks([]) must raise ValueError immediately."""
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        with pytest.raises(ValueError, match="empty"):
            codeql.analyze_taint_flows_to_sinks([])

    def test_analyze_flow_paths_raises_on_empty_sources(self):
        """analyze_taint_flow_paths with empty sources list must raise ValueError."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        with pytest.raises(ValueError, match="empty"):
            codeql.analyze_taint_flow_paths(
                sources=[],
                sinks=[TaintNodeRef(file_path="/x.py", start_line=1)],
            )

    def test_analyze_flow_paths_raises_on_empty_sinks(self):
        """analyze_taint_flow_paths with empty sinks list must raise ValueError."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        with pytest.raises(ValueError, match="empty"):
            codeql.analyze_taint_flow_paths(
                sources=[TaintNodeRef(file_path="/x.py", start_line=1)],
                sinks=[],
            )

    def test_analyze_from_sources_raises_when_no_config(self):
        """analyze_taint_flows_from_sources with a non-empty list but no config must raise."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        ref = TaintNodeRef(file_path="/x.py", start_line=1)
        with pytest.raises(ValueError, match="No taint configuration"):
            codeql.analyze_taint_flows_from_sources([ref])

    def test_analyze_to_sinks_raises_when_no_config(self):
        """analyze_taint_flows_to_sinks with a non-empty list but no config must raise."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        ref = TaintNodeRef(file_path="/db.py", start_line=1)
        with pytest.raises(ValueError, match="No taint configuration"):
            codeql.analyze_taint_flows_to_sinks([ref])

    # ------------------------------------------------------------------
    # B6-B8: Mixed-input shapes for _build_*_configs
    # ------------------------------------------------------------------

    def test_build_source_configs_mixed_types_produces_separate_entries(self):
        """A list with both PyTaintSource and TaintNodeRef must produce two TaintSourceConfig entries."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, PyTaintSource, PyCallsite
        src = PyTaintSource(
            source_type="web_request",
            call_site=PyCallsite(method_name="req", start_line=5, end_line=5, file_path="/a.py"),
        )
        ref = TaintNodeRef(file_path="/b.py", start_line=10)
        configs = CodeQL._build_source_configs([src, ref])
        source_types = {c.source_type for c in configs}
        assert "web_request" in source_types
        assert "pinned_source" in source_types
        all_locs = [loc for c in configs for loc in c.locations]
        assert any(loc.file_path == "/a.py" for loc in all_locs)
        assert any(loc.file_path == "/b.py" for loc in all_locs)

    def test_build_sink_configs_taint_node_ref_labelled_pinned_sink(self):
        """A bare TaintNodeRef passed to _build_sink_configs must produce sink_type='pinned_sink'."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        ref = TaintNodeRef(file_path="/db.py", start_line=42)
        configs = CodeQL._build_sink_configs([ref])
        assert len(configs) == 1
        assert configs[0].sink_type == "pinned_sink"
        assert configs[0].severity == "medium"

    def test_build_sink_configs_mixed_types_produces_separate_entries(self):
        """A list with both PyTaintSink and TaintNodeRef must produce two TaintSinkConfig entries."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, PyTaintSink, PyCallsite
        sink = PyTaintSink(
            sink_type="sql_execution", severity="critical",
            vulnerability_type="SQL Injection",
            call_site=PyCallsite(method_name="execute", start_line=5, end_line=5, file_path="/db.py"),
        )
        ref = TaintNodeRef(file_path="/other.py", start_line=10)
        configs = CodeQL._build_sink_configs([sink, ref])
        sink_types = {c.sink_type for c in configs}
        assert "sql_execution" in sink_types
        assert "pinned_sink" in sink_types

    # ------------------------------------------------------------------
    # B9: Singular wrappers delegate to plural
    # ------------------------------------------------------------------

    def test_singular_from_source_delegates_to_plural(self, monkeypatch):
        """analyze_taint_flows_from_source(x) must call analyze_taint_flows_from_sources([x])."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, PyTaintAnalysisResult
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        calls = []

        def mock_plural(sources, config_override=None, symbol_table=None):
            calls.append(list(sources))
            return PyTaintAnalysisResult(project_path="x", flows=[])

        monkeypatch.setattr(codeql, "analyze_taint_flows_from_sources", mock_plural)
        ref = TaintNodeRef(file_path="/x.py", start_line=1)
        codeql.analyze_taint_flows_from_source(ref)
        assert calls == [[ref]]

    def test_singular_to_sink_delegates_to_plural(self, monkeypatch):
        """analyze_taint_flows_to_sink(x) must call analyze_taint_flows_to_sinks([x])."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, PyTaintAnalysisResult
        codeql = CodeQL(project_dir=Path("/fake"), db_path=Path("/fake.db"))
        calls = []

        def mock_plural(sinks, config_override=None, symbol_table=None):
            calls.append(list(sinks))
            return PyTaintAnalysisResult(project_path="x", flows=[])

        monkeypatch.setattr(codeql, "analyze_taint_flows_to_sinks", mock_plural)
        ref = TaintNodeRef(file_path="/db.py", start_line=1)
        codeql.analyze_taint_flows_to_sink(ref)
        assert calls == [[ref]]


class TestConfigLoaderEdgeCases:
    """Unit tests for TaintConfigLoader code paths not covered by TestTaintConfigExtensibility.

    Covers: duplicate sink/sanitizer names, empty sink/sanitizer patterns,
    no-sinks warning, name-collision overrides, include_remote_flow_source
    carry-through, disabled sanitizers, file-not-found, unsupported extension,
    invalid JSON, invalid Pydantic structure, save_config round-trips, and
    edge-case YAML shapes.
    """

    # ------------------------------------------------------------------
    # C1-C3: validate_config gaps
    # ------------------------------------------------------------------

    def test_validate_config_warns_duplicate_sink_names(self):
        """validate_config must report duplicate sink names."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSinkConfig
        sink = TaintSinkConfig(
            name="dup", description="d",
            pattern='API::moduleImport("sqlite3").getMember("execute").getACall()',
            sink_type="sql_execution", vulnerability_type="SQL Injection", severity="critical",
        )
        config = TaintAnalysisConfig(sinks=[sink, sink])
        issues = TaintConfigLoader.validate_config(config)
        assert any("Duplicate" in i and "sink" in i.lower() for i in issues)

    def test_validate_config_warns_duplicate_sanitizer_names(self):
        """validate_config must report duplicate sanitizer names."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSanitizerConfig
        san = TaintSanitizerConfig(
            name="dup", description="d",
            pattern='API::moduleImport("html").getMember("escape").getACall()',
        )
        config = TaintAnalysisConfig(sanitizers=[san, san])
        issues = TaintConfigLoader.validate_config(config)
        assert any("Duplicate" in i and "sanitizer" in i.lower() for i in issues)

    def test_validate_config_warns_empty_pattern_for_sink(self):
        """validate_config must report blank patterns for sinks."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSinkConfig
        config = TaintAnalysisConfig(
            sinks=[TaintSinkConfig(
                name="bad", description="d", pattern="   ",
                sink_type="sql_execution", vulnerability_type="SQL", severity="critical",
            )]
        )
        issues = TaintConfigLoader.validate_config(config)
        assert any("Empty pattern" in i and "sink" in i.lower() for i in issues)

    def test_validate_config_warns_empty_pattern_for_sanitizer(self):
        """validate_config must report blank patterns for sanitizers."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSanitizerConfig
        config = TaintAnalysisConfig(
            sanitizers=[TaintSanitizerConfig(name="bad", description="d", pattern="   ")]
        )
        issues = TaintConfigLoader.validate_config(config)
        assert any("Empty pattern" in i and "sanitizer" in i.lower() for i in issues)

    def test_validate_config_warns_no_sinks(self):
        """validate_config must warn when no user-defined or built-in sinks remain."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        config = TaintAnalysisConfig(sources=[], sinks=[], sanitizers=[])
        issues = TaintConfigLoader.validate_config(config)
        assert any("No taint sinks" in i for i in issues)

    # ------------------------------------------------------------------
    # C4-C5: _merge_configs name-collision and include_remote_flow_source
    # ------------------------------------------------------------------

    def test_merge_source_name_collision_custom_wins(self):
        """A custom source with the same name as a base source must replace the base entry."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSourceConfig
        base_src = TaintSourceConfig(
            name="x", description="base",
            pattern='API::builtin("a").getACall()', source_type="base_type",
        )
        custom_src = TaintSourceConfig(
            name="x", description="custom",
            pattern='API::builtin("b").getACall()', source_type="custom_type",
        )
        merged = TaintConfigLoader._merge_configs(
            TaintAnalysisConfig(sources=[base_src]),
            TaintAnalysisConfig(sources=[custom_src]),
        )
        colliding = [s for s in merged.sources if s.name == "x"]
        assert len(colliding) == 1
        assert colliding[0].source_type == "custom_type"

    def test_merge_sink_name_collision_custom_wins(self):
        """A custom sink with the same name as a base sink must replace the base entry."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSinkConfig
        base_sk = TaintSinkConfig(
            name="y", description="base",
            pattern='API::builtin("a").getACall()',
            sink_type="base_sink", vulnerability_type="Base", severity="low",
        )
        custom_sk = TaintSinkConfig(
            name="y", description="custom",
            pattern='API::builtin("b").getACall()',
            sink_type="custom_sink", vulnerability_type="Custom", severity="critical",
        )
        merged = TaintConfigLoader._merge_configs(
            TaintAnalysisConfig(sinks=[base_sk]),
            TaintAnalysisConfig(sinks=[custom_sk]),
        )
        colliding = [s for s in merged.sinks if s.name == "y"]
        assert len(colliding) == 1
        assert colliding[0].sink_type == "custom_sink"

    def test_merge_include_remote_flow_source_custom_wins(self):
        """include_remote_flow_source=False in custom must override True in base."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig
        base = TaintAnalysisConfig(include_remote_flow_source=True)
        custom = TaintAnalysisConfig(include_remote_flow_source=False)
        merged = TaintConfigLoader._merge_configs(base, custom)
        assert merged.include_remote_flow_source is False

    # ------------------------------------------------------------------
    # C6: _filter_disabled sanitizer branch
    # ------------------------------------------------------------------

    def test_filter_disabled_removes_disabled_sanitizers(self):
        """_filter_disabled must strip sanitizers whose enabled field is False."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSanitizerConfig
        san = TaintSanitizerConfig(
            name="san", description="d",
            pattern='API::builtin("x").getACall()',
            enabled=False,
        )
        config = TaintAnalysisConfig(sanitizers=[san])
        filtered = TaintConfigLoader._filter_disabled(config)
        assert len(filtered.sanitizers) == 0

    def test_filter_disabled_keeps_enabled_sanitizers(self):
        """_filter_disabled must retain sanitizers whose enabled field is True."""
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSanitizerConfig
        san = TaintSanitizerConfig(
            name="san", description="d",
            pattern='API::builtin("x").getACall()',
            enabled=True,
        )
        config = TaintAnalysisConfig(sanitizers=[san])
        filtered = TaintConfigLoader._filter_disabled(config)
        assert len(filtered.sanitizers) == 1

    # ------------------------------------------------------------------
    # C7-C10: _load_from_file error paths
    # ------------------------------------------------------------------

    def test_load_config_raises_file_not_found(self, tmp_path):
        """load_config with a path to a non-existent file must raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            TaintConfigLoader.load_config(tmp_path / "does_not_exist.yaml")

    def test_load_config_raises_for_unsupported_extension(self, tmp_path):
        """load_config with a .toml file must raise ValueError mentioning supported formats."""
        f = tmp_path / "config.toml"
        f.write_text("[sources]\n")
        with pytest.raises(ValueError, match="Unsupported"):
            TaintConfigLoader.load_config(f)

    def test_load_config_raises_for_invalid_json(self, tmp_path):
        """load_config with a malformed JSON file must raise ValueError."""
        f = tmp_path / "config.json"
        f.write_text("{invalid json}")
        with pytest.raises(ValueError, match="Invalid JSON"):
            TaintConfigLoader.load_config(f)

    def test_load_config_raises_for_invalid_pydantic_structure(self, tmp_path):
        """load_config with well-formed YAML but wrong structure must raise ValueError."""
        f = tmp_path / "config.yaml"
        f.write_text("sources: not_a_list\n")  # sources must be a list
        with pytest.raises(ValueError, match="Invalid taint configuration"):
            TaintConfigLoader.load_config(f)

    # ------------------------------------------------------------------
    # C11: save_config round-trips
    # ------------------------------------------------------------------

    def test_save_config_round_trip_yaml(self, tmp_path):
        """save_config + load_config round-trip via YAML must preserve all config values."""
        config = get_default_taint_config()
        out = tmp_path / "config.yaml"
        TaintConfigLoader.save_config(config, out, format="yaml")
        loaded = TaintConfigLoader.load_config(out, use_defaults=False)
        assert len(loaded.sources) == len(config.sources)
        assert len(loaded.sanitizers) == len(config.sanitizers)
        assert loaded.max_path_length == config.max_path_length
        assert loaded.confidence_threshold == config.confidence_threshold

    def test_save_config_round_trip_json(self, tmp_path):
        """save_config + load_config round-trip via JSON must preserve all config values."""
        config = get_default_taint_config()
        out = tmp_path / "config.json"
        TaintConfigLoader.save_config(config, out, format="json")
        loaded = TaintConfigLoader.load_config(out, use_defaults=False)
        assert len(loaded.sources) == len(config.sources)
        assert loaded.confidence_threshold == config.confidence_threshold
        assert loaded.include_remote_flow_source == config.include_remote_flow_source

    def test_save_config_raises_for_unsupported_format(self, tmp_path):
        """save_config with an unsupported format string must raise ValueError."""
        config = get_default_taint_config()
        with pytest.raises(ValueError, match="Unsupported"):
            TaintConfigLoader.save_config(config, tmp_path / "out.toml", format="toml")

    # ------------------------------------------------------------------
    # F1-F4: YAML edge cases
    # ------------------------------------------------------------------

    def test_load_config_accepts_yml_extension(self, tmp_path):
        """Files with .yml extension must be parsed as YAML."""
        f = tmp_path / "config.yml"
        f.write_text("max_path_length: 5\n")
        config = TaintConfigLoader.load_config(f, use_defaults=False)
        assert config.max_path_length == 5

    def test_disabled_builtin_sink_unknown_name_silently_ignored(self):
        """An unrecognised name in disabled_builtin_sinks must leave all builtins intact."""
        from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
        config = get_default_taint_config().model_copy(update={
            "disabled_builtin_sinks": ["FakeSink::Sink"],
        })
        query = TaintQueryGenerator.generate_query(config)
        # All real builtins still present
        assert "SqlInjection::Sink" in query
        assert "CommandInjection::Sink" in query

    def test_config_with_scalar_overrides_only_preserves_default_sources(self, tmp_path):
        """A YAML file with only scalar overrides merged with defaults keeps default sources."""
        f = tmp_path / "scalars.yaml"
        f.write_text("max_path_length: 15\nconfidence_threshold: high\n")
        config = TaintConfigLoader.load_config(f, use_defaults=True)
        assert config.max_path_length == 15
        assert config.confidence_threshold == "high"
        assert len(config.sources) > 0  # defaults preserved


class TestSchemaValidatorEdgeCases:
    """Unit tests for TaintNodeRef, config-entry validators, and schema model field coverage."""

    # ------------------------------------------------------------------
    # D1-D2: Both pattern + locations accepted simultaneously
    # ------------------------------------------------------------------

    def test_source_config_accepts_both_pattern_and_locations(self):
        """TaintSourceConfig with both pattern and locations must be valid."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, TaintSourceConfig
        ref = TaintNodeRef(file_path="/app.py", start_line=1)
        sc = TaintSourceConfig(
            name="x", description="d", source_type="t",
            pattern='API::builtin("input").getACall()',
            locations=[ref],
        )
        assert sc.pattern is not None
        assert len(sc.locations) == 1

    def test_sink_config_accepts_both_pattern_and_locations(self):
        """TaintSinkConfig with both pattern and locations must be valid."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, TaintSinkConfig
        ref = TaintNodeRef(file_path="/db.py", start_line=1)
        sk = TaintSinkConfig(
            name="x", description="d",
            sink_type="sql_execution", vulnerability_type="SQL Injection", severity="critical",
            pattern='API::moduleImport("sqlite3").getMember("execute").getACall()',
            locations=[ref],
        )
        assert sk.pattern is not None
        assert len(sk.locations) == 1

    # ------------------------------------------------------------------
    # D3-D4: TaintSanitizerConfig validator
    # ------------------------------------------------------------------

    def test_sanitizer_config_requires_pattern_or_locations(self):
        """TaintSanitizerConfig with neither pattern nor locations must raise ValueError."""
        from codeanalyzer.schema.py_schema import TaintSanitizerConfig
        with pytest.raises(ValueError):
            TaintSanitizerConfig(name="x", description="d", sanitizes=["xss"])

    def test_sanitizer_config_accepts_locations_only(self):
        """TaintSanitizerConfig with only a locations list (no pattern) must be valid."""
        from codeanalyzer.schema.py_schema import TaintNodeRef, TaintSanitizerConfig
        ref = TaintNodeRef(file_path="/san.py", start_line=5)
        san = TaintSanitizerConfig(name="x", description="d", locations=[ref], sanitizes=["xss"])
        assert san.pattern is None
        assert len(san.locations) == 1

    # ------------------------------------------------------------------
    # D5: TaintNodeRef boundary / optional column
    # ------------------------------------------------------------------

    def test_taint_node_ref_requires_absolute_file_path(self):
        """TaintNodeRef must raise ValueError for relative paths.

        The generated predicate uses getAbsolutePath() which always returns an
        absolute path — a relative path would never match and silently return
        zero results, so it is rejected at construction time.
        """
        from codeanalyzer.schema.py_schema import TaintNodeRef
        for bad in ("relative/path", "./local.py", "app.py"):
            with pytest.raises(ValueError, match="absolute"):
                TaintNodeRef(file_path=bad, start_line=1)
        # Absolute paths (Unix and Windows) must be accepted
        TaintNodeRef(file_path="/abs/path.py", start_line=1)
        TaintNodeRef(file_path=r"C:\Users\dev\app.py", start_line=1)

    def test_taint_node_ref_default_column_is_minus_one(self):
        """TaintNodeRef without start_column must default to -1 (no column constraint)."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        ref = TaintNodeRef(file_path="/x.py", start_line=10)
        assert ref.start_column == -1

    def test_taint_node_ref_accepts_zero_start_line(self):
        """TaintNodeRef places no positive-integer constraint on start_line."""
        from codeanalyzer.schema.py_schema import TaintNodeRef
        ref = TaintNodeRef(file_path="/x.py", start_line=0)
        assert ref.start_line == 0

    # ------------------------------------------------------------------
    # D6: PyTaintFlowStep field coverage
    # ------------------------------------------------------------------

    def test_taint_flow_step_all_fields_populated(self):
        """PyTaintFlowStep must store all optional fields when provided explicitly."""
        from codeanalyzer.schema.py_schema import PyTaintFlowStep
        step = PyTaintFlowStep(
            location="app.py:10:4",
            function_name="handler",
            start_line=10,
            end_line=10,
            start_column=4,
            end_column=20,
            expression="user_input",
            step_type="source",
            description="entry point",
        )
        assert step.location == "app.py:10:4"
        assert step.function_name == "handler"
        assert step.start_column == 4
        assert step.end_column == 20
        assert step.expression == "user_input"
        assert step.step_type == "source"
        assert step.description == "entry point"

    def test_taint_flow_step_optional_fields_default_correctly(self):
        """PyTaintFlowStep with only required fields must default optionals correctly."""
        from codeanalyzer.schema.py_schema import PyTaintFlowStep
        step = PyTaintFlowStep(location="x.py:1", function_name="f")
        assert step.start_line == -1
        assert step.end_line == -1
        assert step.start_column == -1
        assert step.expression is None
        assert step.step_type == "propagation"
        assert step.description is None

    def test_taint_analysis_result_optional_fields_round_trip(self):
        """PyTaintAnalysisResult must store and expose analysis_timestamp and codeql_database_path."""
        from codeanalyzer.schema.py_schema import PyTaintAnalysisResult
        result = PyTaintAnalysisResult(
            project_path="/proj",
            flows=[],
            analysis_timestamp="2025-01-01T12:00:00+00:00",
            codeql_database_path="/path/to/db",
        )
        assert result.analysis_timestamp == "2025-01-01T12:00:00+00:00"
        assert result.codeql_database_path == "/path/to/db"
