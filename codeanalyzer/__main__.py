import json
import os
import sys
from importlib.metadata import version as _pkg_version, PackageNotFoundError
from pathlib import Path
from typing import List, Optional, Annotated

import typer


def _pin_hash_seed() -> None:
    """Re-exec once with ``PYTHONHASHSEED=0`` unless the caller pinned one.

    Jedi's inference (and any hash-ordered iteration in the pipeline) walks sets
    keyed on module/access-path strings, so an unpinned per-interpreter hash
    seed makes the emitted L2+ call graph vary run to run (issue #99). The
    seed cannot be set after interpreter start, hence the exec. Export
    PYTHONHASHSEED (any value) to opt out or pin a different seed.

    Only fires when this process really is the CLI (canpy / python -m
    codeanalyzer): in-process invocations — e.g. Typer's CliRunner in the
    test suite, or a host app calling the callback — must never have their
    own process exec'd out from under them."""
    if os.environ.get("PYTHONHASHSEED") is not None:
        return
    argv0 = os.path.basename(sys.argv[0]) if sys.argv else ""
    is_cli = argv0 in ("canpy", "codeanalyzer") or sys.argv[0].endswith(
        os.path.join("codeanalyzer", "__main__.py")
    )
    if not is_cli:
        return
    env = dict(os.environ, PYTHONHASHSEED="0")
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "codeanalyzer", *sys.argv[1:]],
        env,
    )

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.utils import _set_log_level, logger
from codeanalyzer.config import OutputFormat
from codeanalyzer.schema import model_dump_json, strip_internal_only
from codeanalyzer.options import AnalysisOptions, EmitTarget


def _version_callback(value: bool) -> None:
    """Print the installed ``codeanalyzer-python`` version and exit.

    Eager so it fires before the rest of the CLI callback (no -i/--input
    required). Reads the version from package metadata so it always reflects
    what is actually installed rather than a hardcoded string."""
    if not value:
        return
    try:
        installed = _pkg_version("codeanalyzer-python")
    except PackageNotFoundError:
        installed = "unknown"
    typer.echo(f"canpy {installed}")
    raise typer.Exit()


