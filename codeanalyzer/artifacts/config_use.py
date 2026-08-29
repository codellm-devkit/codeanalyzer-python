"""config_use detection + resolution (#162): mints `PY_USES_CONFIG` edges
between a call site's key argument and the `PyConfigKey` it reads, plus
first-class unresolved records for a key that never closes on a literal.

Two passes, wired from core.py's `>= 2` block after callee backfill and the
config-keys extraction loop (both populate substrate this depends on --
`BodyNode.callee` and `PyArtifact.config_keys`): `detect_config_reads` scans
every callable's `body{}` for a `call` node whose `callee` names a
`PyExternalSymbol` matching a shipped detector rule
(`config_use_rules.yml`, same load/validate idiom as `entrypoints/rules.py`);
`resolve_uses` decodes the matched call's key argument, resolves a string
literal against `PyArtifact.config_keys` per the rule's namespace
preference, and returns `(edges, unresolved)`. `tier_fns` is the extension
point later dataflow tiers (#162 Task 3) plug into -- Task 2 only ships the
literal tier, implemented directly in `resolve_uses`.

Rule matching is prefix-aware on `module`, not exact-equal: empirically,
`configparser.ConfigParser().get(...)` resolves (via the defuse linker) to
callee module `configparser.RawConfigParser` -- `get` is inherited from the
base class, not defined on `ConfigParser` itself -- so a strict `module ==
"configparser"` would never match the shipped rule. `module == rule.module
or module.startswith(rule.module + ".")` covers this without a per-rule
alias list.

Subscript caveat (controller ruling, verified empirically rather than
assumed): `os.environ["X"]` is an `ast.Subscript`, and
`_iter_calls_in_scope` (symbol_table_builder.py) only ever yields
`ast.Call` nodes -- a probe project with an `os.environ[...]` read produces
zero call body nodes for that statement
(`test_environ_subscript_is_not_lowered_to_a_call_node`,
test/test_config_use_literal.py). `os.getenv` and `os.environ.get` cover
the `[env]` namespace in v1; the subscript form is a recorded gap, not a
rule table entry -- there is no call node it could ever match.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import yaml

from codeanalyzer.schema.ids import ordinal_id
from codeanalyzer.schema.py_schema import (
    PyApplication, PyCallable, PyClass, PyConfigKey, PyConfigRead, PyConfigUseEdge,
)

_SHIPPED = Path(__file__).with_name("config_use_rules.yml")
_TOP_LEVEL_KEYS = {"version", "rules"}
_REQUIRED_RULE_KEYS = {"id", "module", "callable", "key_arg", "namespaces"}
_OPTIONAL_RULE_KEYS = {"kwarg"}


class ConfigUseRulesError(Exception):
    """Raised for a malformed config_use_rules.yml. Never swallowed."""


@dataclass(frozen=True)
class Rule:
    id: str
    module: str
    callable: str
    key_arg: int
    namespaces: Tuple[str, ...]
    kwarg: Optional[str] = None  # not yet actionable -- see _key_from_args


@dataclass(frozen=True)
class _Read:
    """One detector-matched call, before resolution."""

    site: str  # GLOBAL ordinal id: <callable-id>@<local-id>
    callee: str  # external id
    rule: Rule
    key_literal: Optional[str]  # decoded str, when the key arg is a str constant
    key_name: Optional[str]  # bare Name identifier, when the key arg is a Name


# A tier consumes the reads still unresolved after the ones before it, and
# returns (new edges, reads still unresolved after this tier). Task 3 defines
# the real dataflow tiers; Task 2's core.py wiring passes none, so only the
# literal tier below (built into resolve_uses) ever runs.
TierFn = Callable[[List[_Read], PyApplication], Tuple[List[PyConfigUseEdge], List[_Read]]]


def load_rules(path: Path = _SHIPPED) -> List[Rule]:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigUseRulesError(f"rules file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigUseRulesError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigUseRulesError(f"{path}: top level must be a mapping")
    unknown = sorted(set(data) - _TOP_LEVEL_KEYS)
    if unknown:
        raise ConfigUseRulesError(f"{path}: unknown top-level key(s): {', '.join(unknown)}")
    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list):
        raise ConfigUseRulesError(f"{path}: `rules` must be a list")
    return [_parse_rule(raw, path) for raw in raw_rules]


def _parse_rule(raw: Dict, origin: Path) -> Rule:
    if not isinstance(raw, dict):
        raise ConfigUseRulesError(f"{origin}: rule entry must be a mapping: {raw!r}")
    missing = _REQUIRED_RULE_KEYS - set(raw)
    if missing:
        raise ConfigUseRulesError(f"{origin}: rule {raw!r} missing {sorted(missing)}")
    unknown = set(raw) - _REQUIRED_RULE_KEYS - _OPTIONAL_RULE_KEYS
    if unknown:
        raise ConfigUseRulesError(f"{origin}: rule {raw!r} has unknown key(s) {sorted(unknown)}")
    namespaces = raw["namespaces"]
    if not isinstance(namespaces, list) or not namespaces or not all(isinstance(n, str) for n in namespaces):
        raise ConfigUseRulesError(
            f"{origin}: rule {raw['id']!r} `namespaces` must be a non-empty list of strings"
        )
    return Rule(
        id=raw["id"], module=raw["module"], callable=raw["callable"],
        key_arg=int(raw["key_arg"]), namespaces=tuple(namespaces),
        kwarg=raw.get("kwarg"),
    )


def _rule_matches(rule: Rule, module: Optional[str], name: str) -> bool:
    if name != rule.callable:
        return False
    mod = module or ""
    return mod == rule.module or mod.startswith(rule.module + ".")


def _walk_callables(app: PyApplication):
    def walk_callable(c: PyCallable):
        yield c
        for ic in (c.callables or {}).values():
            yield from walk_callable(ic)
        for cl in (c.types or {}).values():
            yield from walk_class(cl)

    def walk_class(cl: PyClass):
        for m in (cl.callables or {}).values():
            yield from walk_callable(m)
        for ic in (cl.types or {}).values():
            yield from walk_class(ic)

    for mod in app.symbol_table.values():
        for fn in (mod.functions or {}).values():
            yield from walk_callable(fn)
        for cl in (mod.types or {}).values():
            yield from walk_class(cl)


def _key_from_args(arguments, key_arg: int) -> Tuple[Optional[str], Optional[str]]:
    """`(key_literal, key_name)` from the call's key-position argument.

    Only positional args reach `BodyNode.arguments` (symbol_table_builder.py
    only walks `node.args`, never `node.keywords`) -- a `kwarg=`-only call
    (e.g. `cp.get(section, option="x")`) has no substrate to read the key
    from, and this returns `(None, None)` for it same as a missing position.
    """
    if key_arg >= len(arguments):
        return None, None
    arg = arguments[key_arg]
    literal = None
    if arg.value is not None:
        try:
            decoded = json.loads(arg.value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str):
            literal = decoded
    return literal, arg.name


def detect_config_reads(app: PyApplication, rules: Optional[Sequence[Rule]] = None) -> List[_Read]:
    """Every call body node whose resolved callee matches a detector rule,
    sorted by site for deterministic downstream iteration."""
    rules = load_rules() if rules is None else rules
    reads: List[_Read] = []
    for c in _walk_callables(app):
        for local_id, node in (c.body or {}).items():
            if node.kind != "call" or not node.callee:
                continue
            sym = app.external_symbols.get(node.callee)
            if sym is None:
                continue
            for rule in rules:
                if not _rule_matches(rule, sym.module, sym.name):
                    continue
                key_literal, key_name = _key_from_args(node.arguments or [], rule.key_arg)
                reads.append(_Read(
                    site=ordinal_id(c.id, local_id), callee=node.callee,
                    rule=rule, key_literal=key_literal, key_name=key_name,
                ))
                break  # (module, callable) is unambiguous across the shipped rule set
    reads.sort(key=lambda r: (r.site, r.rule.id))
    return reads


def _namespace_matches(literal: str, namespace: str, keys: List[PyConfigKey]) -> List[PyConfigKey]:
    if namespace == "env":
        matched = [k for k in keys if k.key == literal]
    else:  # ini/properties: the option name is the key's last dotted segment
        matched = [k for k in keys if k.key == literal or k.key.endswith("." + literal)]
    return sorted(matched, key=lambda k: k.id)


def resolve_uses(
    reads: List[_Read], app: PyApplication, tier_fns: Sequence[TierFn] = (),
) -> Tuple[List[PyConfigUseEdge], List[PyConfigRead]]:
    """Literal tier (built in here) then any dataflow `tier_fns` (Task 3) over
    what's still unresolved. Reads resolved at a lower tier are never
    recomputed -- additive, so `-a 2 ⊆ -a 3 ⊆ -a 4` holds by construction."""
    keys_by_namespace: Dict[str, List[PyConfigKey]] = {}
    for art in app.artifacts.values():
        for key in art.config_keys:
            keys_by_namespace.setdefault(key.namespace, []).append(key)

    edges: List[PyConfigUseEdge] = []
    unresolved: List[_Read] = []
    for read in reads:
        if read.key_literal is None:
            unresolved.append(read)
            continue
        matched: List[PyConfigKey] = []
        for namespace in read.rule.namespaces:
            matched = _namespace_matches(read.key_literal, namespace, keys_by_namespace.get(namespace, []))
            if matched:
                break  # first namespace with >=1 match wins
        if not matched:
            unresolved.append(read)
            continue
        for key in matched:
            edges.append(PyConfigUseEdge(src=read.site, dst=key.id, prov=["literal"]))

    for tier_fn in tier_fns:
        new_edges, unresolved = tier_fn(unresolved, app)
        edges.extend(new_edges)

    # Task 2 scope: only the literal tier above ever runs (core.py's L2 block
    # passes no tier_fns), so every leftover read was tried at exactly that
    # one tier. Task 3 must widen this to the tiers actually attempted once
    # dataflow tier_fns exist.
    unresolved_records = [
        PyConfigRead(
            site=r.site, callee=r.callee, key=r.key_literal,
            reason="undefined-key" if r.key_literal is not None else "non-literal",
            prov=["literal"],
        )
        for r in unresolved
    ]
    edges.sort(key=lambda e: (e.src, e.dst))
    unresolved_records.sort(key=lambda u: (u.site, u.reason, u.key or ""))
    return edges, unresolved_records
