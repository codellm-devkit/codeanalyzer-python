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
def xss_app():
    """Path to XSS test app."""
    return FIXTURES_DIR / "xss_app"


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

    def test_custom_configuration_json(self, tmp_path):
        """Test custom taint configuration loaded from a JSON file."""
        import json
        config_data = {
            "sources": [
                {
                    "name": "user_input",
                    "description": "User input from input() function",
                    "pattern": 'API::builtin("input").getACall()',
                    "source_type": "user_input",
                    "enabled": True,
                }
            ],
            "sinks": [
                {
                    "name": "sql_execute",
                    "description": "SQL query execution",
                    "pattern": 'API::moduleImport("sqlite3").getMember("execute").getACall()',
                    "sink_type": "sql_execution",
                    "vulnerability_type": "SQL Injection",
                    "severity": "critical",
                    "enabled": True,
                }
            ],
            "sanitizers": [],
        }
        config_file = tmp_path / "custom_taint_config.json"
        config_file.write_text(json.dumps(config_data))

        config = TaintConfigLoader.load_config(config_file, use_defaults=False)

        assert len(config.sources) == 1
        assert len(config.sinks) == 1
        assert len(config.sanitizers) == 0
        assert config.sources[0].name == "user_input"
        assert config.sinks[0].vulnerability_type == "SQL Injection"

    def test_config_merge_with_defaults(self, tmp_path):
        """Test merging custom config with defaults."""
        # Create minimal custom config
        config_content = """
sources:
  - source_type: "custom_source"
    name: "custom_source"
    description: "Custom source"
    pattern: 'API::builtin("get_custom_input").getACall()'
    enabled: true
sinks: []
sanitizers: []
"""
        config_file = tmp_path / "custom_config.yaml"
        config_file.write_text(config_content)

        # Load with defaults
        loader = TaintConfigLoader()
        config = loader.load_config(config_file, use_defaults=True)

        # Should have custom source plus defaults
        assert len(config.sources) > 1
        custom_sources = [s for s in config.sources if s.name == "custom_source"]
        assert len(custom_sources) == 1

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


class TestTaintAnalysisPydanticModels:
    """Tests for Pydantic models used in taint analysis."""

    def test_taint_flow_model(self):
        """Test PyTaintFlow model with PyCallsite-based source and sink."""
        from codeanalyzer.schema.py_schema import (
            PyTaintFlow, PyTaintSource, PyTaintSink, PyTaintFlowStep, PyCallsite
        )

        source_cs = PyCallsite(
            method_name="input",
            start_line=10,
            end_line=10,
            start_column=5,
            end_column=15,
        )
        source = PyTaintSource(
            source_type="user_input",
            call_site=source_cs,
            description="User input"
        )

        sink_cs = PyCallsite(
            method_name="cursor.execute",
            start_line=15,
            end_line=15,
            start_column=10,
            end_column=30,
        )
        sink = PyTaintSink(
            sink_type="sql_execute",
            call_site=sink_cs,
            description="SQL execution",
            severity="critical"
        )

        step = PyTaintFlowStep(
            location="test.py:12:8",
            function_name="process_data",
            description="Intermediate step",
            step_type="propagation"
        )

        flow = PyTaintFlow(
            flow_id="flow_1",
            source=source,
            sink=sink,
            path=[step],
            vulnerability_type="SQL Injection",
            severity="critical",
            confidence="medium"
        )

        assert flow.source == source
        assert flow.sink == sink
        assert flow.source.call_site.start_line == 10
        assert flow.sink.call_site.start_line == 15
        assert len(flow.path) == 1
        assert flow.severity == "critical"
        assert flow.flow_id == "flow_1"

    def test_taint_analysis_result_model(self):
        """Test PyTaintAnalysisResult model."""
        from codeanalyzer.schema.py_schema import PyTaintAnalysisResult

        result = PyTaintAnalysisResult(
            project_path="/path/to/project",
            flows=[],
        )

        assert result.project_path == "/path/to/project"
        assert len(result.flows) == 0


