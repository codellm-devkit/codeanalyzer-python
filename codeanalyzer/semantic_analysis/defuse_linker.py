"""Per-callable defuse linker: backfill call edges Jedi could not resolve.

The Joern/Fraunhofer CPG pattern applied to this analyzer: Jedi supplies the
fast base call graph; this pass walks *local* def-use information — lexical
scopes, alias chains, import bindings — to resolve what Jedi left unresolved.
Per callable, no global fixpoint, deterministic by construction — see
``docs/design/specs/2026-08-25-defuse-linker-call-graph-design.md``.

Resolution rules (validated edge-for-edge against Joern and Fraunhofer CPG on
the requests fixture):

- **Bare names** resolve through the lexical chain of *function* scopes out to
  the module — class bodies are transparent, and parameters/ordinary
  assignments shadow outer names, both exactly as at runtime. The chain covers
  local ``def``s, alias assignments (``f = handler; f(x)``), ``from m import
  f [as g]`` (cross-module through the symbol table), and finally Python
  builtins (``super()``, ``len()`` → ``builtins.<name>``).
- **``self.X()`` / ``cls.X()``** resolves against the enclosing class and its
  same-module base chain.
- **Module-alias receivers** (``import logging; logging.getLogger(...)``)
  resolve through ``import`` bindings — to a declared function when the module
  is in the symbol table, to a dotted external otherwise.
- **Constructor calls** to imported or builtin classes follow the existing
  conventions: an in-table class becomes ``<class sig>.__init__``; anything
  else stays a dotted external name.
- **Module- and class-scope call sites and decorator applications** (import
  time work: ``getLogger`` at module level, ``@setupmethod`` in a class body)
  are collected from the AST — the symbol table has no call sites for them —
  and attributed to the module (#131's convention), or to the enclosing
  function for decorators applied inside one.

Edges carry ``prov: ["defuse"]`` and merge with Jedi's via
``call_graph.merge_edges``. Resolutions for real callable sites are
*returned*, never written into ``PyCallsite.callee_signature``: the symbol
table round-trips through the analysis cache, and a persisted resolution
would resurface on the next run as a Jedi edge, silently changing provenance.
``l2_callees.backfill_callees`` takes the returned map instead.
"""
import ast
import builtins as _py_builtins
from typing import Dict, List, Optional, Tuple

from codeanalyzer.schema.py_schema import PyCallable, PyCallEdge, PyClass, PyModule

__all__ = ["defuse_linker_edges"]

# (caller signature, "line:col" of the call site) -> resolved callee signature
Resolutions = Dict[Tuple[str, str], str]

_MAX_CHAIN = 16  # assignment-chain hops before giving up (cycle safety net)
_BUILTINS = frozenset(dir(_py_builtins))


def _is_junk_resolution(sig: Optional[str]) -> bool:
    """Jedi resolutions that name a *type*, not a call target.

    Decorator-wrapped callables resolve to their wrapper's type —
    ``typing.Callable`` for plain decorators, ``functools._lru_cache_wrapper``
    for ``lru_cache`` — which is an annotation, not a callee.
    """
    return bool(sig) and (
        sig.startswith("typing.")
        or sig.startswith("functools._lru_cache")
        or sig == "builtins.NoneType"
    )


class _Scope:
    """One *function* scope (or the module scope). Class bodies never get one."""

    __slots__ = (
        "parent", "funcs", "bindings", "imports", "mod_imports", "blocked",
        "literal_types", "instance_types",
    )

    def __init__(self, parent: Optional["_Scope"]) -> None:
        self.parent = parent
        # bare name -> name-path of a function declared in this scope
        self.funcs: Dict[str, Tuple[str, ...]] = {}
        # bare name -> RHS bare name of a simple alias assignment
        self.bindings: Dict[str, str] = {}
        # bare name -> (module dotted qual, original name) from `from m import f`
        self.imports: Dict[str, Tuple[str, str]] = {}
        # bare name -> dotted module from `import m[.sub] [as alias]`
        self.mod_imports: Dict[str, str] = {}
        # names this scope shadows opaquely (parameters, non-alias assignments)
        self.blocked: set = set()
        # names assigned from literals -> builtin type name ("s = \"\"" -> str)
        self.literal_types: Dict[str, str] = {}
        # names assigned from a constructor call -> class bare name
        # ("jar = RequestsCookieJar()" -> "RequestsCookieJar")
        self.instance_types: Dict[str, str] = {}


