"""Per-callable defuse linker: backfill call edges Jedi could not resolve.

The Joern/Fraunhofer CPG pattern applied to this analyzer: Jedi supplies the
fast base call graph; this pass walks each callable's *local* def-use chain
(plus module-scope bindings) to resolve the call sites Jedi left unresolved.
Per callable, no global fixpoint, deterministic by construction — see
``docs/design/specs/2026-08-25-defuse-linker-call-graph-design.md``.

Scope rules mirror Python's: a bare name at a call site resolves through the
lexical chain of *function* scopes out to the module — class bodies are
transparent, exactly as at runtime (a method cannot reach a sibling method by
bare name). MVP resolution covers bare-name calls whose reaching definition
is a declared function (module-level or nested) or an assignment chain ending
at one (``f = handler; f(x)``), bare names bound by ``from m import f``
(resolved cross-module through the symbol table, both absolute and relative
spellings), plus ``self.X()`` / ``cls.X()`` receiver calls
resolved against the enclosing class and its same-module bases (Jedi misses a
surprising number of these on decorated or mixin-heavy classes). Other
receivers, parameters, and cross-module flows are out of scope (spec:
extensions).

Edges emitted here carry ``prov: ["defuse"]`` and are merged with Jedi's via
``call_graph.merge_edges``. Resolutions are *returned*, never written into
``PyCallsite.callee_signature``: the symbol table round-trips through the
analysis cache, and a persisted resolution would resurface on the next run as
a Jedi edge (``jedi_call_graph_edges`` derives from ``callee_signature``),
silently changing provenance. ``l2_callees.backfill_callees`` takes the
returned map instead.
"""
import ast
from typing import Dict, List, Optional, Tuple

from codeanalyzer.schema.py_schema import PyCallable, PyCallEdge, PyClass, PyModule

__all__ = ["defuse_linker_edges"]

# (caller signature, "line:col" of the call site) -> resolved callee signature
Resolutions = Dict[Tuple[str, str], str]

_MAX_CHAIN = 16  # assignment-chain hops before giving up (cycle safety net)


class _Scope:
    """One *function* scope (or the module scope). Class bodies never get one."""

    __slots__ = ("parent", "funcs", "bindings", "imports")

    def __init__(self, parent: Optional["_Scope"]) -> None:
        self.parent = parent
        # bare name -> name-path of a function declared in this scope
        self.funcs: Dict[str, Tuple[str, ...]] = {}
        # bare name -> RHS bare name of a simple alias assignment
        self.bindings: Dict[str, str] = {}
        # bare name -> (module dotted qual, original name) from `from m import f`
        self.imports: Dict[str, Tuple[str, str]] = {}


