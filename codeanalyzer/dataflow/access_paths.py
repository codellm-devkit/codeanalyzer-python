################################################################################
# Copyright IBM Corporation 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Stage 3a of the level-3 dataflow ladder: the access-path variable model.

An access path is ``base(.field | [*])*`` — ``x``, ``x.f``, ``x.f.g``,
``arr[*]`` (all subscripts collapse to ``[*]``). Depth is k-limited (default
3): ``x.f.g.h`` with k=3 becomes ``x.f.g.*``, which conservatively interferes
with every deeper path. The string form is the ``var`` label of every DDG
edge.

Bases are classified per function scope: ``local``, ``param``, ``self`` (the
first parameter of a method), ``global`` (module binding — explicit ``global``
declaration or a free name not bound in an enclosing function), ``capture``
(free name bound in an enclosing function), and the pseudo-base ``<return>``.

Per-statement facts (defs / uses) follow the documented Python rules:

- Compound statements contribute only their *header* expressions (the CFG is
  statement-level; bodies are separate nodes).
- Comprehension target variables live in their own scope: they are neither
  defs nor uses of the enclosing statement (Python 3 semantics), while the
  iterable and free names remain uses.
- A nested ``def``/``class`` statement defines its name and *uses* every
  enclosing-scope variable the nested body captures (the closure binding is
  over-approximated to the definition site) plus decorators and defaults.
- Calls mutate, over-approximately: the receiver base of a method call and
  every argument that is itself an access path (a mutable reference) are
  weak-defined at the call statement. Sound-leaning by contract; refined
  precision is downstream's job.
- ``del x`` is a def (the name is re-bound to "undefined").
- ``return e`` uses ``e`` and defines the pseudo-path ``<return>``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from codeanalyzer.dataflow.cfg import ControlFlowGraph

RETURN_PATH = "<return>"

# Base-kind vocabulary (recorded per function for the SDG's formal nodes).
BASE_KINDS = ("local", "param", "self", "global", "capture")


def k_limit(path: str, k: int) -> str:
    """Truncate an access path to k dotted components; a truncated path ends
    in ``.*`` and interferes with everything deeper (``x.f.g.h`` with k=3 →
    ``x.f.g.*``). ``[*]`` rides on its owning component."""
    parts = path.split(".")
    if len(parts) <= k:
        return path
    return ".".join(parts[:k]) + ".*"


def interferes(use: str, definition: str) -> bool:
    """Path interference without aliasing: exact match, prefix in either
    direction (a write to ``x`` reaches a read of ``x.f``; a write to ``x.f``
    reaches a read of ``x``), and truncation wildcards."""
    if use == definition:
        return True
    u, d = use.rstrip("*").rstrip("."), definition.rstrip("*").rstrip(".")
    return (
        u == d
        or u.startswith(d + ".")
        or d.startswith(u + ".")
        or u.startswith(d + "[")
        or d.startswith(u + "[")
    )


def suffix_of(path: str) -> str:
    """The field suffix after the base — the part aliasing preserves."""
    base_end = len(path)
    for i, ch in enumerate(path):
        if ch in ".[":
            base_end = i
            break
    return path[base_end:]


def base_of(path: str) -> str:
    for i, ch in enumerate(path):
        if ch in ".[":
            return path[:i]
    return path


@dataclass
class FunctionScope:
    """Name classification for one callable."""

    params: List[str] = field(default_factory=list)
    self_name: Optional[str] = None
    locals_: Set[str] = field(default_factory=set)
    globals_: Set[str] = field(default_factory=set)
    captures: Set[str] = field(default_factory=set)

    def kind_of(self, base: str) -> str:
        if base == self.self_name:
            return "self"
        if base in self.params:
            return "param"
        if base in self.captures:
            return "capture"
        if base in self.globals_:
            return "global"
        if base in self.locals_:
            return "local"
        return "global"  # unknown free name: a module/builtin binding


@dataclass
class StatementFacts:
    """Defs and uses (k-limited access-path strings) of one CFG node."""

    defs: Set[str] = field(default_factory=set)
    uses: Set[str] = field(default_factory=set)


def _assigned_names(func: ast.AST) -> Set[str]:
    """Names bound anywhere in the function body (not descending into nested
    def/class bodies): assignment targets, loop targets, with-as, except-as,
    imports, nested def/class names, del targets, walrus targets."""
    names: Set[str] = set()

    def collect_target(t: ast.AST) -> None:
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for el in t.elts:
                collect_target(el)
        elif isinstance(t, ast.Starred):
            collect_target(t.value)
        # Attribute/Subscript targets bind no *name*.

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(child.name)
                continue  # nested scope
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    collect_target(t)
            elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                collect_target(child.target)
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                collect_target(child.target)
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    if item.optional_vars is not None:
                        collect_target(item.optional_vars)
            elif isinstance(child, ast.ExceptHandler):
                if child.name:
                    names.add(child.name)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(child, ast.NamedExpr):
                collect_target(child.target)
            elif isinstance(child, ast.Delete):
                for t in child.targets:
                    collect_target(t)
            walk(child)

    walk(func)
    return names


