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
from typing import Dict, List, Optional, Set, Tuple

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
        for inner in (c.inner_callables or {}).values():
            from_callable(inner, chain + (c,))
        for cls in (c.inner_classes or {}).values():
            from_class(cls, chain + (c,))

    def from_class(cls: PyClass, chain: Tuple[PyCallable, ...]) -> None:
        for m in (cls.methods or {}).values():
            from_callable(m, chain)
        for inner in (cls.inner_classes or {}).values():
            from_class(inner, chain)

    for fn in (module.functions or {}).values():
        from_callable(fn, ())
    for cls in (module.classes or {}).values():
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


def build_program_graphs(
    app: PyApplication,
    k: int = DEFAULT_K_LIMIT,
) -> ProgramGraphsIR:
    """Build CFG/PDG per callable and the whole-program SDG."""
    class_idx = _class_index(app)
    callable_idx = _callable_index(app)

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

            oracle = TypeBasedAliasOracle(_base_types(pycallable))
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
        (e.source, e.target)
        for e in app.call_graph
        if e.source in infos and e.target in infos
    ]
    # Callsite resolutions are part of the same oracle (they may include
    # constructor retargets the edge list lacks).
    for sig, info in infos.items():
        for cs in info.call_sites:
            for t in cs.targets:
                call_edges.append((sig, t))

    summaries = compute_summaries(infos, sorted(set(call_edges)))
    return assemble_sdg(infos, summaries, k)


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