def _module_qual(file_key: str) -> str:
    """Dotted module qual from a symbol-table file key (matches signatures).

    ``requests/api.py`` -> ``requests.api``; a package ``__init__.py`` quals to
    the package itself. File keys are relative POSIX paths by contract.
    """
    parts = file_key[:-3].split("/") if file_key.endswith(".py") else file_key.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _collect_scopes(
    tree: ast.Module,
    module_qual: str,
) -> Tuple[_Scope, Dict[Tuple[str, int], _Scope]]:
    """Build the function-scope tree and an index ``(name, lineno) -> scope``.

    The index maps each ``def`` to the scope *inside* it, so a callable's
    own locals are the innermost link of its lookup chain.
    """
    module_scope = _Scope(None)
    by_def: Dict[Tuple[str, int], _Scope] = {}

    def walk(node: ast.AST, scope: _Scope, path: Tuple[str, ...], in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_path = path + (child.name,)
                if not in_class:
                    scope.funcs[child.name] = child_path
                inner = _Scope(scope)
                by_def[(child.name, child.lineno)] = inner
                walk(child, inner, child_path, in_class=False)
            elif isinstance(child, ast.ClassDef):
                # Transparent for bare-name lookup: methods hang off the same
                # enclosing function scope, and class-body names are invisible
                # to them (Python's own rule).
                walk(child, scope, path + (child.name,), in_class=True)
            elif isinstance(child, ast.ImportFrom):
                if not in_class:
                    _record_import(child, scope, module_qual)
            else:
                if not in_class:
                    _record_bindings(child, scope)
                walk(child, scope, path, in_class=in_class)

    walk(tree, module_scope, (), in_class=False)
    return module_scope, by_def


def _record_import(node: ast.ImportFrom, scope: _Scope, module_qual: str) -> None:
    """Record ``from m import f [as g]`` under the importer's scope.

    Relative spellings resolve against the importing module's own qual:
    ``from . import x`` / ``from .sub import x`` walk up ``level`` packages.
    ``import m`` is deliberately ignored — it binds a module, and calls
    through it are receiver-form (``m.f()``), out of MVP scope.
    """
    if node.level:
        base = module_qual.split(".")
        # level 1 = current package: drop the module's own last segment.
        base = base[: len(base) - node.level]
        target = ".".join(base + ([node.module] if node.module else []))
    else:
        target = node.module or ""
    if not target:
        return
    for alias in node.names:
        if alias.name == "*":
            continue
        scope.imports[alias.asname or alias.name] = (target, alias.name)


def _record_bindings(stmt: ast.AST, scope: _Scope) -> None:
    """Record ``t = v`` where both sides are bare names (the alias pattern)."""
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Name):
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name):
                scope.bindings[tgt.id] = stmt.value.id
    elif (
        isinstance(stmt, ast.AnnAssign)
        and isinstance(stmt.value, ast.Name)
        and isinstance(stmt.target, ast.Name)
    ):
        scope.bindings[stmt.target.id] = stmt.value.id


# Resolution result: ("local", name-path) or ("import", module qual, name)
_Target = Tuple


def _resolve(name: str, scope: Optional[_Scope]) -> Optional[_Target]:
    """Chase *name* through the scope chain and alias bindings to a target."""
    for _ in range(_MAX_CHAIN):
        s = scope
        while s is not None:
            if name in s.funcs:
                return ("local", s.funcs[name])
            if name in s.imports:
                return ("import",) + s.imports[name]
            if name in s.bindings:
                break
            s = s.parent
        if s is None:
            return None
        # A binding's RHS resolves from the scope the assignment sits in.
        name, scope = s.bindings[name], s
    return None


def _signature_for_path(mod: PyModule, path: Tuple[str, ...]) -> Optional[str]:
    """Navigate the symbol table by function-name path; return the signature.

    Paths produced by :func:`_collect_scopes` contain function *and* class
    names; only paths whose every step is a function exist under
    ``mod.functions`` — a path through a class names a method, which bare-name
    lookup can never reach, so a miss here is simply "not resolvable".
    """
    node: Optional[PyCallable] = mod.functions.get(path[0]) if path else None
    for name in path[1:]:
        if node is None:
            return None
        node = (node.callables or {}).get(name)
    return node.signature if node is not None else None


def _iter_callables(mod: PyModule):
    """Every callable in *mod* with its enclosing class (None for functions)."""

    def from_callable(c: PyCallable, owner: Optional[PyClass]):
        yield c, owner
        for nested in (c.callables or {}).values():
            # A def nested inside a method is a plain function: its bare
            # ``self`` (if any) is a closure over the method's — close enough
            # for may-call resolution, so the owner is inherited.
            yield from from_callable(nested, owner)
        for cls in (c.types or {}).values():
            yield from from_class(cls)

    def from_class(cls: PyClass):
        for m in (cls.callables or {}).values():
            yield from from_callable(m, cls)
        for inner in (cls.types or {}).values():
            yield from from_class(inner)

    for fn in (mod.functions or {}).values():
        yield from from_callable(fn, None)
    for cls in (mod.types or {}).values():
        yield from from_class(cls)


