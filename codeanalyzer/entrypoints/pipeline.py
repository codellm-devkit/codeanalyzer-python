"""Entrypoint detection: a post-pass over the built L1 symbol table (#27).

Runs AFTER the symbol table exists so every view reference resolves as a
lookup against ids that already exist. Additive metadata: a failure here
loses flags, never the analysis.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyClass
from codeanalyzer.utils import logger


def detect_entrypoints(
    app: PyApplication, project_dir: Path, rule_paths: Iterable[Path] = ()
) -> None:
    """Populate ``entrypoints`` on every callable and class, in place."""
    try:
        _run_stages(app, project_dir, tuple(rule_paths))
    except Exception as exc:  # noqa: BLE001 - additive pass must never abort analysis
        logger.warning("entrypoint detection failed: %s", exc)
        app.entrypoint_report.errors.append(str(exc))
    _derive_flags(app)


def _run_stages(app: PyApplication, project_dir: Path, rule_paths: tuple) -> None:
    """Stages 0-4. Empty until Task 5; the skeleton exists so the contract does."""
    return None


def _derive_flags(app: PyApplication) -> None:
    for node in _walk(app):
        node.is_entrypoint = bool(node.entrypoints)


def _walk(app: PyApplication) -> Iterator[object]:
    def walk_callable(c: PyCallable) -> Iterator[object]:
        yield c
        for inner in (c.callables or {}).values():
            yield from walk_callable(inner)
        for cls in (c.types or {}).values():
            yield from walk_class(cls)

    def walk_class(k: PyClass) -> Iterator[object]:
        yield k
        for m in (k.callables or {}).values():
            yield from walk_callable(m)
        for inner in (k.types or {}).values():
            yield from walk_class(inner)

    for mod in app.symbol_table.values():
        for fn in (mod.functions or {}).values():
            yield from walk_callable(fn)
        for cls in (mod.types or {}).values():
            yield from walk_class(cls)