def _declared(func: ast.AST, decl_type) -> Set[str]:
    names: Set[str] = set()

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, decl_type):
                names.update(child.names)
            walk(child)

    walk(func)
    return names


def _param_names(func: ast.AST) -> List[str]:
    a = func.args
    names = [p.arg for p in getattr(a, "posonlyargs", [])] + [p.arg for p in a.args]
    if a.vararg:
        names.append(a.vararg.arg)
    names.extend(p.arg for p in a.kwonlyargs)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return names


def free_names(func: ast.AST) -> Set[str]:
    """Names the callable reads but does not bind — candidates for capture
    (if bound in an enclosing function) or module globals. Includes the free
    names of its own nested callables (capture transits scopes)."""
    bound = set(_param_names(func)) | _assigned_names(func) | _declared(func, ast.Global)
    used: Set[str] = set()

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                used.update(free_names(child) - {child.name})
                continue
            if isinstance(child, ast.Lambda):
                lam_bound = set(_param_names(child))
                for name in _names_loaded(child.body):
                    if name not in lam_bound:
                        used.add(name)
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                used.add(child.id)
            walk(child)

    walk(func)
    return used - bound


def _names_loaded(node: ast.AST) -> Set[str]:
    out: Set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.add(n.id)
    return out


def build_scope(func: ast.AST, enclosing_locals: Set[str]) -> FunctionScope:
    """Classify every base name the callable touches. ``enclosing_locals`` is
    the union of locals/params of all enclosing callables (for capture vs
    global disambiguation)."""
    params = _param_names(func)
    scope = FunctionScope(params=params)
    if params and isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        decorators = {ast.unparse(d) for d in func.decorator_list}
        if params[0] in ("self", "cls") and "staticmethod" not in decorators:
            scope.self_name = params[0]
    scope.globals_ = _declared(func, ast.Global)
    nonlocals = _declared(func, ast.Nonlocal)
    scope.locals_ = _assigned_names(func) - scope.globals_ - nonlocals
    free = (free_names(func) | nonlocals) - set(params)
    scope.captures = {n for n in free if n in enclosing_locals}
    scope.globals_ |= free - scope.captures
    return scope


