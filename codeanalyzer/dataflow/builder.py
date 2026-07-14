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

"""The level-3 orchestrator: symbol table + call graph → program graphs.

``build_program_graphs`` is the single entry point ``Codeanalyzer.analyze``
calls at ``-a 3``. It re-parses each module file with the stdlib ``ast`` (the
same parser the symbol table used), maps every ``PyCallable`` to its def node
by ``(file, start_line)`` — which is what guarantees graph nodes join back to
symbol-table signatures — then runs the construction ladder:

    per callable: CFG → dominance → facts (module-qualified globals)
    whole program: SCC condensation → summary fixpoint → SDG assembly

The call graph and Jedi-resolved callsites are frozen oracles: targets are
looked up, never re-inferred. Callables whose AST cannot be recovered (file
changed on disk, decorators moving line numbers, generated code) are skipped
with a warning — their callers still treat them as external pass-through, so
the result degrades gracefully instead of crashing (contract rule).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from codeanalyzer.dataflow.access_paths import _PathExtractor, _calls_in
from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.dataflow.pdg import build_pdg
from codeanalyzer.dataflow.sdg import ProgramGraphsIR, assemble_sdg
from codeanalyzer.dataflow.summaries import CallSite, FunctionInfo, compute_summaries
from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyClass, PyModule
from codeanalyzer.utils import logger

DEFAULT_K_LIMIT = 3


def _walk_callables(
    module: PyModule,
) -> List[Tuple[PyCallable, Tuple[PyCallable, ...]]]:
    """Every callable in the module with its chain of enclosing callables."""
    out: List[Tuple[PyCallable, Tuple[PyCallable, ...]]] = []

    def from_callable(c: PyCallable, chain: Tuple[PyCallable, ...]) -> None:
        out.append((c, chain))
        for inner in (c.callables or {}).values():
            from_callable(inner, chain + (c,))
        for cls in (c.types or {}).values():
            from_class(cls, chain + (c,))

    def from_class(cls: PyClass, chain: Tuple[PyCallable, ...]) -> None:
        for m in (cls.callables or {}).values():
            from_callable(m, chain)
        for inner in (cls.types or {}).values():
            from_class(inner, chain)

    for fn in (module.functions or {}).values():
        from_callable(fn, ())
    for cls in (module.types or {}).values():
        from_class(cls, ())
    return out


def _locals_of(func: ast.AST) -> Set[str]:
    from codeanalyzer.dataflow.access_paths import _assigned_names, _param_names

    return set(_param_names(func)) | _assigned_names(func)


def _base_types(c: PyCallable) -> Dict[str, Optional[str]]:
    types: Dict[str, Optional[str]] = {}
    for p in c.parameters or []:
        types[p.name] = p.type
    for v in c.local_variables or []:
        types.setdefault(v.name, v.type)
    return types


def _class_index(app: PyApplication) -> Dict[str, PyClass]:
    from codeanalyzer.semantic_analysis.call_graph import iter_classes_in_symbol_table

    return {c.signature: c for c in iter_classes_in_symbol_table(app.symbol_table)}


def _callable_index(app: PyApplication) -> Dict[str, PyCallable]:
    from codeanalyzer.semantic_analysis.call_graph import iter_callables_in_symbol_table

    return {c.signature: c for c in iter_callables_in_symbol_table(app.symbol_table)}


def _match_args(
    call: ast.Call,
    callee: PyCallable,
    extractor: _PathExtractor,
    receiver_path: Optional[str],
) -> Tuple[Tuple[str, Optional[str]], ...]:
    """Positional/keyword-match actual access paths to callee param names.
    The receiver (or constructed object) binds the leading self/cls param."""
    params = [p.name for p in (callee.parameters or [])]
    pairs: List[Tuple[str, Optional[str]]] = []
    positional = list(params)
    if params and params[0] in ("self", "cls"):
        if receiver_path is not None:
            pairs.append((params[0], receiver_path))
        positional = params[1:]
    for name, arg in zip(positional, call.args):
        if isinstance(arg, ast.Starred):
            break
        pairs.append((name, extractor.path_of(arg)))
    for kw in call.keywords:
        if kw.arg and kw.arg in params:
            pairs.append((kw.arg, extractor.path_of(kw.value)))
    return tuple(pairs)


def build_function_pdgs(
    app: PyApplication,
    k: int = DEFAULT_K_LIMIT,
    *,
    oracle_factory: Callable[[PyCallable, ast.AST], object],
) -> Tuple[Dict[str, FunctionInfo], Dict[str, ast.AST]]:
    """Intraprocedural phase only: one ``FunctionInfo`` (CFG → PDG) per
    callable, keyed by signature, with no SDG/summary/callsite work.

    ``oracle_factory(pycallable, func_ast)`` supplies the may-alias oracle per
    callable — the matched def AST is threaded through so the primary L4 oracle
    (:func:`~codeanalyzer.dataflow.scalpel_oracle.make_alias_oracle`) can build
    Scalpel's SSA from it; ``TypeBasedAliasOracle`` for the plain L4 path and
    ``SyntacticOracle`` for L3 simply ignore the AST argument.

    Returns ``(infos, func_asts)`` rather than bare PDGs so that the L4
    orchestrator (:func:`build_program_graphs`) still has both the
    ``FunctionInfo`` records its callsite/summary/SDG phases mutate and the
    matched def nodes its Phase 2 reads. L3 callers just read ``info.pdg`` per
    signature and ignore ``func_asts``.
    """
    infos: Dict[str, FunctionInfo] = {}
    func_asts: Dict[str, ast.AST] = {}

    for file_key, module in sorted(app.symbol_table.items()):
        path = Path(module.file_path)
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError) as exc:
            logger.warning(f"level 3: skipping {path} (unparseable: {exc})")
            continue

        def_index: Dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                def_index[node.lineno] = node

        for pycallable, chain in _walk_callables(module):
            func = def_index.get(pycallable.start_line)
            if func is None or func.name != pycallable.name:
                logger.warning(
                    f"level 3: no AST match for {pycallable.signature} "
                    f"({path}:{pycallable.start_line}); treated as external"
                )
                continue

            enclosing_locals: Set[str] = set()
            for enclosing in chain:
                enclosing_ast = def_index.get(enclosing.start_line)
                if enclosing_ast is not None:
                    enclosing_locals |= _locals_of(enclosing_ast)

            oracle = oracle_factory(pycallable, func)
            pdg = build_pdg(
                func,
                enclosing_locals=enclosing_locals,
                oracle=oracle,
                k=k,
                global_qualifier=module.module_name,
            )
            infos[pycallable.signature] = FunctionInfo(
                signature=pycallable.signature, pdg=pdg, oracle=oracle
            )
            func_asts[pycallable.signature] = func

    return infos, func_asts


def emit_l3_body(
    app: PyApplication,
    infos: Dict[str, FunctionInfo],
    sig_to_id: Dict[str, str],
    graphs: Set[str],
) -> None:
    """Project each callable's syntactic PDG onto the v2 tree at L3.

    For every callable that produced a ``FunctionInfo`` in
    :func:`build_function_pdgs` (syntactic oracle), this writes onto the
    matching ``PyCallable`` in ``app``'s symbol table:

    * ``body`` — one node per CFG node, keyed by its LOCAL id (``"@entry"``/
      ``"@exit"`` for the synthetic bookends, ``"line:col"`` for real
      statements — the same key format L1 uses). A statement position an L1
      pass already materialized as a ``call`` node lands on the SAME local key,
      so it keeps its ``call`` kind and L2-resolved ``callee`` in place (no
      re-keying, no duplication) and is only given the byte-offset ``span`` L1
      could not compute.
    * ``cfg`` — one ``CfgEdge`` per CFG edge, endpoints as local ids.
    * ``cdg`` — the PDG's control-dependence edges.
    * ``ddg`` — the PDG's syntactic def-use edges, each with ``prov=["ssa"]``
      (no points-to provenance at L3; that is the L4 delta).

    ``graphs`` scopes the edge lists exactly as the dormant
    :func:`to_program_graphs` does: ``cfg`` needs ``"cfg"``; ``cdg`` needs
    ``"pdg"``/``"sdg"``; ``ddg`` needs those or ``"dfg"``. ``body`` is always
    populated. Callables absent from ``infos`` (unrecovered AST) are skipped.
    """
    from codeanalyzer.dataflow.identity import IdentityMap
    from codeanalyzer.schema.py_schema import (
        BodyNode,
        CdgEdge,
        CfgEdge,
        DdgEdge,
        Span,
        byte_offsets,
    )

    want_pdg = bool({"pdg", "sdg"} & graphs)
    want_cfg = "cfg" in graphs
    want_ddg = want_pdg or "dfg" in graphs

    def _span_of(source: str, node) -> Optional["Span"]:
        if not source or node.start_line < 1:
            return None
        return Span(
            start=(node.start_line, node.start_column),
            end=(node.end_line, node.end_column),
            bytes=byte_offsets(
                source,
                node.start_line,
                node.start_column,
                node.end_line,
                node.end_column,
            ),
        )

    for module in app.symbol_table.values():
        source = module.source
        for pycallable, _chain in _walk_callables(module):
            info = infos.get(pycallable.signature)
            if info is None:
                continue
            pdg = info.pdg
            callable_id = sig_to_id.get(pycallable.signature) or pycallable.id
            im = IdentityMap.for_function(callable_id, pdg)

            for node in pdg.cfg.nodes:
                local = im.local(node.id)
                if node.id == pdg.cfg.entry_id:
                    pycallable.body[local] = BodyNode(kind="entry")
                    continue
                if node.id == pdg.cfg.exit_id:
                    pycallable.body[local] = BodyNode(kind="exit")
                    continue
                span = _span_of(source, node)
                # An L1 `call` node was keyed by its LOCAL "line:col"; this CFG
                # node at the same position lands on the SAME key, so keep the
                # node's `call` kind and L2-resolved `callee` in place and just
                # fill any missing span — never re-key or duplicate it.
                existing = pycallable.body.get(local)
                if existing is not None:
                    if existing.span is None and span is not None:
                        existing.span = span
                    continue
                pycallable.body[local] = BodyNode(kind=node.kind, span=span)

            if want_cfg:
                pycallable.cfg = [
                    CfgEdge(
                        src=im.local(e.source),
                        dst=im.local(e.target),
                        kind=e.kind,
                    )
                    for e in pdg.cfg.edges
                ]
            if want_pdg:
                pycallable.cdg = [
                    CdgEdge(src=im.local(e.source), dst=im.local(e.target))
                    for e in pdg.edges
                    if e.type == "CDG"
                ]
            if want_ddg:
                pycallable.ddg = [
                    DdgEdge(
                        src=im.local(e.source),
                        dst=im.local(e.target),
                        var=e.var,
                        prov=["ssa"],
                    )
                    for e in pdg.edges
                    if e.type == "DDG"
                ]


def build_program_graphs(
    app: PyApplication,
    k: int = DEFAULT_K_LIMIT,
    *,
    oracle_factory: Callable[[PyCallable, ast.AST], object] = (
        lambda c, fast: TypeBasedAliasOracle(_base_types(c))
    ),
) -> ProgramGraphsIR:
    """Build CFG/PDG per callable and the whole-program SDG.

    ``oracle_factory(pycallable, func_ast)`` selects the per-callable may-alias
    oracle. The default is the frozen :class:`TypeBasedAliasOracle` (preserving
    the historical behavior); the L4 path in ``core`` injects
    :func:`~codeanalyzer.dataflow.scalpel_oracle.make_alias_oracle` so Scalpel
    is the primary oracle with the type-based total fallback.
    """
    class_idx = _class_index(app)
    callable_idx = _callable_index(app)

    infos, func_asts = build_function_pdgs(app, k, oracle_factory=oracle_factory)

    # Callsites and nested defs, now that every signature is known.
    for sig, info in infos.items():
        pycallable = callable_idx[sig]
        func = func_asts[sig]
        extractor = _PathExtractor(info.pdg.scope, k)

        calls_by_pos: Dict[Tuple[int, int], Tuple[int, ast.Call]] = {}
        calls_by_line: Dict[int, Tuple[int, ast.Call]] = {}
        for node in info.pdg.cfg.nodes:
            if node.ast_node is None:
                continue
            for call in _calls_in(node.ast_node):
                pos = (call.lineno, call.col_offset)
                calls_by_pos.setdefault(pos, (node.id, call))
                calls_by_line.setdefault(call.lineno, (node.id, call))

        for site in pycallable.call_sites or []:
            target = site.callee_signature
            if not target:
                continue
            if target in class_idx and target not in infos:
                target = f"{target}.__init__"  # constructor → its initializer
            if target not in infos:
                continue  # external or unrecovered: pass-through posture

            located = calls_by_pos.get((site.start_line, site.start_column))
            if located is None:
                located = calls_by_line.get(site.start_line)
            if located is None:
                continue
            node_id, call = located

            receiver_path: Optional[str] = None
            if isinstance(call.func, ast.Attribute):
                receiver_path = extractor.path_of(call.func.value)
            elif site.is_constructor_call:
                # p = Box(...) binds the constructed object (self) to p.
                owner = info.pdg.cfg.node_by_id(node_id).ast_node
                if (
                    isinstance(owner, ast.Assign)
                    and len(owner.targets) == 1
                    and isinstance(owner.targets[0], (ast.Name, ast.Attribute))
                ):
                    receiver_path = extractor.path_of(owner.targets[0])

            info.call_sites.append(
                CallSite(
                    node_id=node_id,
                    targets=(target,),
                    arg_paths=_match_args(call, callable_idx[target], extractor, receiver_path),
                    line=site.start_line,
                )
            )

        for node in info.pdg.cfg.nodes:
            if isinstance(node.ast_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested_sig = f"{sig}.{node.ast_node.name}"
                if nested_sig in infos:
                    info.nested_defs.append((node.id, nested_sig))

    call_edges = [
        (e.src, e.dst)
        for e in app.call_graph
        if e.src in infos and e.dst in infos
    ]
    # Callsite resolutions are part of the same oracle (they may include
    # constructor retargets the edge list lacks).
    for sig, info in infos.items():
        for cs in info.call_sites:
            for t in cs.targets:
                call_edges.append((sig, t))

    summaries = compute_summaries(infos, sorted(set(call_edges)))
    return assemble_sdg(infos, summaries, k)


def emit_l4(
    app: PyApplication,
    ir: ProgramGraphsIR,
    sig_to_id: Dict[str, str],
) -> None:
    """Project the interprocedural L4 delta of ``ir`` onto the v2 tree.

    Layered strictly *on top of* the L3 syntactic overlay (which
    :func:`emit_l3_body` has already written), so L3 ⊆ L4 holds by
    construction — this function only *adds* keys/edges, never rewrites L3's.
    Per callable it emits:

    * **synthetic param vertices** — each :class:`ParamNode`
      (``formal_in``/``formal_out``/``actual_in``/``actual_out``) becomes a
      ``body`` node keyed by its LOCAL id (``@formal_in:<i>``, ``@formal_out``,
      ``<callsite-local>/actual_in:<i>``, …), carrying the variable it models in
      ``of`` and — for actuals — the owning callsite's local id in ``parent``;
    * **summary edges** — each same-signature ``SUMMARY`` SDG edge (a callee's
      transitive actual_in → actual_out pass-through) lands on the callable's
      ``summary`` as a :class:`SummaryEdge` of LOCAL ids;
    * **param_in / param_out** — each cross-function ``PARAM_IN`` / ``PARAM_OUT``
      SDG edge becomes an application-level :class:`ParamEdge` of GLOBAL ids
      (``<callable-id>@<local>``), resolved through the endpoint functions'
      identity maps.

    ``CALL`` SDG edges are dropped — they duplicate the call graph. ``ddg``
    points-to provenance and taint are *not* emitted here (later tasks).
    """
    from codeanalyzer.dataflow.identity import IdentityMap
    from codeanalyzer.schema.py_schema import BodyNode, ParamEdge, SummaryEdge

    # L4 emission is additive (it *appends* summary/param edges), so it must
    # first clear any L4 state a reused cache left on these live objects —
    # otherwise repeated ``-a 4`` runs against the same cache_dir would keep
    # growing the lists (1→2→3→…). L3's emit reassigns its lists and is already
    # idempotent; L4 has to reset explicitly. App-scope lists reset once here,
    # before the loop that appends to them; per-callable ``summary`` is reset in
    # the (a) loop below.
    app.param_in = []
    app.param_out = []

    # Tree callables by signature: these are the live objects in ``app``'s
    # symbol table, so mutating them mutates the emitted tree.
    sig_to_callable: Dict[str, PyCallable] = {}
    for module in app.symbol_table.values():
        for pycallable, _chain in _walk_callables(module):
            sig_to_callable[pycallable.signature] = pycallable

    # One IdentityMap per function, each folding *that* function's synthetic
    # param vertices, so both intra-function (summary) and cross-function
    # (param_in/param_out) endpoints resolve uniformly by node id.
    ims: Dict[str, IdentityMap] = {}
    for sig, fg in ir.functions.items():
        pycallable = sig_to_callable.get(sig)
        callable_id = sig_to_id.get(sig) or (pycallable.id if pycallable else sig)
        ims[sig] = IdentityMap.for_function(
            callable_id, fg.pdg, param_nodes=fg.param_nodes
        )

    # (a) synthetic param vertices onto each callable's body.
    for sig, fg in ir.functions.items():
        pycallable = sig_to_callable.get(sig)
        if pycallable is None:
            continue
        # Idempotency under cache reuse: drop L4 state a prior run left on this
        # live callable before re-emitting. ``summary`` is append-built below, so
        # reset it. The param vertices are re-added by keyed assignment (already
        # idempotent), but a code change between runs could leave stale ones — so
        # defensively drop any pre-existing param-kind body nodes first.
        pycallable.summary = []
        for k in [
            k
            for k, n in pycallable.body.items()
            if n.kind in ("formal_in", "formal_out", "actual_in", "actual_out")
        ]:
            del pycallable.body[k]
        im = ims[sig]
        for pn in fg.param_nodes:
            parent = im.local(pn.call_node) if pn.call_node is not None else None
            pycallable.body[im.local(pn.id)] = BodyNode(
                kind=pn.kind, of=pn.var, parent=parent
            )

    # (b/c/d) SDG edges → summary / param_in / param_out; CALL dropped.
    for e in ir.sdg_edges:
        if e.type == "CALL":
            continue
        if e.type == "SUMMARY":
            pycallable = sig_to_callable.get(e.source_sig)
            im = ims.get(e.source_sig)
            if pycallable is None or im is None:
                continue
            pycallable.summary.append(
                SummaryEdge(
                    src=im.local(e.source_node),
                    dst=im.local(e.target_node),
                )
            )
        elif e.type in ("PARAM_IN", "PARAM_OUT"):
            src_im = ims.get(e.source_sig)
            dst_im = ims.get(e.target_sig)
            if src_im is None or dst_im is None:
                continue
            edge = ParamEdge(
                src=src_im.global_id(e.source_node),
                dst=dst_im.global_id(e.target_node),
            )
            (app.param_in if e.type == "PARAM_IN" else app.param_out).append(edge)


def _ddg_local_set(im, pdg) -> Set[Tuple[str, str, Optional[str]]]:
    """The DDG edges of ``pdg`` as a set of ``(local_src, local_dst, var)``.

    Keyed by LOCAL ids (``"line:col"`` / ``"@entry"``…), which are position-
    based and thus stable across oracle choice — so a syntactic-oracle set and
    a real-oracle set are directly comparable through the *same* identity map.
    """
    return {
        (im.local(e.source), im.local(e.target), e.var)
        for e in pdg.edges
        if e.type == "DDG"
    }


def emit_ddg_pointsto_delta(
    app: PyApplication,
    syntactic_infos: Dict[str, FunctionInfo],
    ir: ProgramGraphsIR,
    sig_to_id: Dict[str, str],
) -> None:
    """Append the semantic ``ddg`` delta at L4: the alias-derived def-use edges
    the real (Scalpel-primary) oracle produces beyond the L3 syntactic
    (name-equality) set, each tagged ``prov=["points-to"]``.

    Strictly *additive*: :func:`emit_l3_body` has already written the syntactic
    ``ssa`` edges, and this function never touches them — it only appends the
    points-to delta. For every signature present in *both* ``syntactic_infos``
    (the L3 syntactic PDGs) and ``ir.functions`` (the real-oracle PDGs):

    * build one :class:`IdentityMap` from the real PDG — the CFG is
      oracle-independent, so node ids and their ``"line:col"`` locals coincide
      between the two builds, and a single map resolves both sets;
    * ``S`` = the syntactic-oracle DDG set, ``F`` = the real-oracle DDG set,
      both as ``(local_src, local_dst, var)``;
    * for each edge in ``F − S`` (sorted for determinism) whose endpoints exist
      in the callable's ``body`` (defensive — they are CFG nodes), append a
      ``DdgEdge(prov=["points-to"])``.
    """
    from codeanalyzer.dataflow.identity import IdentityMap
    from codeanalyzer.schema.py_schema import DdgEdge

    # Tree callables by signature: the live objects in ``app``'s symbol table,
    # so appending to their ``ddg`` mutates the emitted tree in place.
    sig_to_callable: Dict[str, PyCallable] = {}
    for module in app.symbol_table.values():
        for pycallable, _chain in _walk_callables(module):
            sig_to_callable[pycallable.signature] = pycallable

    for sig, fg in ir.functions.items():
        syn = syntactic_infos.get(sig)
        pycallable = sig_to_callable.get(sig)
        if syn is None or pycallable is None:
            continue
        callable_id = sig_to_id.get(sig) or pycallable.id
        im = IdentityMap.for_function(callable_id, fg.pdg)

        # Idempotency under cache reuse: strip any points-to edges a prior run
        # appended, so this append is idempotent regardless of whether
        # ``emit_l3_body`` reassigned ``ddg`` this run (it only does when the
        # ``--graphs`` selector includes ddg). The ``ssa`` edges are left
        # untouched — they are L3's and this delta is strictly additive over them.
        pycallable.ddg = [e for e in pycallable.ddg if e.prov != ["points-to"]]

        delta = _ddg_local_set(im, fg.pdg) - _ddg_local_set(im, syn.pdg)
        for src, dst, var in sorted(delta, key=lambda t: (t[0], t[1], t[2] or "")):
            if src not in pycallable.body or dst not in pycallable.body:
                continue
            pycallable.ddg.append(
                DdgEdge(src=src, dst=dst, var=var, prov=["points-to"])
            )


VALID_GRAPHS = ("cfg", "dfg", "pdg", "sdg")


def to_program_graphs(ir: ProgramGraphsIR, graphs: Set[str]):
    """Project the IR onto the ``program_graphs`` schema section, scoped by
    the ``--graphs`` selector. ``dfg`` emits the PDG's DDG edges only;
    ``sdg`` implies the dependence edges it is stitched over."""
    from codeanalyzer.schema.py_schema import (
        PyCFG,
        PyCFGEdge,
        PyFunctionGraphs,
        PyGraphNode,
        PyParamNode,
        PyPDG,
        PyPDGEdge,
        PyProgramGraphs,
        PySDGEdge,
        PySDGEndpoint,
    )

    want_pdg = bool({"pdg", "sdg"} & graphs)
    want_dfg = want_pdg or "dfg" in graphs
    functions: Dict[str, "PyFunctionGraphs"] = {}
    for sig in sorted(ir.functions):
        fg = ir.functions[sig]
        out = PyFunctionGraphs()
        if "cfg" in graphs:
            out.cfg = PyCFG(
                nodes=[
                    PyGraphNode(
                        id=n.id,
                        kind=n.kind,
                        start_line=n.start_line,
                        end_line=n.end_line,
                        start_column=n.start_column,
                        end_column=n.end_column,
                    )
                    for n in fg.pdg.cfg.nodes
                ],
                edges=[
                    PyCFGEdge(source=e.source, target=e.target, kind=e.kind)
                    for e in fg.pdg.cfg.edges
                ],
            )
        edges: List["PyPDGEdge"] = []
        if want_pdg:
            edges.extend(
                PyPDGEdge(source=e.source, target=e.target, type="CDG")
                for e in fg.pdg.edges
                if e.type == "CDG"
            )
        if want_dfg:
            edges.extend(
                PyPDGEdge(source=e.source, target=e.target, type="DDG", var=e.var)
                for e in fg.ddg
            )
            edges.extend(
                PyPDGEdge(source=e.source, target=e.target, type=e.type, var=e.var)
                for e in fg.extra_edges
                if e.type == "DDG" or want_pdg
            )
        if edges:
            edges.sort(key=lambda e: (e.source, e.target, e.type, e.var or ""))
            out.pdg = PyPDG(edges=edges)
        if "sdg" in graphs:
            out.param_nodes = [
                PyParamNode(
                    id=p.id,
                    kind=p.kind,
                    var=p.var,
                    call_node=p.call_node,
                    start_line=p.start_line,
                    end_line=p.end_line,
                )
                for p in fg.param_nodes
            ]
        functions[sig] = out

    sdg_edges = []
    if "sdg" in graphs:
        sdg_edges = [
            PySDGEdge(
                source=PySDGEndpoint(signature=e.source_sig, node=e.source_node),
                target=PySDGEndpoint(signature=e.target_sig, node=e.target_node),
                type=e.type,
                var=e.var,
            )
            for e in ir.sdg_edges
        ]

    return PyProgramGraphs(
        schema_version="1.0.0",
        k_limit=ir.k_limit,
        functions=functions,
        sdg_edges=sdg_edges,
    )
