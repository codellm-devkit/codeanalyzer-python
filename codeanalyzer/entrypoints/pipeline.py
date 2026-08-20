"""Entrypoint detection: a post-pass over the built L1 symbol table (#27).

Runs AFTER the symbol table exists so every view reference resolves as a
lookup against ids that already exist. Additive metadata: a failure here
loses flags, never the analysis.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Iterator

from codeanalyzer.entrypoints.detect import detected_frameworks
from codeanalyzer.entrypoints.matching import entrypoints_from_bases, entrypoints_from_decorators
from codeanalyzer.entrypoints.rules import RuleSet, load_rules
from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyClass, PyModule
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
    base-class matching) exist so far.

    Base-class resolution needs the OWNING MODULE's import table (a written
    base like ``APIView`` only resolves via that module's own
    ``from rest_framework.views import APIView``), so this walks module by
    module rather than the whole app flat, building one resolver per module.

    Clears every node's ``entrypoints`` first: on a warm cache,
    ``_build_symbol_table`` reuses the SAME cached ``PyModule``/``PyCallable``
    objects when a file is unchanged, so without this clear a second run
    would ``extend`` onto records already written by the first run and
    duplicate them. A single full clear up front (rather than clearing each
    node as it's visited) avoids wiping ``entrypoints_from_bases`` records
    that ``_walk_module`` writes onto a method before visiting that method
    directly.
    """
    for node in _walk(app):
        node.entrypoints = []

    app.entrypoint_report.rulesets = list(rules.rulesets)
    frameworks = detected_frameworks(app, project_dir, rules)
    app.entrypoint_report.frameworks_detected = sorted(frameworks)

    names = sorted(frameworks)
    for mod in app.symbol_table.values():
        resolve = _base_resolver(mod)
        for node in _walk_module(mod):
            for name in names:
                fw = rules.frameworks[name]
                node.entrypoints.extend(entrypoints_from_decorators(node, name, fw.decorators))
                if isinstance(node, PyClass) and fw.bases:
                    class_eps, method_eps = entrypoints_from_bases(
                        node, name, fw.bases, resolve
                    )
                    node.entrypoints.extend(class_eps)
                    for method_name, eps in method_eps.items():
                        target = (node.callables or {}).get(method_name)
                        if target is not None:
                            target.entrypoints.extend(eps)


def _base_resolver(mod: PyModule):
    """A per-module ``resolve`` callable for ``entrypoints_from_bases``, built
    from the module's own import table -- exact data already on the node,
    never a Jedi guess. Covers ``from x.y import Z[ as W]`` and
    ``import x.y[ as z]``, plus a dotted base (``views.APIView``) whose head
    is the imported name. A base the import table has no mapping for is
    returned unchanged -- under-approximate rather than guess.
    """
    aliases: Dict[str, str] = {}
    for imp in mod.imports or []:
        original = imp.alias or imp.name
        aliases[imp.name] = imp.module if imp.module == original else f"{imp.module}.{original}"

    def resolve(written: str) -> str:
        head, _, rest = written.partition(".")
        target = aliases.get(head)
        return f"{target}.{rest}" if target and rest else (target or written)

    return resolve


def _derive_flags(app: PyApplication) -> None:
    for node in _walk(app):
        node.is_entrypoint = bool(node.entrypoints)


def _walk_module(mod: PyModule) -> Iterator[object]:
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

    for fn in (mod.functions or {}).values():
        yield from walk_callable(fn)
    for cls in (mod.types or {}).values():
        yield from walk_class(cls)


def _walk(app: PyApplication) -> Iterator[object]:
    for mod in app.symbol_table.values():
        yield from _walk_module(mod)
