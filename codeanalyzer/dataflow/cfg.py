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

"""Stage 1 of the level-3 dataflow ladder: the exceptional, statement-level CFG.

One CFG per callable, lowered from the stdlib ``ast`` tree — the same parse the
symbol-table builder uses, so node spans and callable signatures line up with
the rest of ``analysis.json``.

Lowering rules (the Python checklist from the CLDK dataflow contract):

- One synthetic ``ENTRY`` (node id 0) and one synthetic ``EXIT`` (last CFG id).
  Multi-exit is normalized: every ``return``/``raise``/fall-off-end gets an
  edge to ``EXIT`` with the appropriate kind.
- ``if``/``while``/``for`` headers are their own nodes (kinds ``branch`` /
  ``loop``) with ``true``/``false`` out-edges; loop back edges carry
  ``loop_back``; ``break``/``continue`` carry their own kinds.
- ``try/except/else/finally``: the try body is lowered in sequence; each
  statement that can raise gets an ``exception`` edge to the innermost
  enclosing handler chain (or ``EXIT`` when there is none). ``except`` match
  clauses are ``handler`` nodes chained by ``false`` edges; an unmatched
  exception propagates outward. ``finally`` bodies are lowered once, on the
  normal path; abrupt entries (return / unhandled raise / break / continue
  observed in the protected region) add corresponding out-edges from the
  finally's end. Exceptions raised inside nested ``finally``-protected regions
  connect straight to the enclosing handler chain — a documented
  over-approximation (the finally body still executes on every normal path,
  so its definitions are never lost, only their ordering on pure-exception
  paths).
- ``with``/``async with``: the header is a ``statement`` node that defines the
  ``as`` targets; the implicit ``__exit__`` try/finally is *not* materialized
  (documented over-approximation); body statements keep their exception edges.
- Generators: a statement containing ``yield``/``yield from`` gets its
  fall-through successor edge with kind ``yield`` (the resume path) plus a
  ``yield`` edge to ``EXIT`` (the generator may never be resumed).
  ``await`` marks the successor edge ``await_resume``.
- ``raise`` → ``exception`` edge to the handler chain / EXIT, no fall-through.
  ``assert`` gets a fall-through plus an ``exception`` edge.
- Expression-level short-circuit (``and``/``or``/ternary) stays atomic inside
  its statement node — the CFG is statement-level by contract.
- Comprehensions are atomic expressions of their statement (their implicit
  loop and scope are handled by the access-path model, not the CFG).
- Nested ``def``/``class`` statements are single ``statement`` nodes (the
  binding); their bodies get their own CFGs keyed by their own signatures.
  Decorators are call-site facts, not CFG nodes.
- Infinite loops (``while True:`` with no break) get a synthetic ``exception``
  edge from the loop header to ``EXIT`` so post-dominance stays well-formed
  (in Python any loop can exit via an async signal such as KeyboardInterrupt,
  so the edge is semantically honest).
- Statements unreachable from ``ENTRY`` (dead code after a return/raise) are
  pruned: they cannot carry dependence.

Statements are considered able to raise when they contain a call, attribute
access, subscript, explicit ``raise``/``assert``, a ``with`` header, or a
``for`` header (iterator protocol) — over-approximate by design.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# The shared, cross-language node-kind and edge-kind vocabulary. Python adds no
# renamed/repurposed kinds; `yield` / `await_resume` are the contract's own.
NODE_KINDS = (
    "entry",
    "exit",
    "statement",
    "branch",
    "loop",
    "return",
    "raise",
    "handler",
)

EDGE_KINDS = (
    "fallthrough",
    "true",
    "false",
    "switch_case",
    "loop_back",
    "exception",
    "return",
    "break",
    "continue",
    "yield",
    "await_resume",
)


@dataclass
class CFGNode:
    """A statement-level CFG node. ``id`` is assigned in source-span order
    after construction (ENTRY = 0, EXIT = last CFG id)."""

    id: int
    kind: str
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1
    # The owning AST statement/expression (None for ENTRY/EXIT). Not emitted;
    # used by later stages to compute def/use sets.
    ast_node: Optional[ast.AST] = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class CFGEdge:
    source: int
    target: int
    kind: str


@dataclass
class ControlFlowGraph:
    """CFG of a single callable, keyed externally by the callable signature."""

    nodes: List[CFGNode]
    edges: List[CFGEdge]
    entry_id: int
    exit_id: int

    def successors(self) -> Dict[int, List[Tuple[int, str]]]:
        succ: Dict[int, List[Tuple[int, str]]] = {n.id: [] for n in self.nodes}
        for e in self.edges:
            succ[e.source].append((e.target, e.kind))
        return succ

    def predecessors(self) -> Dict[int, List[Tuple[int, str]]]:
        pred: Dict[int, List[Tuple[int, str]]] = {n.id: [] for n in self.nodes}
        for e in self.edges:
            pred[e.target].append((e.source, e.kind))
        return pred

    def node_by_id(self, node_id: int) -> CFGNode:
        return next(n for n in self.nodes if n.id == node_id)


class _TempNode:
    """Mutable node used during lowering, renumbered at finalize time."""

    __slots__ = ("kind", "ast_node", "span", "seq")

    def __init__(self, kind: str, ast_node: Optional[ast.AST], span, seq: int):
        self.kind = kind
        self.ast_node = ast_node
        self.span = span  # (start_line, start_col, end_line, end_col)
        self.seq = seq


def _span_of(node: ast.AST) -> Tuple[int, int, int, int]:
    return (
        getattr(node, "lineno", -1),
        getattr(node, "col_offset", -1),
        getattr(node, "end_lineno", getattr(node, "lineno", -1)),
        getattr(node, "end_col_offset", -1),
    )


def _contains(node: ast.AST, types: tuple, *, into_nested_defs: bool = False) -> bool:
    """True if ``node`` contains an AST node of one of ``types``, without
    descending into nested function/class definitions (their bodies belong to
    other CFGs) unless requested."""
    stop = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, types):
            return True
        if not into_nested_defs and isinstance(child, stop):
            continue
        if _contains(child, types, into_nested_defs=into_nested_defs):
            return True
    return False


def _can_raise(stmt: ast.stmt) -> bool:
    """Over-approximate: if we can't prove the statement doesn't throw, it
    gets the exception edge (contract rule)."""
    if isinstance(stmt, (ast.Raise, ast.Assert, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor)):
        return True
    return _contains(stmt, (ast.Call, ast.Attribute, ast.Subscript, ast.Await))


def _stmt_kind(stmt: ast.stmt) -> str:
    if isinstance(stmt, ast.Return):
        return "return"
    if isinstance(stmt, ast.Raise):
        return "raise"
    if isinstance(stmt, ast.If):
        return "branch"
    if isinstance(stmt, (ast.While, ast.For, ast.AsyncFor)):
        return "loop"
    return "statement"


def _resume_kind(stmt: ast.stmt) -> str:
    """Edge kind of the statement's normal successor edge: generators resume
    after a yield, coroutines after an await."""
    if _contains(stmt, (ast.Yield, ast.YieldFrom)):
        return "yield"
    if _contains(stmt, (ast.Await,)):
        return "await_resume"
    return "fallthrough"


class _LoopFrame:
    __slots__ = ("header", "break_fringe")

    def __init__(self, header: _TempNode):
        self.header = header
        # (node, kind) dangling edges produced by `break` — connected to the
        # loop's successor once the loop is fully lowered.
        self.break_fringe: List[Tuple[_TempNode, str]] = []


class _FinallyFrame:
    """Tracks a try/finally protected region while its body is lowered.

    ``entry_fringe`` collects the abrupt-exit nodes (return / raise / break /
    continue) observed inside the protected region — they become incoming
    edges of the finally body, which is how the finally stays reachable when
    the try body never completes normally. ``abrupt`` records which exit kinds
    were seen so the finally's end re-emits a matching out-edge for each."""

    __slots__ = ("abrupt", "entry_fringe")

    def __init__(self):
        self.abrupt: Set[str] = set()
        self.entry_fringe: List[Tuple[_TempNode, str]] = []