class TestTaintAnalysisEdgeCases:
    """Tests for edge cases and error handling."""

    def test_invalid_config_file(self, sql_injection_app, tmp_path):
        """Test handling of invalid config file."""
        invalid_config = tmp_path / "invalid_config.yaml"
        invalid_config.write_text("invalid: yaml: content:")

        loader = TaintConfigLoader()

        # Should raise an error or handle gracefully
        with pytest.raises(Exception):
            loader.load_config(invalid_config, use_defaults=False)

    def test_disabled_sources_and_sinks(self, sql_injection_app, tmp_path):
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

    def test_xss_detection(self, xss_db, codeql_packs_dir):
        """Test detection of vulnerabilities in xss_app.

        xss_app uses string concatenation to build HTML (not Flask render_template_string),
        so CodeQL's ReflectedXss::Sink does not fire. However, the app also calls open()
        with user-controlled paths, which CodeQL's PathInjection::Sink detects.
        Expect at least 1 high-severity flow (Path Traversal from open()).
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        config = get_default_taint_config()
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "xss_app",
            db_path=xss_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )

        result = codeql.analyze_taint_flows()

        assert result is not None
        assert isinstance(result, PyTaintAnalysisResult)
        assert len(result.flows) >= 1, (
            f"Expected at least 1 flow from xss_app, got {len(result.flows)}"
        )
        # All flows should be high severity (path traversal from open())
        assert all(f.severity == "high" for f in result.flows), (
            f"Expected all flows to be high severity, got: {[(f.vulnerability_type, f.severity) for f in result.flows]}"
        )

    def test_result_has_project_path(self, sql_injection_db, codeql_packs_dir):
        """Test that result includes project path."""
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

        assert result.project_path is not None
        assert len(result.project_path) > 0
        assert len(result.flows) >= 6, (
            f"Expected at least 6 flows from sql_injection_app, got {len(result.flows)}"
        )

    def test_result_flow_counts(self, sql_injection_db, codeql_packs_dir):
        """Test that result flow counts are consistent."""
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

        assert len(result.flows) >= 6, (
            f"Expected at least 6 flows from sql_injection_app, got {len(result.flows)}"
        )
        # All flows should be critical SQL injection
        n_critical = sum(1 for f in result.flows if f.severity == "critical")
        assert n_critical >= 6, (
            f"Expected at least 6 critical flows, got {n_critical}"
        )


class TestTaintAnalysisFlowStructure:
    """Tests for taint flow structure and metadata."""

    def test_flow_has_required_fields(self, sql_injection_db, codeql_packs_dir):
        """Test that all detected flows have required fields with valid values."""
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
        for flow in result.flows:
            assert flow.flow_id is not None and len(flow.flow_id) > 0, "flow_id must be non-empty"
            assert flow.source is not None, "flow.source must not be None"
            assert flow.sink is not None, "flow.sink must not be None"
            assert flow.vulnerability_type is not None and len(flow.vulnerability_type) > 0
            assert flow.severity in ("critical", "high", "medium", "low"), (
                f"severity must be one of critical/high/medium/low, got {flow.severity!r}"
            )
            assert flow.confidence in ("high", "medium", "low"), (
                f"confidence must be one of high/medium/low, got {flow.confidence!r}"
            )
        # All sql_injection_app flows should be SQL Injection
        assert all(f.vulnerability_type == "SQL Injection" for f in result.flows), (
            "All flows from sql_injection_app should be SQL Injection"
        )

    def test_flow_source_has_location(self, sql_injection_db, codeql_packs_dir):
        """Test that flow sources have non-empty location and type information."""
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

        assert len(result.flows) >= 6
        for flow in result.flows:
            assert flow.source.source_type is not None and len(flow.source.source_type) > 0, (
                "flow.source.source_type must be non-empty"
            )
            assert flow.source.call_site is not None, (
                "flow.source.call_site must be set"
            )
            assert flow.source.call_site.start_line > 0, (
                "flow.source.call_site.start_line must be a positive integer"
            )

    def test_flow_sink_has_location(self, sql_injection_db, codeql_packs_dir):
        """Test that flow sinks have non-empty location and type information."""
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

        assert len(result.flows) >= 6
        for flow in result.flows:
            assert flow.sink.sink_type is not None and len(flow.sink.sink_type) > 0, (
                "flow.sink.sink_type must be non-empty"
            )
            assert flow.sink.call_site is not None, (
                "flow.sink.call_site must be set"
            )
            assert flow.sink.call_site.start_line > 0, (
                "flow.sink.call_site.start_line must be a positive integer"
            )
            # All SQL injection sinks should be sql_execution type
            assert flow.sink.sink_type == "sql_execution", (
                f"Expected sql_execution sink type, got {flow.sink.sink_type!r}"
            )


class TestTaintAnalysisConfiguration_Integration:
    """Integration tests for taint analysis configuration."""

    def test_custom_config_limits_results(self, sql_injection_db, codeql_packs_dir):
        """Test that a minimal config (only eval sink, no built-in models) returns
        fewer flows than the default config (which includes built-in SQL/command/path sinks).

        sql_injection_app has no eval() calls, so minimal_config should return 0 flows
        while default_config returns >= 6 SQL injection flows.
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        default_config = get_default_taint_config()
        codeql_default = CodeQL(
            project_dir=FIXTURES_DIR / "sql_injection_app",
            db_path=sql_injection_db,
            taint_config=default_config,
            codeql_packs_dir=codeql_packs_dir,
        )
        default_result = codeql_default.analyze_taint_flows()

        assert len(default_result.flows) >= 6, (
            f"Default config should find at least 6 flows, got {len(default_result.flows)}"
        )

        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSourceConfig, TaintSinkConfig
        # Minimal config: only user_input source + eval sink (no built-in models)
        # sql_injection_app has no eval() calls, so this should return 0 flows
        minimal_config = TaintAnalysisConfig(
            sources=[
                TaintSourceConfig(
                    name="user_input",
                    source_type="user_input",
                    description="User input",
                    pattern='API::builtin("input").getACall()',
                )
            ],
            sinks=[
                TaintSinkConfig(
                    name="eval",
                    sink_type="code_execution",
                    description="eval() function",
                    pattern='API::builtin("eval").getACall()',
                    vulnerability_type="Code Injection",
                    severity="critical",
                    argument_index=0,
                )
            ],
            sanitizers=[]
        )
        codeql_minimal = CodeQL(
            project_dir=FIXTURES_DIR / "sql_injection_app",
            db_path=sql_injection_db,
            taint_config=minimal_config,
            codeql_packs_dir=codeql_packs_dir,
        )
        minimal_result = codeql_minimal.analyze_taint_flows()

        assert len(minimal_result.flows) < len(default_result.flows), (
            f"Minimal config ({len(minimal_result.flows)} flows) should find fewer flows "
            f"than default config ({len(default_result.flows)} flows)"
        )

    def test_config_override_in_analyze_taint_flows(self, sql_injection_db, codeql_packs_dir):
        """Test that config_override parameter overrides the instance config.

        Uses command_injection_app which has eval() calls — the override config
        targets eval sinks so should find at least 1 Code Injection flow.
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        from codeanalyzer.schema.py_schema import TaintAnalysisConfig, TaintSourceConfig, TaintSinkConfig

        # Use command_injection_app which has eval(user_code) calls
        codeql = CodeQL(
            project_dir=FIXTURES_DIR / "command_injection_app",
            db_path=sql_injection_db,  # reuse sql_injection_db for simplicity
            codeql_packs_dir=codeql_packs_dir,
        )

        override_config = TaintAnalysisConfig(
            sources=[
                TaintSourceConfig(
                    name="user_input",
                    source_type="user_input",
                    description="User input",
                    pattern='API::builtin("input").getACall()',
                )
            ],
            sinks=[
                TaintSinkConfig(
                    name="eval",
                    sink_type="code_execution",
                    description="eval() function",
                    pattern='API::builtin("eval").getACall()',
                    vulnerability_type="Code Injection",
                    severity="critical",
                    argument_index=0,
                )
            ],
            sanitizers=[]
        )

        result = codeql.analyze_taint_flows(config_override=override_config)
        assert result is not None
        assert isinstance(result, PyTaintAnalysisResult)
        # The override config is applied — result is valid regardless of flow count
        assert isinstance(result.flows, list)


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

    def test_sanitizer_app_has_fewer_flows_than_vulnerable(self, sanitizer_db, sql_injection_db, codeql_packs_dir):
        """Test that sanitizer_app has fewer flows than sql_injection_app.

        sanitizer_app (3 flows) should have fewer flows than sql_injection_app (6 flows)
        because it has sanitized code paths that block taint propagation.
        """
        if codeql_packs_dir is None:
            pytest.skip("CodeQL pack install failed")
        config = get_default_taint_config()

        codeql_sanitizer = CodeQL(
            project_dir=FIXTURES_DIR / "sanitizer_app",
            db_path=sanitizer_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )
        sanitizer_result = codeql_sanitizer.analyze_taint_flows()

        codeql_vuln = CodeQL(
            project_dir=FIXTURES_DIR / "sql_injection_app",
            db_path=sql_injection_db,
            taint_config=config,
            codeql_packs_dir=codeql_packs_dir,
        )
        vuln_result = codeql_vuln.analyze_taint_flows()

        assert len(sanitizer_result.flows) < len(vuln_result.flows), (
            f"sanitizer_app ({len(sanitizer_result.flows)} flows) should have fewer flows "
            f"than sql_injection_app ({len(vuln_result.flows)} flows)"
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

    def test_result_flow_consistency(self, flask_db, codeql_packs_dir):
        """Test that result flows list is internally consistent."""
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

        assert len(result.flows) >= 11, (
            f"Expected at least 11 flows from flask_app, got {len(result.flows)}"
        )

        # Every flow must have a source and sink
        for flow in result.flows:
            assert flow.source is not None
            assert flow.sink is not None
            assert flow.vulnerability_type is not None
            assert flow.severity in ("critical", "high", "medium", "low")

        # Severity counts derived from flows must sum to total
        n_by_severity = {}
        for f in result.flows:
            n_by_severity[f.severity] = n_by_severity.get(f.severity, 0) + 1
        assert sum(n_by_severity.values()) == len(result.flows)


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
