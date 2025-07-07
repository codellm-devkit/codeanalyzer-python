from loguru import logger
from codeanalyzer.__main__ import app


def test_cli_help(cli_runner):
    """Must be able to run the CLI and see help output."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage: codeanalyzer [OPTIONS] COMMAND [ARGS]..." in result.output


def test_cli_call_symbol_table(cli_runner, project_root):
    """Must be able to run the CLI with symbol table analysis."""
    result = cli_runner.invoke(
        app,
        [
            "--input",
            str(project_root),
            "--analysis-level",
            "1",
            "--not-using-codeql",
            "--quiet",
        ],
    )
    logger.info(result.output)
    # assert "Implementation with jedi/asteroid goes here..." in result.output