def _module_qual(file_key: str) -> str:
    """Dotted module qual from a symbol-table file key (matches signatures).

    ``requests/api.py`` -> ``requests.api``; a package ``__init__.py`` quals to
    the package itself. File keys are relative POSIX paths by contract.
    """
    parts = file_key[:-3].split("/") if file_key.endswith(".py") else file_key.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _ModuleFacts:
    """Everything the walker extracts from one module's AST in a single pass."""

    __slots__ = (
        "module_scope", "by_def", "toplevel_calls", "decorators", "calls_by_scope",
    )

    def __init__(self) -> None:
        self.module_scope = _Scope(None)
        # (def name, def lineno) -> the scope INSIDE that def
        self.by_def: Dict[Tuple[str, int], _Scope] = {}
        # (kind, node) at module or class scope (import-time calls plus
        # f-string lowerings); kind 'call' or a builtins name
        self.toplevel_calls: List[Tuple[str, ast.AST]] = []
        # (decorator expr, scope it resolves in, function path of the def's
        # container or () for module/class level)
        self.decorators: List[Tuple[ast.expr, _Scope, Tuple[str, ...]]] = []
        # (kind, node) inside each *function* scope, for sites Jedi's
        # extractor never recorded (with-statement context managers, etc.)
        self.calls_by_scope: Dict[int, List[Tuple[str, ast.AST]]] = {}


