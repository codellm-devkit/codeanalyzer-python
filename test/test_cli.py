import json
import shutil
from pathlib import Path
import pytest
from codeanalyzer.__main__ import app
from codeanalyzer.utils import logger

_TAINT_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "taint_analysis"


def test_cli_help(cli_runner):
    """Must be able to run the CLI and see help output."""
    result = cli_runner.invoke(app, ["--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert result.exit_code == 0

def test_cli_call_symbol_table_with_json(cli_runner, whole_applications__xarray):
    """Must be able to run the CLI with symbol table analysis."""
    output_dir = whole_applications__xarray.joinpath("test", ".output")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = cli_runner.invoke(
        app,
        [
            "--input",
            str(whole_applications__xarray),
            "--output",
            str(output_dir),
            "--ray",
            "--no-codeql",
            "--cache-dir",
            str(whole_applications__xarray.joinpath("test", ".cache")),
            "--clear-cache",
            "--format=json",
        ],
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.exit_code == 0, "CLI command should succeed"
    assert Path(output_dir).joinpath("analysis.json").exists(), "Output JSON file should be created"
    json_obj = json.loads(Path(output_dir).joinpath("analysis.json").read_text())
    assert json_obj is not None, "JSON output should not be None"
    assert isinstance(json_obj, dict), "JSON output should be a dictionary"
    assert "symbol_table" in json_obj.keys(), "Symbol table should be present in the output"
    assert len(json_obj["symbol_table"]) > 0, "Symbol table should not be empty"


def test_single_file(cli_runner, single_functionalities__stuff_nested_in_functions):
    """Must be able to run the CLI with single file analysis using --file-name flag."""
    output_dir = single_functionalities__stuff_nested_in_functions.joinpath(".output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Path to the specific test file
    test_file = single_functionalities__stuff_nested_in_functions.joinpath("main.py")

    result = cli_runner.invoke(
        app,
        [
            "--input",
            str(single_functionalities__stuff_nested_in_functions),
            "--file-name",
            str(test_file),
            "--no-ray",
            "--clear-cache",
            "-vv",
            "--skip-tests",
            "--output",
            str(output_dir),
            "--eager",
            "--format=json",
        ],
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    
    assert result.exit_code == 0, f"CLI command should succeed. Output: {result.output}"
    assert Path(output_dir).joinpath("analysis.json").exists(), "Output JSON file should be created"
    
    # Load and validate the JSON output
    json_obj = json.loads(Path(output_dir).joinpath("analysis.json").read_text())
    assert json_obj is not None, "JSON output should not be None"
    assert isinstance(json_obj, dict), "JSON output should be a dictionary"
    assert "symbol_table" in json_obj.keys(), "Symbol table should be present in the output"


def test_cli_taint_analysis(cli_runner, tmp_path):
    """CLI with --analysis-level 3 --codeql must produce analysis.json with taint_analysis.

    Uses sql_injection_app which has 3 vulnerable cursor.execute() calls (direct concat,
    format string, f-string) plus sys.argv → execute. CodeQL's SqlInjection::Sink model
    detects all of them via the built-in model layer.
    """
    if not shutil.which("codeql"):
        pytest.skip("CodeQL CLI not available")

    sql_injection_app = _TAINT_FIXTURES_DIR / "sql_injection_app"
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = tmp_path / "cache"

    result = cli_runner.invoke(
        app,
        [
            "--input", str(sql_injection_app),
            "--output", str(output_dir),
            "--analysis-level", "3",
            "--codeql",
            "--no-ray",
            "--cache-dir", str(cache_dir),
            "--clear-cache",
            "--format=json",
        ],
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )

    assert result.exit_code == 0, (
        f"CLI command should succeed. Output:\n{result.output}"
    )

    analysis_file = output_dir / "analysis.json"
    assert analysis_file.exists(), "analysis.json should be created in the output directory"

    json_obj = json.loads(analysis_file.read_text())
    assert isinstance(json_obj, dict), "JSON output should be a dictionary"

    # --- Symbol table ---
    assert "symbol_table" in json_obj, "symbol_table must be present in analysis.json"
    assert len(json_obj["symbol_table"]) > 0, "symbol_table should not be empty"

    # --- Taint analysis top-level structure ---
    assert "taint_analysis" in json_obj, (
        "taint_analysis key must be present in analysis.json for --analysis-level 3"
    )
    taint = json_obj["taint_analysis"]
    assert taint is not None, "taint_analysis must not be null"
    for key in ("flows", "project_path"):
        assert key in taint, f"taint_analysis must contain '{key}'"
    assert "statistics" not in taint, "taint_analysis must not contain 'statistics' (field was removed)"
    assert "sources" not in taint, "taint_analysis must not contain top-level 'sources' (embedded in flows)"
    assert "sinks" not in taint, "taint_analysis must not contain top-level 'sinks' (embedded in flows)"

    # --- Flow count ---
    flows = taint["flows"]
    assert isinstance(flows, list), "taint_analysis.flows must be a list"
    assert len(flows) >= 6, (
        f"Expected at least 6 SQL injection flows from sql_injection_app, got {len(flows)}"
    )

    # --- All flows are SQL Injection ---
    sql_flows = [f for f in flows if f.get("vulnerability_type") == "SQL Injection"]
    assert len(sql_flows) >= 6, (
        f"Expected at least 6 SQL Injection flows, got {len(sql_flows)}"
    )

    # --- All SQL Injection flows are critical ---
    assert all(f["severity"] == "critical" for f in sql_flows), (
        "All SQL Injection flows must be critical severity"
    )

    # --- Each flow has required fields with valid values ---
    for flow in flows:
        assert flow.get("flow_id"), "Each flow must have a non-empty flow_id"
        assert flow.get("vulnerability_type"), "Each flow must have a vulnerability_type"
        assert flow["severity"] in ("critical", "high", "medium", "low"), (
            f"severity must be critical/high/medium/low, got {flow['severity']!r}"
        )
        assert flow.get("confidence") in ("high", "medium", "low"), (
            f"confidence must be high/medium/low, got {flow.get('confidence')!r}"
        )

        # Source fields — location/line info is now inside call_site
        source = flow.get("source", {})
        assert source.get("source_type"), "Flow source must have a non-empty source_type"
        source_cs = source.get("call_site", {})
        assert source_cs, "Flow source must have a call_site"
        assert isinstance(source_cs.get("start_line"), int) and source_cs["start_line"] > 0, (
            "Flow source.call_site.start_line must be a positive integer"
        )

        # Sink fields — location/line info is now inside call_site
        sink = flow.get("sink", {})
        assert sink.get("sink_type"), "Flow sink must have a non-empty sink_type"
        sink_cs = sink.get("call_site", {})
        assert sink_cs, "Flow sink must have a call_site"
        assert isinstance(sink_cs.get("start_line"), int) and sink_cs["start_line"] > 0, (
            "Flow sink.call_site.start_line must be a positive integer"
        )
        # All SQL injection sinks should be sql_execution type
        assert sink["sink_type"] == "sql_execution", (
            f"Expected sql_execution sink type, got {sink['sink_type']!r}"
        )

    # --- Severity consistency (derived from flows, no statistics field) ---
    n_critical = sum(1 for f in flows if f.get("severity") == "critical")
    assert n_critical >= 6, (
        f"Expected at least 6 critical flows, got {n_critical}"
    )
    # All severity values must sum to total flows
    severity_counts = {}
    for f in flows:
        sev = f.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    assert sum(severity_counts.values()) == len(flows), (
        "Sum of per-severity flow counts must equal total flows"
    )
