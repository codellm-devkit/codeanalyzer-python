import typer
from typing import *
from pathlib import Path

app = typer.Typer(
    name="codeanalyzer",
    help="Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)


@app.command()
def main(
    input: Annotated[Path, typer.Option("-i", "--input", help="Path to the project root directory.")],
    output: Annotated[Optional[Path], typer.Option("-o", "--output", help="Destination directory to save the output graphs. By default, the SDG formatted as a JSON will be printed to the console.")] = None,
    analysis_level: Annotated[int, typer.Option("-a", "--analysis-level", help="Level of analysis to perform. Options: 1 (for just symbol table) or 2 (for call graph). Default: 1")] = 1,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Print logs to console.")] = False,
    target_files: Annotated[Optional[List[str]], typer.Option("-t", "--target-files", help="For each file user wants to perform source analysis on top of existing analysis.json")] = None,
):
    """
    Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.
    """
    pass


if __name__ == "__main__":
    app()