def _collect(tree: ast.Module, module_qual: str) -> _ModuleFacts:
    facts = _ModuleFacts()

    def record_decorators(node, scope: _Scope, container: Tuple[str, ...]) -> None:
        for dec in node.decorator_list:
            expr = dec.func if isinstance(dec, ast.Call) else dec
            facts.decorators.append((expr, scope, container))

    def walk(node: ast.AST, scope: _Scope, path: Tuple[str, ...], in_class: bool,
             container: Tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_path = path + (child.name,)
                if not in_class:
                    scope.funcs[child.name] = child_path
                record_decorators(child, scope, container)
                inner = _Scope(scope)
                for arg_field in ("args", "posonlyargs", "kwonlyargs"):
                    for a in getattr(child.args, arg_field, []) or []:
                        inner.blocked.add(a.arg)
                for a in (child.args.vararg, child.args.kwarg):
                    if a is not None:
                        inner.blocked.add(a.arg)
                facts.by_def[(child.name, child.lineno)] = inner
                facts.calls_by_scope[id(inner)] = list(_scope_expressions(child))
                walk(child, inner, child_path, in_class=False, container=child_path)
            elif isinstance(child, ast.ClassDef):
                record_decorators(child, scope, container)
                # Transparent for bare-name lookup: methods hang off the same
                # enclosing function scope, and class-body names are invisible
                # to them (Python's own rule).
                walk(child, scope, path + (child.name,), in_class=True,
                     container=container)
            else:
                if not in_class:
                    _record_stmt(child, scope)
                walk(child, scope, path, in_class=in_class, container=container)

    facts.toplevel_calls = list(_scope_expressions(tree))
    walk(tree, facts.module_scope, (), in_class=False, container=())
    return facts


def _scope_expressions(node: ast.AST):
    """Yield ``(kind, node)`` for calls and f-string conversions executed in
    *node*'s own scope — descending into class bodies (they execute in the
    enclosing scope) but never into nested ``def`` bodies.

    Kinds: ``("call", ast.Call)``, ``("repr" | "str" | "ascii" | "format",
    ast.FormattedValue)`` — CPython lowers f-string conversions and format
    specs to ``repr()``/``str()``/``ascii()``/``format()`` calls, and the
    reference CPG tools (Joern, Fraunhofer) emit those edges.
    """
    root = node
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur is not root and isinstance(
            cur, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if isinstance(cur, ast.Call):
            yield ("call", cur)
        elif isinstance(cur, ast.Compare) and any(
            isinstance(op, (ast.Eq, ast.NotEq)) for op in cur.ops
        ):
            # `a == b` / `a != b` dispatches through __eq__/__ne__ at runtime;
            # only self-rooted comparisons are resolvable locally, and the
            # reference CPG tools emit exactly those.
            if isinstance(cur.left, ast.Name) and cur.left.id in ("self", "cls"):
                yield ("selfeq", cur)
        elif isinstance(cur, (ast.For, ast.AsyncFor)) and isinstance(
            cur.iter, ast.Name
        ):
            # `for x in y:` calls y.__iter__() at runtime; resolvable when
            # y's type is locally known (the reference tools lower this too).
            yield ("iter", cur)
        elif isinstance(cur, ast.FormattedValue):
            conv = {114: "repr", 115: "str", 97: "ascii"}.get(cur.conversion)
            if conv is not None:
                yield (conv, cur)
            if cur.format_spec is not None:
                yield ("format", cur)
        stack.extend(reversed(list(ast.iter_child_nodes(cur))))


_LITERAL_TYPES = {
    ast.List: "list", ast.ListComp: "list",
    ast.Dict: "dict", ast.DictComp: "dict",
    ast.Set: "set", ast.SetComp: "set",
    ast.Tuple: "tuple", ast.JoinedStr: "str",
}


def _literal_type(value: ast.expr) -> Optional[str]:
    t = _LITERAL_TYPES.get(type(value))
    if t is not None:
        return t
    if isinstance(value, ast.Constant):
        v = value.value
        if isinstance(v, str):
            return "str"
        if isinstance(v, bytes):
            return "bytes"
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int):
            return "int"
        if isinstance(v, float):
            return "float"
    return None


def _record_stmt(stmt: ast.AST, scope: _Scope) -> None:
    """Record one statement's contribution to *scope*'s name table."""
    if isinstance(stmt, ast.ImportFrom):
        _record_import_from(stmt, scope)
    elif isinstance(stmt, ast.Import):
        for alias in stmt.names:
            if alias.asname:
                scope.mod_imports[alias.asname] = alias.name
            else:
                # `import a.b` binds only `a`; attribute chains rooted at `a`
                # re-append the rest.
                root = alias.name.split(".", 1)[0]
                scope.mod_imports[root] = root
    elif isinstance(stmt, ast.Assign):
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name):
                if isinstance(stmt.value, ast.Name):
                    scope.bindings[tgt.id] = stmt.value.id
                else:
                    scope.blocked.add(tgt.id)
                    lt = _literal_type(stmt.value)
                    if lt is not None:
                        scope.literal_types[tgt.id] = lt
                    elif isinstance(stmt.value, ast.Call) and isinstance(
                        stmt.value.func, ast.Name
                    ):
                        scope.instance_types[tgt.id] = stmt.value.func.id
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        if isinstance(stmt.value, ast.Name):
            scope.bindings[stmt.target.id] = stmt.value.id
        elif stmt.value is not None:
            scope.blocked.add(stmt.target.id)


def _record_import_from(node: ast.ImportFrom, scope: _Scope) -> None:
    """``from m import f [as g]``; relative spellings resolve at lookup time.

    The dotted target is stored with the leading-dots convention already
    resolved by the caller via ``module_qual`` — see ``_collect`` usage."""
    # module_qual-relative resolution happens in _resolve_import_target; here
    # the raw (level, module) pair is packed into the stored qual.
    prefix = "." * node.level
    target = prefix + (node.module or "")
    for alias in node.names:
        if alias.name == "*":
            continue
        scope.imports[alias.asname or alias.name] = (target, alias.name)


def _absolute_module(spelled: str, module_qual: str) -> Optional[str]:
    """Resolve a possibly-relative import spelling to a dotted module qual."""
    if not spelled.startswith("."):
        return spelled or None
    level = len(spelled) - len(spelled.lstrip("."))
    rest = spelled[level:]
    base = module_qual.split(".")
    base = base[: len(base) - level]
    parts = base + ([rest] if rest else [])
    joined = ".".join(p for p in parts if p)
    return joined or None


