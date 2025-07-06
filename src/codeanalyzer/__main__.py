import sys
from loguru import logger
import typer
from typing import Optional, Annotated
from pathlib import Path
from codeanalyzer.core import AnalyzerCore

app = typer.Typer(
    name="codeanalyzer",
    help="Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)


def _setup_logger(level: str = "INFO") -> None:
    """
    Setup the logger with the specified level.

    Args:
        level (str): The logging level to set. Default is "INFO".
    """
    if __name__ != "__main__":
        return  # Avoid reconfiguring logger if not running as a cli application

    logger.remove()
    logger.add(
        sys.stderr,
        format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {message}",
        level=level,
        colorize=True,
    )


@app.callback()
def init_logging(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable debug logging.")
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Silence all output except errors.")
    ] = False,
):
    if verbose:
        _setup_logger("DEBUG")
    elif quiet:
        _setup_logger("ERROR")
    else:
        _setup_logger("INFO")


@logger.catch
@app.command()
def main(
    input: Annotated[
        Path, typer.Option("-i", "--input", help="Path to the project root directory.")
    ],
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
    ] = True,
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
):
    """Static Analysis on Python source code using Jedi, CodeQL and Tree sitter."""
    with AnalyzerCore(input, using_codeql, analysis_level) as analyzer:
        print(analyzer.analyze())


if __name__ == "__main__":
    app()