class CFGBuilder:
    """Lowers one callable's AST into a :class:`ControlFlowGraph`."""

    def __init__(self) -> None:
        self._nodes: List[_TempNode] = []
        self._edges: List[Tuple[_TempNode, _TempNode, str]] = []
        self._seq = 0
        self._loop_stack: List[_LoopFrame] = []
        # Innermost-first chain of exception targets: (first handler node of a
        # try's except chain, finally-stack depth when it was pushed). The
        # depth lets exception edges mark only the finally frames *inside* the
        # protected region as abruptly exited — an exception caught by this
        # try's own handler re-enters the normal path.
        self._handler_stack: List[Tuple[_TempNode, int]] = []
        self._finally_stack: List[_FinallyFrame] = []

    # ---------------------------------------------------------------- helpers

    def _new_node(self, kind: str, ast_node: Optional[ast.AST], span=None) -> _TempNode:
        node = _TempNode(kind, ast_node, span or (_span_of(ast_node) if ast_node else (-1, -1, -1, -1)), self._seq)
        self._seq += 1
        self._nodes.append(node)
        return node

    def _connect(self, fringe: List[Tuple[_TempNode, str]], target: _TempNode) -> None:
        for source, kind in fringe:
            self._edges.append((source, target, kind))

    def _exception_target(self) -> Optional[_TempNode]:
        return self._handler_stack[-1][0] if self._handler_stack else None

    def _mark_exception_transit(self, node: Optional[_TempNode] = None) -> None:
        """Mark the finally frames an in-flight exception passes through:
        every frame inside the innermost handler's protected region, or all
        frames when the exception escapes the function."""
        depth = self._handler_stack[-1][1] if self._handler_stack else 0
        transit = self._finally_stack[depth:]
        for frame in transit:
            frame.abrupt.add("exception")
        if node is not None and transit:
            transit[-1].entry_fringe.append((node, "exception"))

    def _add_exception_edge(self, node: _TempNode, exit_node: _TempNode) -> None:
        target = self._exception_target() or exit_node
        self._edges.append((node, target, "exception"))
        self._mark_exception_transit()

    # ----------------------------------------------------------------- build

    def build(self, func: ast.AST) -> ControlFlowGraph:
        """``func`` is a FunctionDef / AsyncFunctionDef whose body is lowered.
        ENTRY takes the ``def`` line's span; EXIT the end of the callable."""
        entry = self._new_node("entry", None, span=(func.lineno, func.col_offset, func.lineno, func.col_offset))
        end_line = getattr(func, "end_lineno", func.lineno)
        end_col = getattr(func, "end_col_offset", -1)
        self._exit = self._new_node("exit", None, span=(end_line, end_col, end_line, end_col))

        fringe = self._lower_block(func.body, [(entry, "fallthrough")])
        # Fall-off-end is an implicit `return None`.
        self._connect([(n, "return") for n, _ in fringe], self._exit)

        return self._finalize(entry, self._exit)

    # ------------------------------------------------------------- lowering

    def _lower_block(
        self, stmts: List[ast.stmt], fringe: List[Tuple[_TempNode, str]]
    ) -> List[Tuple[_TempNode, str]]:
        for stmt in stmts:
            if not fringe:
                # Dead code after return/raise/break/continue: lower it anyway
                # (nodes unreachable from ENTRY are pruned at finalize).
                pass
            fringe = self._lower_stmt(stmt, fringe)
        return fringe

    def _lower_stmt(
        self, stmt: ast.stmt, fringe: List[Tuple[_TempNode, str]]
    ) -> List[Tuple[_TempNode, str]]:
        if isinstance(stmt, ast.If):
            return self._lower_if(stmt, fringe)
        if isinstance(stmt, ast.While):
            return self._lower_while(stmt, fringe)
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            return self._lower_for(stmt, fringe)
        if isinstance(stmt, ast.Try):
            return self._lower_try(stmt, fringe)
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            return self._lower_with(stmt, fringe)
        if isinstance(stmt, ast.Return):
            return self._lower_return(stmt, fringe)
        if isinstance(stmt, ast.Raise):
            return self._lower_raise(stmt, fringe)
        if isinstance(stmt, ast.Break):
            return self._lower_break(stmt, fringe)
        if isinstance(stmt, ast.Continue):
            return self._lower_continue(stmt, fringe)
        # Simple statement (incl. nested def/class = the binding statement).
        node = self._new_node(_stmt_kind(stmt), stmt)
        self._connect(fringe, node)
        if _can_raise(stmt):
            self._add_exception_edge(node, self._exit)
        resume = _resume_kind(stmt)
        if resume == "yield":
            # The generator may be abandoned at any yield.
            self._edges.append((node, self._exit, "yield"))
        return [(node, resume)]

    def _lower_if(self, stmt: ast.If, fringe):
        header = self._new_node("branch", stmt, span=_span_of(stmt.test))
        self._connect(fringe, header)
        if _can_raise_expr(stmt.test):
            self._add_exception_edge(header, self._exit)
        then_fringe = self._lower_block(stmt.body, [(header, "true")])
        if stmt.orelse:
            else_fringe = self._lower_block(stmt.orelse, [(header, "false")])
        else:
            else_fringe = [(header, "false")]
        return then_fringe + else_fringe

    def _lower_while(self, stmt: ast.While, fringe):
        header = self._new_node("loop", stmt, span=_span_of(stmt.test))
        self._connect(fringe, header)
        if _can_raise_expr(stmt.test):
            self._add_exception_edge(header, self._exit)

        frame = _LoopFrame(header)
        self._loop_stack.append(frame)
        body_fringe = self._lower_block(stmt.body, [(header, "true")])
        self._loop_stack.pop()
        self._connect([(n, "loop_back") for n, _ in body_fringe], header)

        # `while True:` / constant-true tests never take the false edge.
        always_true = isinstance(stmt.test, ast.Constant) and bool(stmt.test.value)
        out = [] if always_true else [(header, "false")]
        if stmt.orelse:
            out = self._lower_block(stmt.orelse, out)
        return out + frame.break_fringe

    def _lower_for(self, stmt, fringe):
        header = self._new_node("loop", stmt, span=_span_of(stmt.iter))
        self._connect(fringe, header)
        # The iterator protocol can raise.
        self._add_exception_edge(header, self._exit)

        frame = _LoopFrame(header)
        self._loop_stack.append(frame)
        body_fringe = self._lower_block(stmt.body, [(header, "true")])
        self._loop_stack.pop()
        self._connect([(n, "loop_back") for n, _ in body_fringe], header)

        out = [(header, "false")]
        if stmt.orelse:
            out = self._lower_block(stmt.orelse, out)
        return out + frame.break_fringe

    def _lower_try(self, stmt: ast.Try, fringe):
        has_finally = bool(stmt.finalbody)
        finally_frame = _FinallyFrame() if has_finally else None

        handler_entry: Optional[_TempNode] = None
        handler_nodes: List[_TempNode] = []
        if stmt.handlers:
            for handler in stmt.handlers:
                node = self._new_node("handler", handler, span=(
                    handler.lineno,
                    handler.col_offset,
                    getattr(handler.type, "end_lineno", handler.lineno) if handler.type else handler.lineno,
                    getattr(handler.type, "end_col_offset", -1) if handler.type else -1,
                ))
                handler_nodes.append(node)
            handler_entry = handler_nodes[0]

        if finally_frame is not None:
            self._finally_stack.append(finally_frame)

        # Protected region: body (+ else) raises reach this try's handlers.
        if handler_entry is not None:
            self._handler_stack.append((handler_entry, len(self._finally_stack)))
        body_fringe = self._lower_block(stmt.body, fringe)
        if stmt.orelse:
            body_fringe = self._lower_block(stmt.orelse, body_fringe)
        if handler_entry is not None:
            self._handler_stack.pop()

        # Handler chain: matched → handler body; unmatched → next handler,
        # falling off the chain propagates outward (outer handler or EXIT).
        handler_exit_fringes: List[Tuple[_TempNode, str]] = []
        for i, (handler, node) in enumerate(zip(stmt.handlers, handler_nodes)):
            hb_fringe = self._lower_block(handler.body, [(node, "true")])
            handler_exit_fringes.extend(hb_fringe)
            is_catch_all = handler.type is None
            if i + 1 < len(handler_nodes):
                self._edges.append((node, handler_nodes[i + 1], "false"))
            elif not is_catch_all:
                outer = self._exception_target() or self._exit
                self._edges.append((node, outer, "exception"))
                self._mark_exception_transit(node)

        normal_fringe = body_fringe + handler_exit_fringes

        if finally_frame is not None:
            self._finally_stack.pop()
            fin_entry = normal_fringe + finally_frame.entry_fringe
            fin_fringe = self._lower_block(stmt.finalbody, fin_entry)
            # Abrupt completions observed in the protected region re-emerge
            # from the finally body's end.
            for node, _kind in list(fin_fringe):
                if "return" in finally_frame.abrupt:
                    self._edges.append((node, self._exit, "return"))
                if "exception" in finally_frame.abrupt:
                    target = self._exception_target() or self._exit
                    self._edges.append((node, target, "exception"))
                if "break" in finally_frame.abrupt and self._loop_stack:
                    self._loop_stack[-1].break_fringe.append((node, "break"))
                if "continue" in finally_frame.abrupt and self._loop_stack:
                    self._edges.append((node, self._loop_stack[-1].header, "continue"))
            return fin_fringe

        return normal_fringe

    def _lower_with(self, stmt, fringe):
        node = self._new_node("statement", stmt, span=(
            stmt.lineno,
            stmt.col_offset,
            stmt.items[-1].context_expr.end_lineno,
            stmt.items[-1].context_expr.end_col_offset,
        ))
        self._connect(fringe, node)
        self._add_exception_edge(node, self._exit)
        return self._lower_block(stmt.body, [(node, "fallthrough")])

    def _lower_return(self, stmt: ast.Return, fringe):
        node = self._new_node("return", stmt)
        self._connect(fringe, node)
        if stmt.value is not None and _can_raise_expr(stmt.value):
            self._add_exception_edge(node, self._exit)
        if self._finally_stack:
            # Routed through the innermost finally; its end re-emits `return`.
            for frame in self._finally_stack:
                frame.abrupt.add("return")
            self._finally_stack[-1].entry_fringe.append((node, "return"))
            return []
        self._edges.append((node, self._exit, "return"))
        return []

    def _lower_raise(self, stmt: ast.Raise, fringe):
        node = self._new_node("raise", stmt)
        self._connect(fringe, node)
        target = self._exception_target() or self._exit
        self._edges.append((node, target, "exception"))
        self._mark_exception_transit(node)
        return []

    def _lower_break(self, stmt: ast.Break, fringe):
        node = self._new_node("statement", stmt)
        self._connect(fringe, node)
        if self._loop_stack:
            self._loop_stack[-1].break_fringe.append((node, "break"))
        for frame in self._finally_stack:
            frame.abrupt.add("break")
        if self._finally_stack:
            self._finally_stack[-1].entry_fringe.append((node, "break"))
        return []

    def _lower_continue(self, stmt: ast.Continue, fringe):
        node = self._new_node("statement", stmt)
        self._connect(fringe, node)
        if self._loop_stack:
            self._edges.append((node, self._loop_stack[-1].header, "continue"))
        for frame in self._finally_stack:
            frame.abrupt.add("continue")
        if self._finally_stack:
            self._finally_stack[-1].entry_fringe.append((node, "continue"))
        return []

    # ------------------------------------------------------------- finalize

    def _finalize(self, entry: _TempNode, exit_node: _TempNode) -> ControlFlowGraph:
        # 1. Prune nodes unreachable from ENTRY (dead code).
        succ: Dict[_TempNode, List[Tuple[_TempNode, str]]] = {n: [] for n in self._nodes}
        for s, t, k in self._edges:
            succ[s].append((t, k))
        reachable: Set[_TempNode] = set()
        stack = [entry]
        while stack:
            n = stack.pop()
            if n in reachable:
                continue
            reachable.add(n)
            for t, _ in succ[n]:
                if t not in reachable:
                    stack.append(t)
        reachable.add(exit_node)  # EXIT always exists even if nothing reaches it yet

        # 2. Synthetic escape edges: any reachable node that cannot reach EXIT
        #    sits in an infinite loop; give its loop header an `exception`
        #    edge to EXIT (documented above).
        live_edges = [(s, t, k) for s, t, k in self._edges if s in reachable and t in reachable]
        pred: Dict[_TempNode, List[_TempNode]] = {n: [] for n in reachable}
        for s, t, _ in live_edges:
            pred[t].append(s)
        reaches_exit: Set[_TempNode] = set()
        stack = [exit_node]
        while stack:
            n = stack.pop()
            if n in reaches_exit:
                continue
            reaches_exit.add(n)
            for p in pred[n]:
                if p not in reaches_exit:
                    stack.append(p)
        stuck = [n for n in reachable if n not in reaches_exit]
        if stuck:
            headers = [n for n in stuck if n.kind == "loop"] or stuck
            for header in headers:
                live_edges.append((header, exit_node, "exception"))

        # 3. Renumber in source-span order: ENTRY = 0, EXIT = last.
        middle = sorted(
            (n for n in reachable if n is not entry and n is not exit_node),
            key=lambda n: (n.span, n.seq),
        )
        ordered = [entry] + middle + [exit_node]
        ids = {n: i for i, n in enumerate(ordered)}

        nodes = [
            CFGNode(
                id=ids[n],
                kind=n.kind,
                start_line=n.span[0],
                start_column=n.span[1],
                end_line=n.span[2],
                end_column=n.span[3],
                ast_node=n.ast_node,
            )
            for n in ordered
        ]
        seen: Set[Tuple[int, int, str]] = set()
        edges: List[CFGEdge] = []
        for s, t, k in sorted(live_edges, key=lambda e: (ids[e[0]], ids[e[1]], e[2])):
            key = (ids[s], ids[t], k)
            if key in seen:
                continue
            seen.add(key)
            edges.append(CFGEdge(source=ids[s], target=ids[t], kind=k))

        return ControlFlowGraph(
            nodes=nodes, edges=edges, entry_id=ids[entry], exit_id=ids[exit_node]
        )


def _can_raise_expr(expr: ast.expr) -> bool:
    return isinstance(expr, (ast.Call, ast.Attribute, ast.Subscript, ast.Await)) or _contains(
        expr, (ast.Call, ast.Attribute, ast.Subscript, ast.Await)
    )


def build_cfg(func: ast.AST) -> ControlFlowGraph:
    """Build the exceptional, statement-level CFG of one callable."""
    return CFGBuilder().build(func)