# Typed resolution results
_LOCAL, _FROM, _MODALIAS, _BUILTIN = "local", "from", "modalias", "builtin"


def _resolve_name(name: str, scope: Optional[_Scope]) -> Optional[Tuple]:
    """Chase *name* through scopes and alias bindings to a typed target."""
    for _ in range(_MAX_CHAIN):
        s = scope
        while s is not None:
            if name in s.funcs:
                return (_LOCAL, s.funcs[name])
            if name in s.imports:
                return (_FROM,) + s.imports[name]
            if name in s.mod_imports:
                return (_MODALIAS, s.mod_imports[name])
            if name in s.blocked:
                return None
            if name in s.bindings:
                break
            s = s.parent
        if s is None:
            return (_BUILTIN, name) if name in _BUILTINS else None
        # A binding's RHS resolves from the scope the assignment sits in.
        name, scope = s.bindings[name], s
    return None


def _signature_for_path(mod: PyModule, path: Tuple[str, ...]) -> Optional[str]:
    """Navigate the symbol table by name path; return the callable's signature.

    Paths from the AST walker interleave function AND class names
    (``("Response", "iter_content", "generate")`` for a def nested in a
    method), so each step tries the current container's functions, nested
    callables, and classes.
    """
    if not path:
        return None
    node = None  # PyCallable | PyClass
    container_fns = mod.functions or {}
    container_classes = {c.name: c for c in (mod.types or {}).values()}
    for name in path:
        if node is None:
            node = container_fns.get(name) or container_classes.get(name)
        elif isinstance(node, PyClass):
            node = (node.callables or {}).get(name) or {
                c.name: c for c in (node.types or {}).values()
            }.get(name)
        else:
            node = (node.callables or {}).get(name) or {
                c.name: c for c in (node.types or {}).values()
            }.get(name)
        if node is None:
            return None
    return node.signature if isinstance(node, PyCallable) else None


def _class_in_module(mod: PyModule, name: str) -> Optional[PyClass]:
    for cls in sorted((mod.types or {}).values(), key=lambda c: c.name):
        if cls.name == name:
            return cls
    return None


def _target_signature(
    target: Tuple,
    mod: PyModule,
    module_qual: str,
    by_qual: Dict[str, PyModule],
    attrs: Tuple[str, ...] = (),
) -> Optional[str]:
    """Map a typed resolution (plus trailing attributes) to a callee signature.

    Follows the analyzer's existing conventions: declared functions by their
    symbol-table signature; in-table classes as ``<sig>.__init__`` (a call of
    a class is its constructor); everything else as a dotted external name,
    which the pipeline homes under ``@external`` ids downstream.
    """
    kind = target[0]
    if kind == _LOCAL:
        if attrs:
            return None
        return _signature_for_path(mod, target[1])
    if kind == _BUILTIN:
        if attrs:
            return None
        return f"builtins.{target[1]}"
    if kind == _FROM:
        spelled, orig = target[1], target[2]
        src_qual = _absolute_module(spelled, module_qual)
        if src_qual is None:
            return None
        src_mod = by_qual.get(src_qual)
        if src_mod is not None and not attrs:
            fn = (src_mod.functions or {}).get(orig)
            if fn is not None:
                return fn.signature
            cls = _class_in_module(src_mod, orig)
            if cls is not None:
                return f"{cls.signature}.__init__"
        return ".".join((src_qual, orig) + attrs)
    if kind == _MODALIAS:
        dotted = target[1]
        if not attrs:
            return None  # a bare module reference is not callable
        qual = ".".join((dotted,) + attrs[:-1])
        leaf = attrs[-1]
        src_mod = by_qual.get(qual)
        if src_mod is not None:
            fn = (src_mod.functions or {}).get(leaf)
            if fn is not None:
                return fn.signature
            cls = _class_in_module(src_mod, leaf)
            if cls is not None:
                return f"{cls.signature}.__init__"
        return ".".join((dotted,) + attrs)
    return None


