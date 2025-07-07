import sys
from loguru import logger
import typer
from typing import Optional, Annotated
from pathlib import Path
from codeanalyzer.core import AnalyzerCore
import json


def _setup_logger(level: str = "INFO") -> None:
    """
    Setup the logger with the specified level.

    Args:
        level (str): The logging level to set. Default is "INFO".
    """
    if __name__ != "__main__" or level == "OFF":
        return  # Avoid reconfiguring logger if not running as a cli application

    logger.remove()
    logger.add(
        sys.stderr,
        format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {message}",
        level=level,
        colorize=True,
    )


@logger.catch
def main(
    input: Annotated[
        Path, typer.Option("-i", "--input", help="Path to the project root directory.")
    ],
    virtualenv: Annotated[
        Optional[Path],
        typer.Option(
            "-venv",
            "--virtualenv",
            help="Path to the virtual environment directory. If not provided, one will be created using the project's requirements.txt (or pyproject.toml). If this fails, the system Python will be used.",
        ),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "-o",
            "--output",
            help="Destination directory to save the output graphs. By default, the SDG formatted as a JSON will be printed to the console.",
        ),
    ] = None,
    using_codeql: Annotated[
        bool,
        typer.Option(
            "--using-codeql/--not-using-codeql",
            "-ql/-nql",
            help="Use static analysis provided by the CodeQL backend. Default: True",
        ),
    ] = False,
    rebuild_analysis: Annotated[
        bool,
        typer.Option(
            "--rebuild-analysis/--dont-rebuild-analysis",
            "-ra/-nra",
            help="Rebuild the analysis from scratch. Default: True",
        ),
    ] = False,
    clear_cache: Annotated[
        bool,
        typer.Option(
            "--clear-cache-on-exit/--do-not-clear-cache-on-exit",
            "-cc/-dncc",
            help="Clear the analysis cache after the analysis is complete. Default: False",
        ),
    ] = False,
    analysis_level: Annotated[
        int,
        typer.Option(
            "-a",
            "--analysis-level",
            help="Level of analysis to perform. Options: 1 (symbol table) or 2 (call graph). Default: 1",
        ),
    ] = 1,
    verbose: Annotated[
        bool, typer.Option("--verbose/--quiet", "-v/-q", help="Enable verbose output.")
    ] = False,
):
    """Static Analysis on Python source code using Jedi, Asteroid, and Treesitter."""
    if verbose:
        _setup_logger("DEBUG")
    else:
        _setup_logger("OFF")

    with AnalyzerCore(
        input, virtualenv, using_codeql, rebuild_analysis, clear_cache, analysis_level
    ) as analyzer:
        artifacts = analyzer.analyze()
        # Default to printing the artifacts to stdout
        print_stream = sys.stdout
        # The user has specified an output directory, so we save the artifacts there.
        if output is not None:
            output.mkdir(parents=True, exist_ok=True)
            print_stream = output / "analysis.json"

        print(json.dumps(artifacts, indent=4), file=print_stream)


app = typer.Typer(
    callback=main,
    name="codeanalyzer",
    help="Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.",
    invoke_without_command=True,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)

if __name__ == "__main__":
    app()
