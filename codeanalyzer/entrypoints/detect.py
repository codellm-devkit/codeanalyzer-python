"""Stage 0: which frameworks is this project actually using? (#27)

Gates every later stage, so a project without Celery never pays for Celery
rules and cannot false-positive on a locally-defined ``shared_task``. A
package counts as present if first-party source imports it OR the dependency
manifest names it -- either is sufficient, since an import may be dynamic.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Set

from codeanalyzer.entrypoints.rules import RuleSet
from codeanalyzer.schema.py_schema import PyApplication

_REQ = re.compile(r"^\s*['\"]?([A-Za-z0-9_.\-]+)")
_DEPS_ARRAY = re.compile(r"dependencies\s*=\s*\[(.*?)\]", re.DOTALL)
_PKG = re.compile(r"['\"]([A-Za-z0-9][A-Za-z0-9_.\-]*)")


def detected_frameworks(app: PyApplication, project_dir: Path, rules: RuleSet) -> Set[str]:
    present = _imported_packages(app) | _manifest_packages(project_dir)
    return {
        name
        for name, fw in rules.frameworks.items()
        if any(pkg in present for pkg in (fw.detect or [name]))
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
                out.add(spelling.split(".", 1)[0])
    return out


def _manifest_packages(project_dir: Path) -> Set[str]:
    out: Set[str] = set()
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        # PEP 621 `[project] dependencies = [...]` -- single- or multi-line.
        m = _DEPS_ARRAY.search(pyproject.read_text())
        if m:
            for pm in _PKG.finditer(m.group(1)):
                out.add(pm.group(1).split("[", 1)[0].lower())
    requirements = project_dir / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text().splitlines():
            m = _REQ.match(line)
            if m:
                out.add(m.group(1).split("[", 1)[0].lower())
    return out
