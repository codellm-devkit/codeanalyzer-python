import json
from pathlib import Path
from codeanalyzer.__main__ import app
from codeanalyzer.utils import logger


def test_cli_help(cli_runner):
    """Must be able to run the CLI and see help output."""
    result = cli_runner.invoke(app, ["--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert result.exit_code == 0

def test_cli_call_symbol_table_with_json(cli_runner, project_root):
    """Must be able to run the CLI with symbol table analysis."""
    output_dir = project_root.joinpath("test", ".output")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = cli_runner.invoke(
        app,
        [
            "--input",
            str(project_root),
            "--output",
            str(output_dir),
            "--analysis-level",
            "1",
            "--no-codeql",
            "--cache-dir",
            str(project_root.joinpath("test", ".cache")),
            "--keep-cache",
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
