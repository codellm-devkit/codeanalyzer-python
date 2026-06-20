#!/usr/bin/env python3
"""Regenerate the ``canpy --help`` block in README.md from the actual Typer CLI,
so the documented options can never drift from the code. Mirrors the TypeScript
backend's ``scripts/update-readme.ts``.

Run it directly::

    uv run python scripts/update_readme.py            # rewrite the block in place
    uv run python scripts/update_readme.py --check    # exit 1 if the block is stale

The release workflow runs the in-place form before publishing and commits the
result back to main. Exits non-zero if the marker block is missing.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
BEGIN = "<!-- BEGIN canpy-help -->"
END = "<!-- END canpy-help -->"
WIDTH = 100  # fixed render width so the box is deterministic across machines


def render_help() -> str:
    """Render ``canpy --help`` deterministically: fixed width, no color, no
    dependence on the host terminal."""
    os.environ["COLUMNS"] = str(WIDTH)
    os.environ["TERM"] = "dumb"
    os.environ["NO_COLOR"] = "1"

    # Typer caps help width at rich_utils.MAX_WIDTH (default 80) regardless of
    # COLUMNS, so CI renders the box narrower than a dev machine. Pin it to WIDTH
    # so the rendered help is wide and byte-identical everywhere.
    try:
        import typer.rich_utils as _ru

        _ru.MAX_WIDTH = WIDTH
    except Exception:  # pragma: no cover - defensive across Typer versions
        pass

    from click.testing import CliRunner
    from typer.main import get_command

    from codeanalyzer.__main__ import app

    result = CliRunner().invoke(get_command(app), ["--help"], prog_name="canpy")
    if result.exit_code != 0:  # pragma: no cover - help should always render
        raise SystemExit(f"update_readme: `canpy --help` exited {result.exit_code}\n{result.output}")
    # Drop rich's right-edge padding so the block is free of trailing whitespace.
    return "\n".join(line.rstrip() for line in result.output.split("\n")).strip("\n")


def main() -> int:
    block = f"{BEGIN}\n\n```text\n$ canpy --help\n\n{render_help()}\n```\n\n{END}"
    md = README.read_text()
    if BEGIN not in md or END not in md:
        print(f"update_readme: markers {BEGIN} … {END} not found in README.md", file=sys.stderr)
        return 1
    updated = re.sub(re.escape(BEGIN) + r"[\s\S]*?" + re.escape(END), lambda _: block, md)

    if updated == md:
        print("README --help block already current")
        return 0
    if "--check" in sys.argv[1:]:
        print("README --help block is STALE — run: uv run python scripts/update_readme.py", file=sys.stderr)
        return 1
    README.write_text(updated)
    print("README --help block updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
