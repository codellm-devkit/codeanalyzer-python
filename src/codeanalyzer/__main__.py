from contextlib import nullcontext
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
    if __name__ != "__main__":
        return  # Avoid reconfiguring logger if not running as a cli application

    logger.remove()

    if level == "OFF":
        return  # If logging is turned off, we do not add any handlers.

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
    output: Annotated[
        Optional[Path],
        typer.Option("-o", "--output", help="Output directory for artifacts."),
    ] = None,
    analysis_level: Annotated[
        int,
        typer.Option("-a", "--analysis-level", help="1: symbol table, 2: call graph."),
    ] = 1,
    using_codeql: Annotated[
        bool, typer.Option("--codeql/--no-codeql", help="Enable CodeQL-based analysis.")
    ] = False,
    rebuild_analysis: Annotated[
        bool,
        typer.Option(
            "--eager/--lazy",
            help="Enable eager or lazy analysis. Eager will rebuild the analysis cache at every run and lazy will use the cache if available. Defaults to lazy.",
        ),
    ] = False,
    cache_dir: Annotated[
        Optional[Path],
        typer.Option(
            "-c",
            "--cache-dir",
            help="Directory to store analysis cache. If not specified, the cache will be stored in the current working directory under `.codeanalyzer`. Defaults to None.",
        ),
    ] = None,
    clear_cache: Annotated[
        bool,
        typer.Option("--clear-cache/--keep-cache", help="Clear cache after analysis."),
    ] = True,
    verbose: Annotated[
        bool, typer.Option("-v/-q", "--verbose/--quiet", help="Enable verbose output.")
    ] = True,
):
    """Static Analysis on Python source code using Jedi, Asteroid, and Treesitter."""
    if verbose:
        _setup_logger("DEBUG")
    else:
        _setup_logger("OFF")

    with AnalyzerCore(
        input, analysis_level, using_codeql, rebuild_analysis, cache_dir, clear_cache
    ) as analyzer:
        artifacts = analyzer.analyze()
        # Default to printing the artifacts to stdout
        print_stream = sys.stdout
        stream_context = nullcontext(print_stream)

        # If output is specified, redirect to file
        if output is not None:
            output.mkdir(parents=True, exist_ok=True)
            output_file = output / "analysis.json"
            stream_context = output_file.open("w")

        with stream_context as f:
            print(artifacts.model_dump_json(indent=4), file=f)


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
