from pathlib import Path
from typing import Optional, Annotated

import typer

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.utils import _set_log_level, logger
from codeanalyzer.config import OutputFormat
from codeanalyzer.schema import model_dump_json
from codeanalyzer.options import AnalysisOptions


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
    using_ray: Annotated[
        bool,
        typer.Option("--ray/--no-ray", help="Enable Ray for distributed analysis."),
    ] = False,
    rebuild_analysis: Annotated[
        bool,
        typer.Option(
            "--eager/--lazy",
            help="Enable eager or lazy analysis. Defaults to lazy.",
        ),
    ] = False,
    skip_tests: Annotated[
        bool,
        typer.Option(
            "--skip-tests/--include-tests",
            help="Skip test files in analysis.",
        ),
    ] = True,
    file_name: Annotated[
        Optional[Path],
        typer.Option(
            "--file-name",
            help="Analyze only the specified file (relative to input directory).",
        ),
    ] = None,
    cache_dir: Annotated[
        Optional[Path],
        typer.Option(
            "-c",
            "--cache-dir",
            help="Directory to store analysis cache. Defaults to '.codeanalyzer' in the input directory.",
        ),
    ] = None,
    clear_cache: Annotated[
        bool,
        typer.Option(
            "--clear-cache/--keep-cache",
            help="Clear cache after analysis. By default, cache is retained.",
        ),
    ] = False,
    verbosity: Annotated[
        int, typer.Option("-v", count=True, help="Increase verbosity: -v, -vv, -vvv")
    ] = 0,
):
    options = AnalysisOptions(
        input=input,
        output=output,
        format=format,
        analysis_level=analysis_level,
        using_codeql=using_codeql,
        using_ray=using_ray,
        rebuild_analysis=rebuild_analysis,
        skip_tests=skip_tests,
        file_name=file_name,
        cache_dir=cache_dir,
        clear_cache=clear_cache,
        verbosity=verbosity,
    )

    _set_log_level(options.verbosity)
    if not options.input.exists():
        logger.error(f"Input path '{options.input}' does not exist.")
        raise typer.Exit(code=1)

    if options.file_name is not None:
        full_file_path = options.input / options.file_name
        if not full_file_path.exists():
            logger.error(
                f"Specified file '{options.file_name}' does not exist in '{options.input}'."
            )
            raise typer.Exit(code=1)
        if not full_file_path.is_file():
            logger.error(f"Specified path '{options.file_name}' is not a file.")
            raise typer.Exit(code=1)
        if not str(options.file_name).endswith(".py"):
            logger.error(
                f"Specified file '{options.file_name}' is not a Python file (.py)."
            )
            raise typer.Exit(code=1)

    with Codeanalyzer(options) as analyzer:
        artifacts = analyzer.analyze()

        if options.output is None:
            print(model_dump_json(artifacts, separators=(",", ":")))
        else:
            options.output.mkdir(parents=True, exist_ok=True)
            _write_output(artifacts, options.output, options.format)


def _write_output(artifacts, output_dir: Path, format: OutputFormat):
    """Write artifacts to file in the specified format."""
    if format == OutputFormat.JSON:
        output_file = output_dir / "analysis.json"
        # Use Pydantic's model_dump_json() for compact output
        json_str = model_dump_json(artifacts, indent=None)
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