def _resolve_expr(
    expr: ast.expr,
    scope: _Scope,
    mod: PyModule,
    module_qual: str,
    by_qual: Dict[str, PyModule],
) -> Optional[str]:
    """Resolve a Name / dotted-Attribute expression to a callee signature."""
    attrs: List[str] = []
    node = expr
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        lt = _literal_type(node) if isinstance(node, ast.expr) else None
        if lt is not None and len(attrs) == 1:
            return f"builtins.{lt}.{attrs[0]}"
        return None
    attrs_t = tuple(reversed(attrs))
    target = _resolve_name(node.id, scope)
    if target is None:
        return None
    return _target_signature(target, mod, module_qual, by_qual, attrs_t)


def _iter_callables(mod: PyModule):
    """Every callable in *mod* with its enclosing class (None for functions)."""

    def from_callable(c: PyCallable, owner: Optional[PyClass]):
        yield c, owner
        for nested in (c.callables or {}).values():
            # A def nested inside a method closes over the method's `self`;
            # inheriting the owner is the right may-call approximation.
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
    method_name: str,
    owner: PyClass,
    classes: Dict[str, PyClass],
    module_scope: Optional[_Scope] = None,
    mod: Optional[PyModule] = None,
    module_qual: str = "",
    by_qual: Optional[Dict[str, PyModule]] = None,
) -> Optional[str]:
    """Resolve ``self.X()`` against *owner*, its bases, then its subclasses.

    The base chain is ordinary lookup. The subclass fallback covers the mixin
    pattern: ``SessionRedirectMixin.resolve_redirects`` calls ``self.send``,
    declared only on ``Session(SessionRedirectMixin)`` — every runtime
    ``self`` inside the mixin is an instance of a subclass, so a method
    declared by exactly one same-module subclass is the real target (the
    reference CPG tools fabricate an inferred stub on the mixin instead;
    resolving to the declaring subclass is strictly more truthful). Ambiguous
    fan-out (several subclasses declare it) resolves to nothing.
    """
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
    declaring = [
        cls
        for name, cls in sorted(classes.items())
        if owner.name in (cls.base_classes or []) and method_name in (cls.callables or {})
    ]
    if len(declaring) == 1:
        return declaring[0].callables[method_name].signature

    if module_scope is None or by_qual is None or mod is None:
        return None

    # Class-attribute indirection: `self.response_class(...)` where
    # `response_class = Response` is a class attribute — resolve through the
    # attribute's initializer to the real target (the reference tools stop at
    # the attribute name; the initializer's target is the actual callee).
    stack = [owner]
    visited = set()
    while stack:
        cls = stack.pop(0)
        if id(cls) in visited:
            continue
        visited.add(id(cls))
        attr = (cls.attributes or {}).get(method_name)
        if attr is not None and attr.initializer:
            try:
                expr = ast.parse(attr.initializer.strip(), mode="eval").body
            except SyntaxError:
                expr = None
            if expr is not None:
                sig = _resolve_expr(expr, module_scope, mod, module_qual, by_qual)
                if sig is None and isinstance(expr, ast.Name):
                    target_cls = classes.get(expr.id)
                    if target_cls is not None:
                        sig = f"{target_cls.signature}.__init__"
                if sig is not None:
                    return sig
        for base in cls.base_classes or []:
            base_cls = classes.get(base)
            if base_cls is not None:
                stack.append(base_cls)

    # Inherited from an imported base: `class FlaskClient(Client)` with
    # `from werkzeug.test import Client` — the method lives on the external
    # base, so name it there instead of fabricating a stub on the subclass.
    stack, visited = [owner], set()
    while stack:
        cls = stack.pop(0)
        if id(cls) in visited:
            continue
        visited.add(id(cls))
        for base in cls.base_classes or []:
            base_cls = classes.get(base)
            if base_cls is not None:
                stack.append(base_cls)
                continue
            target = _resolve_name(base, module_scope)
            if target is None or target[0] == _LOCAL:
                continue
            dotted = _target_signature(target, mod, module_qual, by_qual)
            if dotted:
                return f"{dotted}.{method_name}"
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


