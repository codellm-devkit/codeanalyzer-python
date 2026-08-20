"""Entrypoint detection: a post-pass over the built L1 symbol table (#27).

Runs AFTER the symbol table exists so every view reference resolves as a
lookup against ids that already exist. Additive metadata: a failure here
loses flags, never the analysis.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

from codeanalyzer.entrypoints.detect import detected_frameworks
from codeanalyzer.entrypoints.matching import entrypoints_from_bases, entrypoints_from_decorators
from codeanalyzer.entrypoints.rules import RuleSet, load_rules
from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyClass
from codeanalyzer.utils import logger


def detect_entrypoints(
    app: PyApplication, project_dir: Path, rule_paths: Iterable[Path] = ()
) -> None:
    """Populate ``entrypoints`` on every callable and class, in place.

    Loading the rules is a CONFIGURATION step, not a detection step: a
    malformed user rules file is a hard error that must stop the run before
    analysis starts, so ``load_rules`` runs outside (and before) the
    try/except below. Everything after that -- the actual framework
    detection -- is best-effort and must never abort the analysis.
    """
    rules = load_rules(rule_paths)
    try:
        _run_stages(app, project_dir, rules)
    except Exception as exc:  # noqa: BLE001 - additive pass must never abort analysis
        logger.warning("entrypoint detection failed: %s", exc)
        app.entrypoint_report.errors.append(str(exc))
    _derive_flags(app)


def _run_stages(app: PyApplication, project_dir: Path, rules: RuleSet) -> None:
    """Stages 0-4. Stage 0 (framework detection) and Stage 3 (decorator and
    base-class matching) exist so far."""
    app.entrypoint_report.rulesets = list(rules.rulesets)
    frameworks = detected_frameworks(app, project_dir, rules)
    app.entrypoint_report.frameworks_detected = sorted(frameworks)

    ruleset_name = rules.rulesets[-1] if len(rules.rulesets) > 1 else "shipped"
    for name in sorted(frameworks):
        fw = rules.frameworks[name]
        for node in _walk(app):
            node.entrypoints.extend(
                entrypoints_from_decorators(node, name, fw.decorators, ruleset_name)
            )
            if isinstance(node, PyClass) and fw.bases:
                class_eps, method_eps = entrypoints_from_bases(
                    node, name, fw.bases, ruleset_name, lambda b: b
                )
                node.entrypoints.extend(class_eps)
                for method_name, eps in method_eps.items():
                    target = (node.callables or {}).get(method_name)
                    if target is not None:
                        target.entrypoints.extend(eps)


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
