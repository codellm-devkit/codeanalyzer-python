from pathlib import Path
from typing import Optional, Annotated

import typer

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.utils import _set_log_level, logger
from codeanalyzer.config import OutputFormat
from codeanalyzer.schema import model_dump_json
from codeanalyzer.options import AnalysisOptions, EmitTarget


def main(
    input: Annotated[
        Optional[Path],
        typer.Option(
            "-i",
            "--input",
            help="Path to the project root directory (not required for --emit schema).",
        ),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option("-o", "--output", help="Output directory for artifacts."),
    ] = None,
    format: Annotated[
        OutputFormat,
        typer.Option(
            "-f",
            "--format",
            help="Output format for --emit json: json or msgpack.",
            case_sensitive=False,
        ),
    ] = OutputFormat.JSON,
    emit: Annotated[
        EmitTarget,
        typer.Option(
            "--emit",
            help="Output target: json (analysis.json, default) | neo4j (graph.cypher or live "
            "Bolt push) | schema (the Neo4j schema.json contract).",
            case_sensitive=False,
        ),
    ] = EmitTarget.JSON,
    app_name: Annotated[
        Optional[str],
        typer.Option(
            "--app-name",
            help="Logical application name for the graph :PyApplication anchor "
            "(default: input dir name).",
        ),
    ] = None,
    neo4j_uri: Annotated[
        Optional[str],
        typer.Option(
            "--neo4j-uri",
            envvar="NEO4J_URI",
            help="Push the graph to a live Neo4j over Bolt (incremental); omit to write "
            "graph.cypher. [env: NEO4J_URI]",
        ),
    ] = None,
    neo4j_user: Annotated[
        str,
        typer.Option(
            "--neo4j-user",
            envvar="NEO4J_USERNAME",
            help="Neo4j username. [env: NEO4J_USERNAME]",
        ),
    ] = "neo4j",
    neo4j_password: Annotated[
        str,
        typer.Option(
            "--neo4j-password",
            envvar="NEO4J_PASSWORD",
            help="Neo4j password. Prefer the env var over the flag (the flag is visible in shell "
            "history / process list). [env: NEO4J_PASSWORD]",
        ),
    ] = "neo4j",
    neo4j_database: Annotated[
        Optional[str],
        typer.Option(
            "--neo4j-database",
            envvar="NEO4J_DATABASE",
            help="Neo4j database name (default: server default). [env: NEO4J_DATABASE]",
        ),
    ] = None,
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
    no_venv: Annotated[
        bool,
        typer.Option(
            "--no-venv/--venv",
            help="Skip virtualenv creation and dependency installation; resolve "
            "imports against the ambient Python environment instead.",
        ),
    ] = False,
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
        emit=emit,
        app_name=app_name,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        neo4j_database=neo4j_database,
        using_codeql=using_codeql,
        using_ray=using_ray,
        rebuild_analysis=rebuild_analysis,
        skip_tests=skip_tests,
        no_venv=no_venv,
        file_name=file_name,
        cache_dir=cache_dir,
        clear_cache=clear_cache,
        verbosity=verbosity,
    )

    _set_log_level(options.verbosity)

    # The schema contract is a static artifact — no project analysis required.
    if options.emit == EmitTarget.SCHEMA:
        from codeanalyzer.neo4j.emit import emit_schema

        emit_schema(options.output)
        return

    # Every other target requires an input project.
    if options.input is None:
        logger.error("Missing option '-i' / '--input' (required for --emit json | neo4j).")
        raise typer.Exit(code=1)
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

        if options.emit == EmitTarget.NEO4J:
            from codeanalyzer.neo4j.emit import emit_neo4j

            emit_neo4j(artifacts, options)
        elif options.output is None:
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
    name="canpy",
    help="Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.",
    invoke_without_command=True,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)

def deprecated_main() -> None:
    """Entry point for the legacy ``codeanalyzer`` command. Prints a one-line
    deprecation notice to stderr (so piped stdout — e.g. ``--emit schema`` — stays
    clean) and then runs the CLI unchanged. Kept for backwards compatibility; will
    be removed in a future release."""
    import sys

    print(
        "codeanalyzer: this command has been renamed to `canpy`. The `codeanalyzer` "
        "alias is deprecated and will be removed in a future release — please use `canpy`.",
        file=sys.stderr,
    )
    app()


if __name__ == "__main__":
    app()