def main(
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            help="Show the canpy version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
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
            help="Output format for --emit json: json.",
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
    analysis_level: Annotated[
        Optional[int],
        typer.Option(
            "-a",
            "--analysis-level",
            help="Analysis depth: 1=symbol table+Jedi call graph, 2=+defuse-linker call "
            "graph, 3=+native intraprocedural dataflow (CFG/PDG), "
            "4=+interprocedural SDG (param/summary edges, alias-aware DDG). "
            "[default: 1; incompatible with --emit neo4j, which is always "
            "full-depth]",
            min=1,
            max=4,
            show_default="1",
        ),
    ] = None,
    graphs: Annotated[
        Optional[str],
        typer.Option(
            "--graphs",
            help="Level 3+ only: comma-separated program-graph sections to emit "
            "(cfg, dfg, pdg, sdg). Default: cfg,dfg,pdg. `dfg` emits the PDG's data "
            "edges only; `sdg` requires -a 4. Incompatible with --emit neo4j "
            "(always full-depth).",
            show_default="cfg,dfg,pdg",
        ),
    ] = None,
    graph_field_depth: Annotated[
        int,
        typer.Option(
            "--graph-field-depth",
            help="Level 3 only: k-limit on access-path depth (x.f.g.h with "
            "k=3 becomes x.f.g.*). Mandatory bound — it is what guarantees "
            "the interprocedural fixpoint terminates.",
            min=1,
        ),
    ] = 3,
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
    entrypoint_rules: Annotated[
        Optional[List[Path]],
        typer.Option(
            "--entrypoint-rules",
            help="Extra entrypoint rules file (YAML). Repeatable; merges with "
            "the shipped rules. A malformed file is an error.",
        ),
    ] = None,
):
    # Determinism: pin the interpreter hash seed before any analysis (no-op
    # when PYTHONHASHSEED is already set; --version exits before this).
    _pin_hash_seed()

    # Flag validation (strict: unrecognized values error out, never fall back).
    # -a and --graphs use None sentinels so an explicitly-passed flag is
    # distinguishable from the default (#119).
    explicit_level = analysis_level is not None
    explicit_graphs = graphs is not None

    # Neo4j is always full-depth (#119): the graph carries every level's
    # facts, so depth/section selectors cannot be combined with it — reject
    # explicitly-passed flags and force level 4 with every graph section.
    from codeanalyzer.dataflow.builder import VALID_GRAPHS

    if emit == EmitTarget.NEO4J:
        explicit = [
            flag
            for flag, was_explicit in (
                ("-a/--analysis-level", explicit_level),
                ("--graphs", explicit_graphs),
            )
            if was_explicit
        ]
        if explicit:
            logger.error(
                "--emit neo4j is always full-depth (level 4, all graph "
                f"sections); {' and '.join(explicit)} cannot be combined with it."
            )
            raise typer.Exit(code=2)
        analysis_level = 4
        graphs = ",".join(VALID_GRAPHS)

    if analysis_level is None:
        analysis_level = 1
    if graphs is None:
        graphs = "cfg,dfg,pdg"
    selected_graphs = [g.strip() for g in graphs.split(",") if g.strip()]

    unknown_graphs = [g for g in selected_graphs if g not in VALID_GRAPHS]
    if unknown_graphs:
        logger.error(
            f"Unrecognized --graphs value(s): {', '.join(unknown_graphs)} "
            f"(valid: {', '.join(VALID_GRAPHS)})."
        )
        raise typer.Exit(code=2)
    if not selected_graphs:
        logger.error("--graphs requires at least one of: " + ", ".join(VALID_GRAPHS))
        raise typer.Exit(code=2)
    if "sdg" in selected_graphs and analysis_level < 4:
        logger.error("--graphs sdg requires -a 4 (interprocedural SDG).")
        raise typer.Exit(code=2)
    if analysis_level < 3 and explicit_graphs:
        logger.error("--graphs is a level-3 option; pass -a 3 to emit program graphs.")
        raise typer.Exit(code=2)
    if analysis_level < 3 and graph_field_depth != 3:
        logger.error("--graph-field-depth is a level-3 option; pass -a 3.")
        raise typer.Exit(code=2)

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
        analysis_level=analysis_level,
        graphs=",".join(selected_graphs),
        graph_field_depth=graph_field_depth,
        using_ray=using_ray,
        rebuild_analysis=rebuild_analysis,
        skip_tests=skip_tests,
        no_venv=no_venv,
        file_name=file_name,
        cache_dir=cache_dir,
        clear_cache=clear_cache,
        verbosity=verbosity,
        entrypoint_rules=tuple(entrypoint_rules or ()),
    )

    _set_log_level(options.verbosity)

    # Entrypoint rules are configuration, validated before any analysis work
    # starts (#122 review) -- a typo must fail in milliseconds, not after the
    # symbol table, venv build, Jedi and the defuse linker have all run. `detect_entrypoints`
    # loads the rules again at its own call site; that second load is cheap
    # and keeps the entrypoints pipeline self-contained.
    if options.entrypoint_rules:
        from codeanalyzer.entrypoints.rules import RulesError, load_rules

        try:
            load_rules(options.entrypoint_rules)
        except RulesError as exc:
            logger.error(f"Invalid --entrypoint-rules: {exc}")
            raise typer.Exit(code=1)

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
            print(
                json.dumps(
                    strip_internal_only(
                        artifacts.model_dump(mode="json", exclude_none=True)
                    )
                )
            )
        else:
            options.output.mkdir(parents=True, exist_ok=True)
            _write_output(artifacts, options.output, options.format)


def _write_output(artifacts, output_dir: Path, format: OutputFormat):
    """Write artifacts to file in the specified format."""
    if format == OutputFormat.JSON:
        output_file = output_dir / "analysis.json"
        # Use Pydantic's model_dump_json() for compact output
        # Strip internal-only fields here rather than with a field-level Pydantic
        # `exclude`: the analysis cache shares the serializer and must keep them.
        json_str = json.dumps(
            strip_internal_only(artifacts.model_dump(mode="json", exclude_none=True))
        )
        with output_file.open("w") as f:
            f.write(json_str)
        logger.info(f"Analysis saved to {output_file}")


app = typer.Typer(
    callback=main,
    name="canpy",
    help="Static Analysis on Python source code using Jedi and Tree sitter.",
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
