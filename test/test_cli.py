import json
from pathlib import Path
from codeanalyzer.__main__ import app
from codeanalyzer.utils import logger


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
            "--analysis-level",
            "1",
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