def _classes_by_name(mod: PyModule) -> Dict[str, PyClass]:
    """Every class in *mod* keyed by bare name (first wins, sorted walk)."""
    out: Dict[str, PyClass] = {}

    def add(cls: PyClass) -> None:
        out.setdefault(cls.name, cls)
        for inner in sorted((cls.types or {}).values(), key=lambda c: c.name):
            add(inner)
        for m in (cls.callables or {}).values():
            for nested_cls in sorted((m.types or {}).values(), key=lambda c: c.name):
                add(nested_cls)

    for cls in sorted((mod.types or {}).values(), key=lambda c: c.name):
        add(cls)
    return out


def _resolve_self_call(
    method_name: str, owner: PyClass, classes: Dict[str, PyClass]
) -> Optional[str]:
    """Resolve ``self.X()`` against *owner* and its same-module base chain."""
    seen = set()
    queue = [owner]
    while queue:
        cls = queue.pop(0)
        if id(cls) in seen:
            continue
        seen.add(id(cls))
        target = (cls.callables or {}).get(method_name)
        if target is not None:
            return target.signature
        for base in cls.base_classes or []:
            base_cls = classes.get(base)
            if base_cls is not None:
                queue.append(base_cls)
    return None


def _scope_for_callable(
    c: PyCallable, by_def: Dict[Tuple[str, int], _Scope], module_scope: _Scope
) -> _Scope:
    for line in (c.code_start_line, c.start_line):
        scope = by_def.get((c.name, line))
        if scope is not None:
            return scope
    # Decorated defs: PyCallable lines may point at the decorator; scan a
    # small window below for the def line.
    for delta in range(1, 8):
        scope = by_def.get((c.name, c.start_line + delta))
        if scope is not None:
            return scope
    return module_scope


def defuse_linker_edges(
    symbol_table: Dict[str, PyModule],
) -> Tuple[List[PyCallEdge], Resolutions]:
    """Derive ``prov=["defuse"]`` call edges from local def-use resolution.

    Returns ``(edges, resolutions)``; *resolutions* feeds
    ``l2_callees.backfill_callees`` so resolved ``call`` body nodes get their
    ``callee`` without mutating the cached symbol table.
    """
    edges: Dict[Tuple[str, str], int] = {}
    resolutions: Resolutions = {}
    # dotted module qual -> module, for cross-module from-import resolution
    by_qual: Dict[str, PyModule] = {
        _module_qual(key): m for key, m in sorted(symbol_table.items())
    }

    for key, mod in sorted(symbol_table.items()):
        if not mod.source:
            continue
        try:
            tree = ast.parse(mod.source)
        except SyntaxError:
            continue
        module_scope, by_def = _collect_scopes(tree, _module_qual(key))
        classes = _classes_by_name(mod)

        for caller, owner in _iter_callables(mod):
            sites = [
                s
                for s in (caller.call_sites or [])
                if not s.callee_signature and not s.is_constructor_call
            ]
            if not sites:
                continue
            scope = None
            for site in sites:
                sig: Optional[str] = None
                if not site.receiver_expr:
                    if scope is None:
                        scope = _scope_for_callable(caller, by_def, module_scope)
                    target = _resolve(site.method_name, scope)
                    if target is None:
                        pass
                    elif target[0] == "local":
                        sig = _signature_for_path(mod, target[1])
                    else:  # ("import", module qual, original name)
                        src_mod = by_qual.get(target[1])
                        if src_mod is not None:
                            fn = (src_mod.functions or {}).get(target[2])
                            sig = fn.signature if fn is not None else None
                elif site.receiver_expr in ("self", "cls") and owner is not None:
                    sig = _resolve_self_call(site.method_name, owner, classes)
                if sig is None:
                    continue
                key = (caller.signature, sig)
                edges[key] = edges.get(key, 0) + 1
                resolutions[
                    (caller.signature, f"{site.start_line}:{site.start_column}")
                ] = sig

    return (
        [
            PyCallEdge(src=src, dst=dst, weight=n, prov=["defuse"])
            for (src, dst), n in sorted(edges.items())
        ],
        resolutions,
    )
