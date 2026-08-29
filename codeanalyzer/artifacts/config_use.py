"""config_use detection + resolution (#162): mints `PY_USES_CONFIG` edges
between a call site's key argument and the `PyConfigKey` it reads, plus
first-class unresolved records for a key that never closes on a literal.

Three tiers, wired from core.py: `detect_config_reads` runs once, after
callee backfill and the config-keys extraction loop (both populate
substrate this depends on -- `BodyNode.callee` and `PyArtifact.config_keys`),
scanning every callable's `body{}` for a `call` node whose `callee` names a
`PyExternalSymbol` matching a shipped detector rule (`config_use_rules.yml`,
same load/validate idiom as `entrypoints/rules.py`). `resolve_uses` decodes
each matched call's key argument, resolves a string literal against
`PyArtifact.config_keys` per the rule's namespace preference (the literal
tier, built in directly), then threads whatever is still unresolved through
`tier_fns` in order -- core.py passes `[dataflow_intra_tier]` at `-a 3` and
`[dataflow_intra_tier, dataflow_interproc_tier]` at `-a 4` (#162 Task 3),
so a read resolved at a lower tier is never recomputed and `-a 2 ⊆ -a 3 ⊆
-a 4` holds by construction.

`dataflow_intra_tier` closes a non-literal key argument (`_Read.key_name`)
over its own callable's DDG: `_reaching_literal` finds every DDG edge for
that variable whose destination node's span *contains* the call's span --
not `== the call's own local id` as a first reading of the plan suggests,
because the CFG (and hence the DDG) is statement-level (`dataflow/cfg.py`)
while a call nested in `return`/an assignment gets its own, narrower body-key
span (`schema/l1_body.py`); containment is what actually finds the reaching
def for `return os.getenv(KEY)` as well as a bare `os.getenv(KEY)` statement
(where call and statement spans coincide) -- verified empirically against
both shapes before landing this. Each reaching def must slice+parse to
exactly one `Name = <str Constant>` Assign; multiple reaching defs must all
yield the *same* literal (spec caveat: identical duplicates count as closed).
`dataflow_interproc_tier` handles a key that names a *parameter* of its
enclosing callable: every call site targeting that callable (`call_graph`
join, then a `body` scan for the matching `callee` id -- never `param_in`,
controller ruling) must supply the same string literal at that parameter's
position, either directly (`PyCallArgument.value`) or by one non-recursive
hop of the same intra closure at the *caller*'s own call site (`visited`
seeded with the callee id guards the direct-self-recursion case). Both
tiers re-resolve a closed literal against `PyArtifact.config_keys` through
the same namespace-preference helper the literal tier uses, so a value that
closes but names no declared key still becomes `reason="undefined-key"`
rather than `"non-literal"`.

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

import ast
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import yaml

from codeanalyzer.schema.ids import ordinal_id
from codeanalyzer.schema.py_schema import (
    BodyNode, PyApplication, PyCallable, PyClass, PyConfigKey, PyConfigRead,
    PyConfigUseEdge, PyModule,
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
    # `callable_id`/`local_id`: the dataflow tiers' join point back to the
    # enclosing callable's `.ddg`/`.body` (and, for the interproc tier, its
    # `.parameters` and `call_graph` membership as a callee).
    callable_id: str  # the enclosing callable's can:// id
    local_id: str  # the call node's own LOCAL id within `callable_id`'s body
    callee: str  # external id
    rule: Rule
    key_literal: Optional[str]  # decoded str, when the key arg is a str constant
    key_name: Optional[str]  # bare Name identifier, when the key arg is a Name


# A tier consumes the reads still unresolved after the ones before it, and
# returns (new edges, reads still unresolved after this tier). core.py wires
# `[dataflow_intra_tier]` at `-a 3` and `[dataflow_intra_tier,
# dataflow_interproc_tier]` at `-a 4`; the literal tier (built into
# resolve_uses) always runs first, regardless of level.
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
    kwarg = raw.get("kwarg")
    if kwarg is not None and not isinstance(kwarg, str):
        raise ConfigUseRulesError(f"{origin}: rule {raw['id']!r} `kwarg` must be a string")
    return Rule(
        id=raw["id"], module=raw["module"], callable=raw["callable"],
        key_arg=int(raw["key_arg"]), namespaces=tuple(namespaces),
        kwarg=kwarg,
    )


def _rule_matches(rule: Rule, module: Optional[str], name: str) -> bool:
    if name != rule.callable:
        return False
    mod = module or ""
    return mod == rule.module or mod.startswith(rule.module + ".")


def _walk_callable_tree(c: PyCallable):
    yield c
    for ic in (c.callables or {}).values():
        yield from _walk_callable_tree(ic)
    for cl in (c.types or {}).values():
        yield from _walk_class_tree(cl)


def _walk_class_tree(cl: PyClass):
    for m in (cl.callables or {}).values():
        yield from _walk_callable_tree(m)
    for ic in (cl.types or {}).values():
        yield from _walk_class_tree(ic)


def _walk_module_callables(mod: PyModule):
    for fn in (mod.functions or {}).values():
        yield from _walk_callable_tree(fn)
    for cl in (mod.types or {}).values():
        yield from _walk_class_tree(cl)


def _walk_callables(app: PyApplication):
    for mod in app.symbol_table.values():
        yield from _walk_module_callables(mod)


def _index_callables(app: PyApplication) -> Dict[str, Tuple[PyCallable, str]]:
    """``callable id -> (callable, owning module's source)`` -- the dataflow
    tiers' join point from a read's/call-graph's callable id back to the
    `.ddg`/`.body` data (and the module `source` needed to slice a def's
    span text) that the intra closure reads."""
    return {
        c.id: (c, mod.source)
        for mod in app.symbol_table.values()
        for c in _walk_module_callables(mod)
    }


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
                    site=ordinal_id(c.id, local_id), callable_id=c.id, local_id=local_id,
                    callee=node.callee, rule=rule, key_literal=key_literal, key_name=key_name,
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


def _keys_by_namespace(app: PyApplication) -> Dict[str, List[PyConfigKey]]:
    keys_by_namespace: Dict[str, List[PyConfigKey]] = {}
    for art in app.artifacts.values():
        for key in art.config_keys:
            keys_by_namespace.setdefault(key.namespace, []).append(key)
    return keys_by_namespace


def _resolve_literal_against_keys(
    rule: Rule, literal: str, keys_by_namespace: Dict[str, List[PyConfigKey]],
) -> List[PyConfigKey]:
    """The matched `PyConfigKey`s for `literal` under `rule`'s namespace
    preference order -- the first namespace with >=1 match wins, shared by
    the literal tier and both dataflow tiers so a closed-but-undeclared key
    is `reason="undefined-key"` regardless of which tier closed it."""
    for namespace in rule.namespaces:
        matched = _namespace_matches(literal, namespace, keys_by_namespace.get(namespace, []))
        if matched:
            return matched
    return []


def resolve_uses(
    reads: List[_Read], app: PyApplication, tier_fns: Sequence[TierFn] = (),
) -> Tuple[List[PyConfigUseEdge], List[PyConfigRead]]:
    """Literal tier (built in here) then any dataflow `tier_fns` (Task 3) over
    what's still unresolved. Reads resolved at a lower tier are never
    recomputed -- additive, so `-a 2 ⊆ -a 3 ⊆ -a 4` holds by construction."""
    keys_by_namespace = _keys_by_namespace(app)

    edges: List[PyConfigUseEdge] = []
    unresolved: List[_Read] = []
    for read in reads:
        if read.key_literal is None:
            unresolved.append(read)
            continue
        matched = _resolve_literal_against_keys(read.rule, read.key_literal, keys_by_namespace)
        if not matched:
            unresolved.append(read)
            continue
        for key in matched:
            edges.append(PyConfigUseEdge(src=read.site, dst=key.id, prov=["literal"]))

    for tier_fn in tier_fns:
        new_edges, unresolved = tier_fn(unresolved, app)
        edges.extend(new_edges)

    # `prov` lists every tier attempted: the literal tier always runs (above);
    # `tier_fns` non-empty means some dataflow tier(s) ran too -- the
    # vocabulary is exactly "literal"/"dataflow" (no separate intra/interproc
    # tag), so intra-only (-a 3) and intra+interproc (-a 4) both read the same.
    attempted = ["literal"] + (["dataflow"] if tier_fns else [])
    unresolved_records = [
        PyConfigRead(
            site=r.site, callee=r.callee, key=r.key_literal,
            reason="undefined-key" if r.key_literal is not None else "non-literal",
            prov=attempted,
        )
        for r in unresolved
    ]
    edges.sort(key=lambda e: (e.src, e.dst))
    unresolved_records.sort(key=lambda u: (u.site, u.reason, u.key or ""))
    return edges, unresolved_records


# --- dataflow tiers (#162 Task 3) -------------------------------------------


def _assign_literal(source: str, def_node: Optional[BodyNode]) -> Optional[str]:
    """The str constant a single reaching def closes on, or `None` if
    `def_node` isn't exactly a single-target `Name = <str Constant>` Assign
    (a formal-parameter binding has no span; a `for`-header or multi-target
    assign fails to parse/shape-match -- both correctly never close).
    Also rejected by design, not just incidentally: `ast.AnnAssign`
    (`KEY: str = "X"`), `ast.AugAssign` (`KEY += "X"`), and tuple-unpack
    (`KEY, other = ...`) -- none is a plain single-target `Name = <str
    Constant>` Assign."""
    if def_node is None or def_node.span is None:
        return None
    lo, hi = def_node.span.bytes
    text = source.encode("utf-8")[lo:hi].decode("utf-8")
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        return None
    if len(parsed.body) != 1:
        return None
    stmt = parsed.body[0]
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
        return None
    value = stmt.value
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _reaching_literal(c: PyCallable, source: str, use_local_id: str, var: str) -> Optional[str]:
    """The one string literal every DDG-reaching def of `var` at
    `use_local_id` closes on -- `None` if nothing reaches, any reaching def
    isn't a literal assignment, or reaching defs disagree (identical
    duplicates count as one, per the spec caveat).

    `use_local_id` is a `call` node's own local id, keyed by its own
    (typically narrower) span -- but the CFG/DDG is statement-level
    (`dataflow/cfg.py`), so a def's DDG-recorded *use* site is the
    *enclosing statement's* local id, which only coincides with the call's
    own id when the call is itself a bare expression statement. Matching by
    span containment (the def's dst node's span contains the call's span)
    covers both that coincidence and the common case of a call nested in a
    `return`/assignment, without needing to special-case either shape.
    """
    use_node = c.body.get(use_local_id)
    if use_node is None or use_node.span is None:
        return None
    lo, hi = use_node.span.bytes
    literals: Set[str] = set()
    reached = False
    for edge in c.ddg:
        if edge.var != var:
            continue
        dst_node = c.body.get(edge.dst)
        if dst_node is None or dst_node.span is None:
            continue
        d_lo, d_hi = dst_node.span.bytes
        if not (d_lo <= lo and hi <= d_hi):
            continue  # this DDG use is some other reference to `var`, not this call's
        reached = True
        literal = _assign_literal(source, c.body.get(edge.src))
        if literal is None:
            return None  # any non-closing reaching def kills resolution
        literals.add(literal)
    if not reached or len(literals) != 1:
        return None
    return next(iter(literals))


def dataflow_intra_tier(reads: List[_Read], app: PyApplication) -> Tuple[List[PyConfigUseEdge], List[_Read]]:
    """L3 tier: close a non-literal key argument over its own callable's DDG
    (`_reaching_literal`), then resolve the closed literal against
    `PyArtifact.config_keys` exactly like the literal tier does."""
    index = _index_callables(app)
    keys_by_namespace = _keys_by_namespace(app)
    edges: List[PyConfigUseEdge] = []
    unresolved: List[_Read] = []
    for read in reads:
        entry = index.get(read.callable_id)
        if read.key_name is None or entry is None:
            unresolved.append(read)
            continue
        c, source = entry
        literal = _reaching_literal(c, source, read.local_id, read.key_name)
        if literal is None:
            unresolved.append(read)
            continue
        matched = _resolve_literal_against_keys(read.rule, literal, keys_by_namespace)
        if not matched:
            unresolved.append(replace(read, key_literal=literal))
            continue
        for key in matched:
            edges.append(PyConfigUseEdge(src=read.site, dst=key.id, prov=["dataflow"]))
    return edges, unresolved


def _call_sites_targeting(
    app: PyApplication, index: Dict[str, Tuple[PyCallable, str]], target_id: str,
) -> List[Tuple[PyCallable, str, str, BodyNode]]:
    """Every `(caller, caller_source, local_id, call_node)` whose `callee`
    is `target_id` -- the call-graph join (which callables call it) narrowed
    to the actual call-site body nodes (which arguments they pass), sorted
    for deterministic iteration."""
    caller_ids = sorted({e.src for e in app.call_graph if e.dst == target_id})
    sites: List[Tuple[PyCallable, str, str, BodyNode]] = []
    for caller_id in caller_ids:
        entry = index.get(caller_id)
        if entry is None:
            continue
        caller, source = entry
        for local_id, node in sorted((caller.body or {}).items()):
            if node.kind == "call" and node.callee == target_id:
                sites.append((caller, source, local_id, node))
    return sites


def _site_literal(
    node: BodyNode, param_index: int, caller: PyCallable, source: str, local_id: str, visited: Set[str],
) -> Optional[str]:
    """This call site's contribution to closing the callee's parameter: the
    str literal passed directly at `param_index`, or -- when that argument
    is itself a bare Name -- one non-recursive hop of `_reaching_literal` at
    the *caller*'s own call site (guarded by `visited` against the direct
    self-recursive-call case). Only positional args reach `BodyNode.arguments`
    (same substrate limitation `_key_from_args` documents), so a kwarg-only
    or too-short call site contributes nothing -- `None`, same as any other
    non-closing site, which is enough to leave the read unresolved."""
    args = node.arguments or []
    if param_index >= len(args):
        return None
    arg = args[param_index]
    if arg.value is not None:
        try:
            decoded = json.loads(arg.value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, str) else None
    if arg.name is not None and caller.id not in visited:
        return _reaching_literal(caller, source, local_id, arg.name)
    return None


def dataflow_interproc_tier(
    reads: List[_Read], app: PyApplication,
) -> Tuple[List[PyConfigUseEdge], List[_Read]]:
    """L4 tier: a key that names a *parameter* of its enclosing callable
    closes when every call site targeting that callable supplies the same
    string literal (directly, or via one hop of caller-side intra closure)
    -- call-graph + caller argument values only, never `param_in` traversal
    (controller ruling)."""
    index = _index_callables(app)
    keys_by_namespace = _keys_by_namespace(app)
    edges: List[PyConfigUseEdge] = []
    unresolved: List[_Read] = []
    for read in reads:
        entry = index.get(read.callable_id)
        # `key_literal` already set means the intra tier closed this read to
        # a literal that simply named no declared key (e.g. a parameter
        # shadowed by a local reassignment the intra tier correctly traced
        # instead) -- that is this read's real value; re-deriving one from
        # the *callers'* arguments here would ignore the shadow and can
        # misattribute a caller-supplied value to a name the callee never
        # actually reads.
        if read.key_name is None or read.key_literal is not None or entry is None:
            unresolved.append(read)
            continue
        c, _source = entry
        param_names = [p.name for p in c.parameters or []]
        if read.key_name not in param_names:
            unresolved.append(read)
            continue
        param_index = param_names.index(read.key_name)
        sites = _call_sites_targeting(app, index, c.id)
        if not sites:
            unresolved.append(read)
            continue
        visited = {c.id}  # guards the direct self-recursive-call case
        literals: Set[str] = set()
        all_closed = True
        for caller, source, local_id, node in sites:
            literal = _site_literal(node, param_index, caller, source, local_id, visited)
            if literal is None:
                all_closed = False
                break
            literals.add(literal)
        if not all_closed or len(literals) != 1:
            unresolved.append(read)
            continue
        literal = next(iter(literals))
        matched = _resolve_literal_against_keys(read.rule, literal, keys_by_namespace)
        if not matched:
            unresolved.append(replace(read, key_literal=literal))
            continue
        for key in matched:
            edges.append(PyConfigUseEdge(src=read.site, dst=key.id, prov=["dataflow"]))
    return edges, unresolved
