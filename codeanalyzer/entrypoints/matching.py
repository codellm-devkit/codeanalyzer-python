"""Stage 3: match rules against decorators and base classes (#27).

Matching is on ``PyDecorator.qualified_name`` -- never the written spelling --
so ``@route`` under ``from flask import route`` hits the same rule as
``@app.route``. An unresolved decorator (``qualified_name is None``) never
matches: under-approximate rather than guess.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Dict, Iterable, List, Optional

from codeanalyzer.entrypoints.rules import DecoratorRule
from codeanalyzer.schema.py_schema import PyEntrypoint

# Dispatch names that are HTTP verbs. DRF's ViewSet dispatch names
# (list, retrieve, create, ...) are NOT verbs and must not be emitted as such.
_HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}


def match_pattern(pattern: str, qualified_name: Optional[str]) -> bool:
    """``{a,b}`` alternation and trailing ``*``; everything else is literal."""
    if not qualified_name:
        return False
    return re.fullmatch(_compile(pattern), qualified_name) is not None


def _compile(pattern: str) -> str:
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "{":
            j = pattern.index("}", i)
            alts = pattern[i + 1 : j].split(",")
            out.append("(?:" + "|".join(re.escape(a.strip()) for a in alts) + ")")
            i = j + 1
        elif ch == "*":
            out.append(r"[^\s]*")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def _literal(text: Optional[str]) -> Any:
    """Best-effort: decorator arguments are unparsed source fragments."""
    if text is None:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None


def _route_of(dec, spec: Optional[Dict[str, Any]]) -> Optional[str]:
    if not spec or spec.get("from") != "positional":
        return None
    args = dec.positional_arguments or []
    idx = int(spec.get("index", 0))
    if idx >= len(args):
        return None
    value = _literal(args[idx])
    return value if isinstance(value, str) else None


def _methods_of(dec, rule: DecoratorRule, spec: Optional[Dict[str, Any]]) -> List[str]:
    if not spec:
        return []
    source = spec.get("from")
    if source == "match_suffix":
        verb = (dec.qualified_name or "").rsplit(".", 1)[-1]
        return [verb.upper()]
    if source == "keyword":
        raw = (dec.keyword_arguments or {}).get(spec.get("name", ""))
        value = _literal(raw)
        if isinstance(value, (list, tuple)):
            return [str(v).upper() for v in value]
        return [str(v).upper() for v in (spec.get("default") or [])]
    return []


def entrypoints_from_decorators(
    node, framework: str, rules: Iterable[DecoratorRule], ruleset: str
) -> List[PyEntrypoint]:
    out: List[PyEntrypoint] = []
    for dec in getattr(node, "decorators", []) or []:
        for rule in rules:
            if not match_pattern(rule.match, dec.qualified_name):
                continue
            out.append(
                PyEntrypoint(
                    framework=framework,
                    confidence=rule.confidence,
                    rule=rule.id,
                    ruleset=ruleset,
                    evidence=dec.qualified_name,
                    route=_route_of(dec, rule.route),
                    http_methods=_methods_of(dec, rule, rule.methods),
                )
            )
    return out
