from pathlib import Path
from typing import Annotated, Optional

import typer

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.utils import _set_log_level, logger
from codeanalyzer.config import OutputFormat


def main(
    input: Annotated[
        Path, typer.Option("-i", "--input", help="Path to the project root directory.")
    ],
    output: Annotated[
        Optional[Path],
        typer.Option("-o", "--output", help="Output directory for artifacts."),
    ] = None,
    format: Annotated[
        OutputFormat,
        typer.Option(
            "-f",
            "--format",
            help="Output format: json or msgpack.",
            case_sensitive=False,
        ),
    ] = OutputFormat.JSON,
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

    with Codeanalyzer(
        input, analysis_level, using_codeql, rebuild_analysis, cache_dir, clear_cache
    ) as analyzer:
        artifacts = analyzer.analyze()

        # Handle output based on format
        if output is None:
            # Output to stdout (only for JSON)
            if format == OutputFormat.JSON:
                print(artifacts.model_dump_json(separators=(",", ":")))
            else:
                logger.error(
                    f"Format '{format.value}' requires an output directory (use -o/--output)"
                )
                raise typer.Exit(code=1)
        else:
            # Output to file
            output.mkdir(parents=True, exist_ok=True)
            _write_output(artifacts, output, format)


def _write_output(artifacts, output_dir: Path, format: OutputFormat):
    """Write artifacts to file in the specified format."""
    if format == OutputFormat.JSON:
        output_file = output_dir / "analysis.json"
        # Use Pydantic's json() with separators for compact output
        json_str = artifacts.model_dump_json(indent=None)
        with output_file.open("w") as f:
            f.write(json_str)
        logger.info(f"Analysis saved to {output_file}")

    elif format == OutputFormat.MSGPACK:
        output_file = output_dir / "analysis.msgpack"
        msgpack_data = artifacts.to_msgpack_bytes()
        with output_file.open("wb") as f:
            f.write(msgpack_data)
        logger.info(f"Analysis saved to {output_file}")
        logger.info(
            f"Compression ratio: {artifacts.get_compression_ratio():.1%} of JSON size"
        )


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
