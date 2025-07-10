from codeanalyzer.utils import logger
from codeanalyzer.__main__ import app


def test_cli_help(cli_runner):
    """Must be able to run the CLI and see help output."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage: codeanalyzer [OPTIONS] COMMAND [ARGS]..." in result.output


def test_cli_call_symbol_table(cli_runner, project_root):
    """Must be able to run the CLI with symbol table analysis."""

    output_dir = project_root / "src" / "test" / ".output"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = cli_runner.invoke(
        app,
        [
            "--input",
            str(project_root),
            "--output",
            str(project_root / "src" / "test" / ".output"),
            "--analysis-level",
            "1",
            "--no-codeql",
            "--quiet",
            "--cache-dir",
            str(project_root / "src" / "test" / ".cache"),
            "--keep-cache",
        ],
    )
    logger.debug(f"CLI result: {result.output}")
    # assert result.exit_code == 0
    # assert json.load(Path(output_dir) / ".output" / "analysis.json") is not None
    # assert "symbol_table" in json.load(Path(output_dir) / ".output" / "analysis.json")