def _receiver_target(
    site_receiver: str,
    method_name: str,
    scope: _Scope,
    mod: PyModule,
    module_qual: str,
    by_qual: Dict[str, PyModule],
    classes: Optional[Dict[str, PyClass]] = None,
) -> Optional[str]:
    """Resolve ``recv.method()`` when ``recv`` is (rooted at) a module alias."""
    lt = _literal_receiver_type(site_receiver)
    if lt is not None:
        return f"builtins.{lt}.{method_name}"
    parts = tuple(p for p in site_receiver.split(".") if p)
    if not parts:
        return None
    if len(parts) == 1:
        lt = _lookup_literal_type(parts[0], scope)
        if lt is not None:
            return f"builtins.{lt}.{method_name}"
        cls_name = _lookup_instance_type(parts[0], scope)
        if cls_name is not None and classes is not None:
            target_cls = classes.get(cls_name)
            if target_cls is not None:
                sig = _resolve_self_call(method_name, target_cls, classes)
                if sig is not None:
                    return sig
    target = _resolve_name(parts[0], scope)
    if target is None or target[0] not in (_MODALIAS, _FROM):
        return None
    return _target_signature(
        target, mod, module_qual, by_qual, parts[1:] + (method_name,)
    )


def _literal_receiver_type(receiver_src: str) -> Optional[str]:
    """Type of a receiver whose source text is itself a literal (``''.join``)."""
    try:
        expr = ast.parse(receiver_src.strip(), mode="eval").body
    except SyntaxError:
        return None
    return _literal_type(expr)


def _lookup_instance_type(name: str, scope: Optional[_Scope]) -> Optional[str]:
    """Nearest-scope constructor-assignment type for *name*, if any."""
    s = scope
    while s is not None:
        if name in s.instance_types:
            return s.instance_types[name]
        if (
            name in s.blocked
            or name in s.bindings
            or name in s.funcs
            or name in s.imports
            or name in s.mod_imports
        ):
            return None
        s = s.parent
    return None


def _lookup_literal_type(name: str, scope: Optional[_Scope]) -> Optional[str]:
    """Nearest scope that binds *name* decides; a literal binding yields a type."""
    s = scope
    while s is not None:
        if name in s.literal_types:
            return s.literal_types[name]
        if (
            name in s.blocked
            or name in s.bindings
            or name in s.funcs
            or name in s.imports
            or name in s.mod_imports
        ):
            return None
        s = s.parent
    return None


