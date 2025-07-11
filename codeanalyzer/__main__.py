import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated, Optional

import typer

from codeanalyzer.core import AnalyzerCore
from codeanalyzer.utils import _set_log_level, logger


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
            help="Enable eager or lazy analysis. Defaults to lazy.",
        ),
    ] = False,
    cache_dir: Annotated[
        Optional[Path],
        typer.Option(
            "-c",
            "--cache-dir",
            help="Directory to store analysis cache.",
        ),
    ] = None,
    clear_cache: Annotated[
        bool,
        typer.Option("--clear-cache/--keep-cache", help="Clear cache after analysis."),
    ] = True,
    verbosity: Annotated[
        int, typer.Option("-v", count=True, help="Increase verbosity: -v, -vv, -vvv")
    ] = 0,
):
    """Static Analysis on Python source code using Jedi, Astroid, and Treesitter."""
    _set_log_level(verbosity)

    if not input.exists():
        logger.error(f"Input path '{input}' does not exist.")
        raise typer.Exit(code=1)

    with AnalyzerCore(
        input, analysis_level, using_codeql, rebuild_analysis, cache_dir, clear_cache
    ) as analyzer:
        artifacts = analyzer.analyze()
        print_stream = sys.stdout
        stream_context = nullcontext(print_stream)

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