class _PathExtractor:
    """Turns the header expressions of one statement into def/use path sets."""

    def __init__(self, scope: FunctionScope, k: int):
        self.scope = scope
        self.k = k

    # -- expression → path (None when the expression is not a path) ---------

    def path_of(self, expr: ast.expr) -> Optional[str]:
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            inner = self.path_of(expr.value)
            return None if inner is None else k_limit(f"{inner}.{expr.attr}", self.k)
        if isinstance(expr, ast.Subscript):
            inner = self.path_of(expr.value)
            return None if inner is None else k_limit(f"{inner}[*]", self.k)
        return None

    # -- uses ----------------------------------------------------------------

    def uses_in(self, expr: ast.expr) -> Set[str]:
        """All access paths read by an expression. Comprehension targets are
        scoped out; nested lambda bodies contribute their free names only."""
        uses: Set[str] = set()
        self._collect_uses(expr, uses, shadowed=set())
        return uses

    def _collect_uses(self, expr: ast.expr, out: Set[str], shadowed: Set[str]) -> None:
        if isinstance(expr, ast.Name):
            if isinstance(expr.ctx, ast.Load) and expr.id not in shadowed:
                out.add(expr.id)
            return
        if isinstance(expr, (ast.Attribute, ast.Subscript)):
            p = self.path_of(expr)
            if p is not None and base_of(p) not in shadowed:
                out.add(p)
                if isinstance(expr, ast.Subscript):
                    self._collect_uses(expr.slice, out, shadowed)
                return
            # Not a pure path (e.g. f(x).g): fall through to children.
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            inner_shadow = set(shadowed)
            for comp in expr.generators:
                # The iterable of the first generator evaluates in the
                # enclosing scope; targets shadow from then on.
                self._collect_uses(comp.iter, out, inner_shadow)
                inner_shadow |= _names_loaded_targets(comp.target)
                for cond in comp.ifs:
                    self._collect_uses(cond, out, inner_shadow)
            if isinstance(expr, ast.DictComp):
                self._collect_uses(expr.key, out, inner_shadow)
                self._collect_uses(expr.value, out, inner_shadow)
            else:
                self._collect_uses(expr.elt, out, inner_shadow)
            return
        if isinstance(expr, ast.Lambda):
            lam_shadow = shadowed | set(_param_names(expr))
            self._collect_uses(expr.body, out, lam_shadow)
            return
        for child in ast.iter_child_nodes(expr):
            if isinstance(child, ast.expr):
                self._collect_uses(child, out, shadowed)
            elif isinstance(child, (ast.comprehension, ast.keyword)):
                for sub in ast.iter_child_nodes(child):
                    if isinstance(sub, ast.expr):
                        self._collect_uses(sub, out, shadowed)

    # -- defs ----------------------------------------------------------------

    def defs_of_target(self, target: ast.expr) -> Set[str]:
        defs: Set[str] = set()
        if isinstance(target, ast.Name):
            defs.add(target.id)
        elif isinstance(target, (ast.Attribute, ast.Subscript)):
            p = self.path_of(target)
            if p is not None:
                defs.add(p)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for el in target.elts:
                defs.update(self.defs_of_target(el))
        elif isinstance(target, ast.Starred):
            defs.update(self.defs_of_target(target.value))
        return defs

    def target_reads(self, target: ast.expr) -> Set[str]:
        """Reads implied by a compound target: ``p.f = v`` reads ``p``;
        ``a[i] = v`` reads ``a`` and ``i``."""
        reads: Set[str] = set()
        if isinstance(target, (ast.Attribute, ast.Subscript)):
            inner = self.path_of(target.value)
            if inner is not None:
                reads.add(inner)
            else:
                self._collect_uses(target.value, reads, set())
            if isinstance(target, ast.Subscript):
                self._collect_uses(target.slice, reads, set())
        elif isinstance(target, (ast.Tuple, ast.List)):
            for el in target.elts:
                reads.update(self.target_reads(el))
        elif isinstance(target, ast.Starred):
            reads.update(self.target_reads(target.value))
        return reads

    # -- call mutation (documented over-approximation) -----------------------

    def mutation_defs(self, expr: ast.expr) -> Set[str]:
        """Weak defs of the *contents* of receiver/argument objects (``xs.*``
        — suffixed, so a call mutation is never confused with a local
        rebinding, which is not caller-visible)."""
        defs: Set[str] = set()
        for call in _calls_in(expr):
            if isinstance(call.func, ast.Attribute):
                receiver = self.path_of(call.func.value)
                if receiver is not None:
                    defs.add(k_limit(receiver + ".*", self.k))
            for arg in list(call.args) + [kw.value for kw in call.keywords]:
                p = self.path_of(arg)
                if p is not None:
                    defs.add(k_limit(p + ".*", self.k))
        return defs

    def receiver_uses(self, expr: ast.expr) -> Set[str]:
        """Whole-object reads at call sites: a method call reads its receiver
        (dispatch + any field the callee touches — the alias oracle matches
        field writes through other names against this bare-base use)."""
        uses: Set[str] = set()
        for call in _calls_in(expr):
            if isinstance(call.func, ast.Attribute):
                receiver = self.path_of(call.func.value)
                if receiver is not None:
                    uses.add(receiver)
        return uses


def _names_loaded_targets(target: ast.expr) -> Set[str]:
    out: Set[str] = set()
    for n in ast.walk(target):
        if isinstance(n, ast.Name):
            out.add(n.id)
    return out


def _calls_in(expr: ast.expr) -> List[ast.Call]:
    calls: List[ast.Call] = []
    stack: List[ast.AST] = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            calls.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            stack.append(child)
    return calls


def qualify_globals(paths: Set[str], scope: FunctionScope, qualifier: str) -> Set[str]:
    """Rewrite global bases to their module-qualified form ``module::name``
    (``::`` keeps the qualifier out of the field-path grammar). Builtins stay
    bare — they carry no cross-module dataflow worth modeling."""
    import builtins as _builtins

    out: Set[str] = set()
    for p in paths:
        b = base_of(p)
        if (
            "::" not in b
            and b != RETURN_PATH
            and scope.kind_of(b) == "global"
            and not hasattr(_builtins, b)
        ):
            out.add(f"{qualifier}::{b}" + p[len(b):])
        else:
            out.add(p)
    return out


