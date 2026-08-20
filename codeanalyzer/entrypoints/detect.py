"""Stage 0: which frameworks is this project actually using? (#27)

Gates every later stage, so a project without Celery never pays for Celery
rules and cannot false-positive on a locally-defined ``shared_task``. A
package counts as present if first-party source imports it OR the dependency
manifest names it -- either is sufficient, since an import may be dynamic.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Set

from codeanalyzer.entrypoints.rules import RuleSet
from codeanalyzer.schema.py_schema import PyApplication

_REQ = re.compile(r"^\s*['\"]?([A-Za-z0-9_.\-]+)")
_DEPS_START = re.compile(r"dependencies\s*=\s*\[")
_TABLE_HEADER = re.compile(r"(?m)^[ \t]*\[")
_PKG = re.compile(r"['\"]([A-Za-z0-9][A-Za-z0-9_.\-]*)")


def detected_frameworks(app: PyApplication, project_dir: Path, rules: RuleSet) -> Set[str]:
    # `present` (imports, manifest names) and `detect:` values are both
    # lowercased before comparison -- manifest names were already lowercased
    # (PyPI/pip is case-insensitive) but imports and `detect:` were not, so
    # a `detect: [Flask]` user rule silently never matched a `flask` import.
    present = _imported_packages(app) | _manifest_packages(project_dir)
    return {
        name
        for name, fw in rules.frameworks.items()
        if any(pkg.lower() in present for pkg in (fw.detect or [name]))
    }


def _imported_packages(app: PyApplication) -> Set[str]:
    out: Set[str] = set()
    for mod in app.symbol_table.values():
        for imp in mod.imports or []:
            # `from flask import Flask` puts the package in `module`, not `name`.
            # Prefer `module`; fall back to `name` for a bare `import flask`.
            spelling = (getattr(imp, "module", "") or getattr(imp, "name", "") or "")
            spelling = spelling.lstrip(".")
            if spelling:
                out.add(spelling.split(".", 1)[0].lower())
    return out


def _manifest_packages(project_dir: Path) -> Set[str]:
    out: Set[str] = set()
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        # PEP 621 `[project] dependencies = [...]` -- single- or multi-line,
        # possibly containing nested `[...]` extras (`celery[redis]`).
        span = _deps_array_span(_strip_comments(pyproject.read_text()))
        if span is not None:
            for pm in _PKG.finditer(span):
                out.add(pm.group(1).split("[", 1)[0].lower())
    requirements = project_dir / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text().splitlines():
            m = _REQ.match(line)
            if m:
                out.add(m.group(1).split("[", 1)[0].lower())
    return out


def _strip_comments(text: str) -> str:
    """Drop everything from an unquoted ``#`` to end of line.

    # ponytail: quote tracking resets each line, so a `#` inside a
    # triple-quoted string spanning lines could be mis-stripped. TOML
    # dependency arrays don't use those in practice; revisit if they do.
    """
    out_lines = []
    for line in text.splitlines():
        in_str = None
        cut = len(line)
        for i, ch in enumerate(line):
            if in_str:
                if ch == in_str:
                    in_str = None
            elif ch in ("'", '"'):
                in_str = ch
            elif ch == "#":
                cut = i
                break
        out_lines.append(line[:cut])
    return "\n".join(out_lines)


def _deps_array_span(text: str) -> Optional[str]:
    """Return the contents between the `dependencies = [` and its matching
    `]`, counting bracket depth so a nested `[...]` (extras, e.g.
    `celery[redis]`) doesn't close the span early.

    Bounded by the next TOML table header (a `[` starting a line): if the
    array never closes before then, it's unterminated (truncated/corrupt
    file) and this returns None rather than harvesting quoted strings out
    of whatever table follows.
    """
    m = _DEPS_START.search(text)
    if not m:
        return None
    boundary = _TABLE_HEADER.search(text, m.end())
    limit = boundary.start() if boundary else len(text)
    depth = 1
    in_str = None
    i = m.end()
    while i < limit and depth > 0:
        ch = text[i]
        if in_str:
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return text[m.end() : i - 1]
