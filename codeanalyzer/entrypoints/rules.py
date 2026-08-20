"""Loading and merging of entrypoint rules (#27).

The shipped ``rules.yml`` covers known frameworks; users extend it with
``--entrypoint-rules``. User rules merge additively and may ``disable:`` a
shipped rule by id. A malformed user file is a hard error before analysis
starts -- silently skipping it would let someone ship rules they believe
are live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

_SHIPPED = Path(__file__).with_name("rules.yml")
_CONFIDENCE = {"declared", "certain", "heuristic"}


class RulesError(Exception):
    """Raised for a malformed rules file. Never swallowed."""


@dataclass
class DecoratorRule:
    id: str
    match: str
    confidence: str = "certain"
    route: Optional[Dict[str, Any]] = None
    methods: Optional[Dict[str, Any]] = None


@dataclass
class BaseRule:
    id: str
    match: str
    confidence: str = "certain"
    transitive: bool = False
    dispatch: List[str] = field(default_factory=list)


@dataclass
class Framework:
    name: str
    detect: List[str] = field(default_factory=list)
    decorators: List[DecoratorRule] = field(default_factory=list)
    bases: List[BaseRule] = field(default_factory=list)


@dataclass
class RuleSet:
    frameworks: Dict[str, Framework] = field(default_factory=dict)
    rulesets: List[str] = field(default_factory=list)


def load_rules(user_paths: Iterable[Path] = ()) -> RuleSet:
    out = RuleSet()
    _merge(out, _read(_SHIPPED), "shipped")
    for p in user_paths:
        _merge(out, _read(Path(p)), f"user:{p}")
    return out


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise RulesError(f"rules file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RulesError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError(f"{path}: top level must be a mapping")
    return data


def _merge(out: RuleSet, data: Dict[str, Any], origin: str) -> None:
    out.rulesets.append(origin)
    disabled = set(_disable_list(data, origin))
    frameworks = data.get("frameworks") or {}
    if not isinstance(frameworks, dict):
        raise RulesError(f"{origin}: `frameworks` must be a mapping")

    for name, body in frameworks.items():
        if not isinstance(body, dict):
            raise RulesError(f"{origin}: framework `{name}` must be a mapping")
        fw = out.frameworks.setdefault(name, Framework(name=name))
        fw.detect = sorted(set(fw.detect) | set(body.get("detect") or []))
        for raw in body.get("decorators") or []:
            fw.decorators.append(_decorator_rule(raw, origin))
        for raw in body.get("bases") or []:
            fw.bases.append(_base_rule(raw, origin))

    for fw in out.frameworks.values():
        fw.decorators = [r for r in fw.decorators if r.id not in disabled]
        fw.bases = [r for r in fw.bases if r.id not in disabled]


def _disable_list(data: Dict[str, Any], origin: str) -> List[str]:
    raw = data.get("disable") or []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise RulesError(f"{origin}: `disable` must be a list of rule id strings")
    return raw


def _require(raw: Dict[str, Any], key: str, origin: str) -> Any:
    if key not in raw:
        raise RulesError(f"{origin}: rule {raw!r} is missing `{key}`")
    return raw[key]


def _confidence(raw: Dict[str, Any], origin: str) -> str:
    c = raw.get("confidence", "certain")
    if c not in _CONFIDENCE:
        raise RulesError(f"{origin}: confidence must be one of {sorted(_CONFIDENCE)}, got {c!r}")
    return c


def _decorator_rule(raw: Dict[str, Any], origin: str) -> DecoratorRule:
    return DecoratorRule(
        id=_require(raw, "id", origin),
        match=_require(raw, "match", origin),
        confidence=_confidence(raw, origin),
        route=raw.get("route"),
        methods=raw.get("methods"),
    )


def _base_rule(raw: Dict[str, Any], origin: str) -> BaseRule:
    return BaseRule(
        id=_require(raw, "id", origin),
        match=_require(raw, "match", origin),
        confidence=_confidence(raw, origin),
        transitive=bool(raw.get("transitive", False)),
        dispatch=list(raw.get("dispatch") or []),
    )