def _resolve_uncovered_call(
    call: ast.Call,
    scope: _Scope,
    owner: Optional[PyClass],
    classes: Dict[str, PyClass],
    mod: PyModule,
    module_qual: str,
    by_qual: Dict[str, PyModule],
) -> Optional[str]:
    """Resolve an AST call that has no recorded ``PyCallsite``."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        root = func.value.id
        if root in ("self", "cls") and owner is not None:
            return _resolve_self_call(
                func.attr, owner, classes, scope, mod, module_qual, by_qual
            )
        lt = _lookup_literal_type(root, scope)
        if lt is not None:
            return f"builtins.{lt}.{func.attr}"
    return _resolve_expr(func, scope, mod, module_qual, by_qual)


def defuse_linker_edges(
    symbol_table: Dict[str, PyModule],
) -> Tuple[List[PyCallEdge], Resolutions]:
    """Derive ``prov=["defuse"]`` call edges from local def-use resolution.

    Returns ``(edges, resolutions)``; *resolutions* feeds
    ``l2_callees.backfill_callees`` so resolved ``call`` body nodes get their
    ``callee`` without mutating the cached symbol table. Module-scope and
    decorator edges have no body node and appear only in the edge list.
    """
    edges: Dict[Tuple[str, str], int] = {}
    resolutions: Resolutions = {}
    by_qual: Dict[str, PyModule] = {
        _module_qual(key): m for key, m in sorted(symbol_table.items())
    }

    def bump(src: str, dst: str) -> None:
        edges[(src, dst)] = edges.get((src, dst), 0) + 1

    for key, mod in sorted(symbol_table.items()):
        if not mod.source:
            continue
        try:
            tree = ast.parse(mod.source)
        except SyntaxError:
            continue
        qual = _module_qual(key)
        facts = _collect(tree, qual)
        classes = _classes_by_name(mod)

        # --- function-level call sites (from the symbol table) -------------
        for caller, owner in _iter_callables(mod):
            recorded = {
                (s.start_line, s.start_column) for s in (caller.call_sites or [])
            }
            sites = [
                s
                for s in (caller.call_sites or [])
                if not s.callee_signature or _is_junk_resolution(s.callee_signature)
            ]
            scope = _scope_for_callable(caller, facts.by_def, facts.module_scope)
            for site in sites:
                sig: Optional[str] = None
                if not site.receiver_expr:
                    target = _resolve_name(site.method_name, scope)
                    if target is not None:
                        sig = _target_signature(target, mod, qual, by_qual)
                elif site.receiver_expr in ("self", "cls") and owner is not None:
                    sig = _resolve_self_call(
                        site.method_name, owner, classes,
                        facts.module_scope, mod, qual, by_qual,
                    )
                else:
                    sig = _receiver_target(
                        site.receiver_expr, site.method_name, scope, mod, qual,
                        by_qual, classes,
                    )
                    if sig is None and site.receiver_type:
                        # Jedi's per-site receiver-type inference names the
                        # receiver's class even when the callee is unresolved.
                        rt = site.receiver_type.rsplit(".", 1)[-1]
                        target_cls = classes.get(rt)
                        if target_cls is not None:
                            sig = _resolve_self_call(
                                site.method_name, target_cls, classes,
                                facts.module_scope, mod, qual, by_qual,
                            )
                if sig is None:
                    continue
                bump(caller.signature, sig)
                resolutions[
                    (caller.signature, f"{site.start_line}:{site.start_column}")
                ] = sig

            # Calls Jedi's extractor never recorded as sites at all (with-
            # statement context managers are the common case). They have no
            # body node either, so they contribute edges but no resolutions.
            for kind, node in facts.calls_by_scope.get(id(scope), []):
                if kind == "selfeq":
                    if owner is not None:
                        sig = _resolve_self_call(
                            "__eq__", owner, classes,
                            facts.module_scope, mod, qual, by_qual,
                        )
                        if sig is not None:
                            bump(caller.signature, sig)
                    continue
                if kind == "iter":
                    name = node.iter.id
                    if name in ("self", "cls"):
                        target_cls = owner
                    else:
                        cls_name = _lookup_instance_type(name, scope)
                        target_cls = classes.get(cls_name) if cls_name else None
                    if target_cls is not None:
                        sig = _resolve_self_call("__iter__", target_cls, classes)
                        if sig is not None:
                            bump(caller.signature, sig)
                    continue
                if kind != "call":
                    # f-string conversion/format-spec lowering (repr/str/
                    # ascii/format) — CPython calls these at runtime.
                    bump(caller.signature, f"builtins.{kind}")
                    continue
                if (node.lineno, node.col_offset) in recorded:
                    continue
                sig = _resolve_uncovered_call(
                    node, scope, owner, classes, mod, qual, by_qual
                )
                if sig is not None:
                    bump(caller.signature, sig)

        # --- module/class-scope call sites (from the AST; #131 attribution) -
        for kind, node in facts.toplevel_calls:
            if kind == "call":
                sig = _resolve_expr(node.func, facts.module_scope, mod, qual, by_qual)
            elif kind in ("selfeq", "iter"):
                continue
            else:
                sig = f"builtins.{kind}"
            if sig is not None and sig != qual:
                bump(qual, sig)

        # --- decorator applications ----------------------------------------
        for expr, scope, container in facts.decorators:
            sig = _resolve_expr(expr, scope, mod, qual, by_qual)
            if sig is None:
                continue
            src = _signature_for_path(mod, container) if container else None
            bump(src or qual, sig)

    return (
        [
            PyCallEdge(src=src, dst=dst, weight=n, prov=["defuse"])
            for (src, dst), n in sorted(edges.items())
        ],
        resolutions,
    )