def statement_facts(
    cfg: ControlFlowGraph,
    func: ast.AST,
    scope: FunctionScope,
    k: int,
    global_qualifier: Optional[str] = None,
) -> Dict[int, StatementFacts]:
    """Defs/uses per CFG node id. Compound statements contribute only their
    header expressions; ENTRY defines every param/self/global/capture base
    the function touches (the incoming state). With ``global_qualifier`` set
    (the interprocedural build), global bases become ``module::name``."""
    ex = _PathExtractor(scope, k)
    facts: Dict[int, StatementFacts] = {}

    for node in cfg.nodes:
        f = StatementFacts()
        stmt = node.ast_node

        def call_fx(expr: ast.expr) -> None:
            """Call effects on the current facts: over-approximate mutation
            defs plus the whole-object receiver read."""
            f.defs |= ex.mutation_defs(expr)
            f.uses |= ex.receiver_uses(expr)

        if node.kind == "entry":
            f.defs = set(scope.params) | set(scope.captures)
            if scope.self_name:
                f.defs.add(scope.self_name)
            # Globals the function reads arrive with the incoming state too.
            f.defs |= scope.globals_
        elif stmt is None:
            pass  # exit
        elif isinstance(stmt, ast.Assign):
            f.uses = ex.uses_in(stmt.value)
            for t in stmt.targets:
                f.defs |= ex.defs_of_target(t)
                f.uses |= ex.target_reads(t)
            call_fx(stmt.value)
        elif isinstance(stmt, ast.AugAssign):
            f.uses = ex.uses_in(stmt.value) | ex.defs_of_target(stmt.target) | ex.target_reads(stmt.target)
            f.defs = ex.defs_of_target(stmt.target)
            call_fx(stmt.value)
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value is not None:
                f.uses = ex.uses_in(stmt.value)
                f.defs = ex.defs_of_target(stmt.target)
                f.uses |= ex.target_reads(stmt.target)
                call_fx(stmt.value)
        elif isinstance(stmt, ast.Return):
            if stmt.value is not None:
                f.uses = ex.uses_in(stmt.value)
                call_fx(stmt.value)
            f.defs.add(RETURN_PATH)
        elif isinstance(stmt, ast.If):
            f.uses = ex.uses_in(stmt.test)
            call_fx(stmt.test)
        elif isinstance(stmt, ast.While):
            f.uses = ex.uses_in(stmt.test)
            call_fx(stmt.test)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            f.uses = ex.uses_in(stmt.iter)
            f.defs = ex.defs_of_target(stmt.target)
            call_fx(stmt.iter)
            f.uses |= ex.target_reads(stmt.target)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                f.uses |= ex.uses_in(item.context_expr)
                call_fx(item.context_expr)
                if item.optional_vars is not None:
                    f.defs |= ex.defs_of_target(item.optional_vars)
        elif isinstance(stmt, ast.ExceptHandler):
            if stmt.type is not None:
                f.uses = ex.uses_in(stmt.type)
            if stmt.name:
                f.defs.add(stmt.name)
        elif isinstance(stmt, (ast.Raise, ast.Assert)):
            for sub in ast.iter_child_nodes(stmt):
                if isinstance(sub, ast.expr):
                    f.uses |= ex.uses_in(sub)
                    call_fx(sub)
        elif isinstance(stmt, ast.Expr):
            f.uses = ex.uses_in(stmt.value)
            call_fx(stmt.value)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f.defs.add(stmt.name)
            captured = free_names(stmt) & (scope.locals_ | set(scope.params) | scope.captures)
            f.uses |= captured
            for d in stmt.decorator_list:
                f.uses |= ex.uses_in(d)
            for default in list(stmt.args.defaults) + [
                d for d in stmt.args.kw_defaults if d is not None
            ]:
                f.uses |= ex.uses_in(default)
        elif isinstance(stmt, ast.ClassDef):
            f.defs.add(stmt.name)
            for d in list(stmt.decorator_list) + list(stmt.bases):
                f.uses |= ex.uses_in(d)
        elif isinstance(stmt, ast.Delete):
            for t in stmt.targets:
                f.defs |= ex.defs_of_target(t)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                f.defs.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(stmt, (ast.Global, ast.Nonlocal, ast.Pass, ast.Break, ast.Continue)):
            pass
        else:  # pragma: no cover — future statement kinds stay sound
            for sub in ast.iter_child_nodes(stmt):
                if isinstance(sub, ast.expr):
                    f.uses |= ex.uses_in(sub)

        f.defs = {k_limit(p, k) for p in f.defs}
        f.uses = {k_limit(p, k) for p in f.uses}
        if global_qualifier is not None:
            f.defs = qualify_globals(f.defs, scope, global_qualifier)
            f.uses = qualify_globals(f.uses, scope, global_qualifier)
        facts[node.id] = f

    return facts
