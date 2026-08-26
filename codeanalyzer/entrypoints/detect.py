"""Stage 0: which frameworks is this project actually using? (#27)

Gates every later stage, so a project without Celery never pays for Celery
rules and cannot false-positive on a locally-defined ``shared_task``. A
package counts as present if first-party source imports it OR the dependency
manifest names it -- either is sufficient, since an import may be dynamic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Set

from codeanalyzer import manifests
from codeanalyzer.entrypoints.rules import RuleSet
from codeanalyzer.schema.py_schema import PyApplication


def detected_frameworks(
    app: PyApplication, project_dir: Path, rules: RuleSet
) -> Set[str]:
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
            spelling = getattr(imp, "module", "") or getattr(imp, "name", "") or ""
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
        span = manifests.deps_array_span(
            manifests.strip_comments(pyproject.read_text())
        )
        if span is not None:
            for pm in manifests.PKG_STRING.finditer(span):
                name, _ = manifests.split_spec(pm.group(1))
                if name:
                    out.add(name.lower())
    requirements = project_dir / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text().splitlines():
            name, _ = manifests.split_spec(line)
            if name:
                out.add(name.lower())
    return out
