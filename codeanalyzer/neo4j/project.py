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

"""``project()`` — the pure projection from the canonical :class:`PyApplication`
IR to graph rows. It walks the same recursive symbol table the call-graph builder
walks, but instead of collecting callables it emits nodes + edges. No I/O: the
writers (cypher snapshot / bolt incremental) consume the returned
:class:`GraphRows`.

Modelling decisions (mirror of the TypeScript backend):
  - signature-keyed declarations (PyClass, PyCallable) carry a shared ``:PySymbol``
    label (the global-identity / MERGE key).
  - call sites, decorators, class attributes and variables are first-class nodes.
  - call-graph endpoints absent from the symbol table become ``:PyExternal`` ghost
    nodes, so RPC / third-party / framework edges are preserved (matching the
    analyzer's own ghost-node behaviour).
  - every project-owned node carries an internal ``_module`` provenance prop, so
    the incremental writer can delete exactly what a re-analyzed module emitted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

from codeanalyzer.neo4j.schema import SCHEMA_VERSION
from codeanalyzer.neo4j.rows import GraphRows, NodeRef, Props, RowBuilder, prune
from codeanalyzer.schema import (
    PyApplication,
    PyCallable,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyModule,
    PyVariableDeclaration,
)
from codeanalyzer.schema.py_schema import PyDecorator


def project(app: PyApplication, app_name: str, sig_to_id: dict,
            analyzer: Optional[Any] = None) -> GraphRows:
    """``analyzer`` is the envelope-level ``PyAnalyzerInfo`` (the keystone home
    for analyzer identity); the caller that holds the ``Analysis`` envelope
    passes it through so the :PyApplication node carries it as props."""
    b = RowBuilder()

    app_ref = b.node(
        ["PyApplication"],
        "name",
        app_name,
        prune(
            {
                "schema_version": SCHEMA_VERSION,
                "analyzer_name": analyzer.name if analyzer else None,
                "analyzer_version": analyzer.version if analyzer else None,
                "repo_uri": app.repository.uri if app.repository else None,
                "source_revision": app.repository.revision if app.repository else None,
                "repo_dirty": app.repository.dirty if app.repository else None,
            }
        ),
    )

    # Endpoints listed in app.external_symbols become :PyExternal ghost nodes; the
    # rest are declared :PySymbol nodes emitted here (keyed by their can:// id,
    # resolved through ``sig_to_id``). Both the module-body projection (for
    # PY_EXTENDS / PY_RESOLVES_TO) and the PY_CALLS twin below share this split.
    externals = app.external_symbols or {}

    # file key → module can:// id, so resolved PY_IMPORTS edges land on the v2
    # module merge key (id) rather than the legacy file_key property.
    module_id_by_key = {k: m.id for k, m in app.symbol_table.items()}

    for file_key, mod in app.symbol_table.items():
        mod_ref = b.node(["PyModule"], "id", mod.id, _module_props(mod, file_key))
        b.edge("PY_HAS_MODULE", app_ref, mod_ref)
        _project_module_body(b, file_key, mod_ref, mod, externals, sig_to_id, module_id_by_key)

    # The aggregated :PY_CALLS twin.
    for e in app.call_graph:
        src = _call_endpoint(b, e.src, externals, sig_to_id)
        tgt = _call_endpoint(b, e.dst, externals, sig_to_id)
        b.edge(
            "PY_CALLS", src, tgt, _call_edge_props(e.weight, list(e.prov or []))
        )

    # Level-3 CPG overlay: each callable's v2 body/cfg/cdg/ddg. Idempotent under
    # MERGE — a no-op when no callable carries L3 fields (levels 1/2).
    _project_program_graphs(b, app, externals, sig_to_id)

    return b.finish()


# ----------------------------------------------------------------------------------------------
# Level-3 CPG overlay
# ----------------------------------------------------------------------------------------------


def _global_ordinal(callable_id: str, local_key: str) -> str:
    """The globally-unique PyCFGNode merge key for a callable's body node: the
    callable's ``can://`` id joined to its LOCAL body key with a single ``@``.
    The synthetic bookends already carry the leading ``@`` (``"@entry"``/
    ``"@exit"``); real statements are bare ``"line:col"`` and gain the ``@``.

    This MUST agree with :meth:`IdentityMap.global_id` for the same node, so the
    JSON ``body``/``cfg`` projection and this Neo4j projection land on one node
    identity (two-projection agreement)."""
    return (
        f"{callable_id}{local_key}"
        if local_key.startswith("@")
        else f"{callable_id}@{local_key}"
    )


def _cfg_ref(callable_id: str, local_key: str) -> NodeRef:
    return NodeRef("PyCFGNode", "id", _global_ordinal(callable_id, local_key))


def _project_program_graphs(
    b: RowBuilder, app: PyApplication, externals: dict, sig_to_id: dict
) -> None:
    """Level-3 CPG overlay, projected off each callable's v2 ``body``/``cfg``/
    ``cdg``/``ddg`` (populated by ``emit_l3_body`` at ``-a 3``; empty otherwise).

    Node label ``PyCFGNode`` (merge key ``id`` = the GLOBAL ordinal
    ``<callable can:// id>@<local body key>`` — identical to the JSON body key
    prefixed with the callable id, so the two projections agree). Edges:
    ``PY_HAS_CFG_NODE`` from the owning callable, ``PY_CFG_NEXT`` (prop ``kind``)
    over the CFG, ``PY_CDG`` over control dependence, and ``PY_DDG`` (props
    ``var``/``prov``) over data dependence. The vocabulary is cross-language in
    shape but PY_-namespaced like every other row family, so a multi-language
    database never mingles analyzers' dependence edges.

    L4 (``-a 4``) layers the interprocedural delta onto the same node label:
    parameter-passing vertices (``formal_in``/``formal_out``/``actual_in``/
    ``actual_out``) carry ``var`` (the variable/return they model, from
    ``BodyNode.of``) and ``call_node`` (the owning callsite local id, from
    ``BodyNode.parent``) instead of span-derived lines; ``PY_SUMMARY`` runs over
    each callable's transitive pass-throughs (LOCAL ids → global refs), and the
    app-level ``PY_PARAM_IN``/``PY_PARAM_OUT`` edges connect actual↔formal
    vertices across callables (endpoints are already GLOBAL ordinals matching the
    emitted ``PyCFGNode`` keys). All idempotent under MERGE — no-ops below L4."""
    from codeanalyzer.semantic_analysis.call_graph import _walk_module_callables

    for file_key, mod in app.symbol_table.items():
        for c in _walk_module_callables(mod):
            if not c.id:
                continue  # unstamped callable — assign_ids must run first
            owner = _sym(c.id)  # the :PyCallable node, keyed by its can:// id
            for local_key, node in (c.body or {}).items():
                span = node.span
                # L4 param vertices carry the variable they model (``of``) and
                # their owning callsite (``parent``) instead of span lines; both
                # are None on ordinary statement nodes and pruned away there.
                ref = b.node(
                    ["PyCFGNode"],
                    "id",
                    _global_ordinal(c.id, local_key),
                    prune(
                        {
                            "kind": node.kind,
                            "start_line": span.start[0] if span else None,
                            "end_line": span.end[0] if span else None,
                            "var": node.of,
                            "call_node": node.parent,
                            # Call-site detail (#120). The JSON emits one node per
                            # call site; the graph now does too, instead of a
                            # separate :PyCallSite under a third id scheme.
                            "method_name": node.method_name,
                            "receiver_expr": node.receiver_expr,
                            "receiver_type": node.receiver_type,
                            "return_type": node.return_type,
                            "is_constructor_call": node.is_constructor_call,
                            "arguments_json": _stringify_if(node.arguments),
                            "_module": file_key,
                        }
                    ),
                )
                b.edge("PY_HAS_CFG_NODE", owner, ref)
                if node.kind == "call" and node.callee:
                    b.edge_to_symbol(
                        "PY_RESOLVES_TO", ref,
                        _symbol_ref(node.callee, externals, sig_to_id),
                    )
            for e in c.cfg or []:
                # kind-discriminated: a conditional's true/false pair between one
                # endpoint pair must stay two relationships, not one MERGE.
                b.edge(
                    "PY_CFG_NEXT",
                    _cfg_ref(c.id, e.src),
                    _cfg_ref(c.id, e.dst),
                    {"kind": e.kind},
                    key=e.kind,
                )
            for e in c.cdg or []:
                b.edge("PY_CDG", _cfg_ref(c.id, e.src), _cfg_ref(c.id, e.dst))
            for e in c.ddg or []:
                # (var, prov)-discriminated: the DDG legitimately carries several
                # edges between one statement pair (one per variable, and the
                # ssa/points-to split) — a plain endpoint-pair MERGE collapses
                # them and silently drops dependences.
                b.edge(
                    "PY_DDG",
                    _cfg_ref(c.id, e.src),
                    _cfg_ref(c.id, e.dst),
                    prune({"var": e.var, "prov": list(e.prov) if e.prov else None}),
                    key=f"{e.var or ''}|{','.join(e.prov or [])}",
                )
            # L4 intraprocedural summaries (transitive actual_in → actual_out
            # pass-throughs); LOCAL ids resolved to global PyCFGNode refs.
            for e in c.summary or []:
                b.edge("PY_SUMMARY", _cfg_ref(c.id, e.src), _cfg_ref(c.id, e.dst))

    # L4 interprocedural parameter passing, emitted once at the app scope. The
    # endpoints are ALREADY global ordinals (emit_l4 resolved them through the
    # endpoint functions' identity maps), so they land on the very PyCFGNode ids
    # projected above — a formal_in global id equals _global_ordinal(callee.id,
    # "@formal_in:0"). No dangling references.
    for e in app.param_in or []:
        b.edge(
            "PY_PARAM_IN",
            NodeRef("PyCFGNode", "id", e.src),
            NodeRef("PyCFGNode", "id", e.dst),
        )
    for e in app.param_out or []:
        b.edge(
            "PY_PARAM_OUT",
            NodeRef("PyCFGNode", "id", e.src),
            NodeRef("PyCFGNode", "id", e.dst),
        )


def _sym(can_id: str) -> NodeRef:
    return NodeRef("PySymbol", "id", can_id)


def _symbol_ref(signature: str, externals: dict, sig_to_id: dict) -> NodeRef:
    """Resolve a call/inheritance target to the NodeRef under which it was (or
    will be) emitted: a declared symbol by its can:// id, otherwise a
    signature-keyed :PySymbol (external ghost)."""
    can_id = sig_to_id.get(signature)
    if can_id is not None:
        return NodeRef("PySymbol", "id", can_id)
    return NodeRef("PySymbol", "signature", signature)


def _call_endpoint(
    b: RowBuilder, signature: str, externals: dict, sig_to_id: dict
) -> NodeRef:
    """A call-graph endpoint: a declared callable already emitted (keyed by its
    canonical ``can://`` id, resolved through ``sig_to_id``), or an external symbol
    (imported library / builtin member) materialized as a :PyExternal ghost.

    Classification is authoritative -- it comes from ``app.external_symbols``
    (keyed by ``can://…/@external/…`` id), not a "present in the graph" heuristic --
    so an imported module name (which exists only as a :PyPackage) can never shadow
    the call target. A declared endpoint resolves to its ``can://`` id (either
    already re-identified on the edge, or resolved through ``sig_to_id``); anything
    neither declared nor listed falls back to an id-keyed :PyExternal ghost rather
    than raising."""
    ext = externals.get(signature)
    if ext is None:
        can_id = sig_to_id.get(signature)
        if can_id is not None:
            ext = externals.get(can_id)
            if ext is None:
                return _sym(can_id)
        elif signature.startswith("can://") and "/@external/" not in signature:
            # An already re-identified declared endpoint (post reidentify_call_graph).
            return _sym(signature)
    if ext is not None:
        return b.node(
            ["PySymbol", "PyExternal"],
            "id",
            ext.id or signature,
            prune({"name": ext.name, "module": ext.module}),
        )
    name = signature.rsplit(".", 1)[-1] if "." in signature else signature
    return b.node(
        ["PySymbol", "PyExternal"],
        "id",
        signature,
        prune({"name": name}),
    )


# ----------------------------------------------------------------------------------------------
# Module body
# ----------------------------------------------------------------------------------------------


def _project_module_body(
    b: RowBuilder, file_key: str, mod_ref: NodeRef, mod: PyModule,
    externals: dict, sig_to_id: dict, module_id_by_key: dict,
) -> None:
    for fn in (mod.functions or {}).values():
        _project_callable(b, file_key, mod_ref, "PY_DECLARES", fn, externals, sig_to_id,
                          mod.source)
    for cl in (mod.types or {}).values():
        _project_class(b, file_key, mod_ref, "PY_DECLARES", cl, externals, sig_to_id,
                       mod.source)
    for v in mod.variables or []:
        _project_variable(b, file_key, mod_ref, file_key, v)
    _project_imports(b, mod_ref, mod, module_id_by_key)


def _project_imports(b: RowBuilder, mod_ref: NodeRef, mod: PyModule,
                     module_id_by_key: dict) -> None:
    # At most one PY_IMPORTS edge per (module, target) pair -- mirrors PY_CALLS,
    # which pre-aggregates for the same reason: both writers MERGE edges on
    # (type, from, to) and SET their props, so a second row for the same pair
    # would silently overwrite the first in Neo4j instead of adding an edge.
    # Buckets key on the edge's target identity (the resolved module, or the
    # spelling itself for unresolved/external imports), so the SAME target
    # imported under different spellings (``from pkg import util``,
    # ``from . import util as u``, ``from .util import helper``) collapses
    # onto one edge; the raw spellings ride along as a ``spellings`` array.
    # Resolved internal imports point at the real :PyModule; externals keep
    # the shared :PyPackage. Unresolved *relative* spellings (".", ".foo")
    # name no package -- they are dropped from the graph (the spelling
    # survives in analysis.json), instead of minting bogus
    # :PyPackage{name: "."} nodes.
    agg: dict = {}
    for im in mod.imports or []:
        if not im.module:
            continue
        if im.resolved_module is None and im.module.startswith("."):
            continue
        key = im.resolved_module or im.module
        a = agg.setdefault(
            key, {"spellings": set(), "names": set(), "aliases": set(), "resolved": im.resolved_module}
        )
        a["spellings"].add(im.module)
        if im.name:
            a["names"].add(im.name)
        if im.alias:
            a["aliases"].add(im.alias)
    for key, a in agg.items():
        resolved_id = module_id_by_key.get(a["resolved"]) if a["resolved"] is not None else None
        if resolved_id is not None:
            target = NodeRef("PyModule", "id", resolved_id)
        elif a["resolved"] is None:
            target = b.node(["PyPackage"], "name", key, {})
        else:
            # resolved to a file key that is not in this symbol table (partial
            # run) — keep the module target via its legacy file_key property.
            target = NodeRef("PyModule", "file_key", a["resolved"])
        b.edge(
            "PY_IMPORTS",
            mod_ref,
            target,
            prune(
                {
                    "spellings": sorted(a["spellings"]),
                    "imported_names": sorted(a["names"]) or None,
                    "aliases": sorted(a["aliases"]) or None,
                }
            ),
        )


# ----------------------------------------------------------------------------------------------
# Declarations
# ----------------------------------------------------------------------------------------------


def _project_class(
    b: RowBuilder, file_key: str, parent: NodeRef, parent_rel: str, cl: PyClass,
    externals: dict, sig_to_id: dict, source: str,
) -> None:
    ref = b.node(
        ["PySymbol", "PyClass"], "id", cl.id, _class_props(cl, file_key, source)
    )
    b.edge(parent_rel, parent, ref)

    for d in cl.decorators or []:
        _project_decorator(b, ref, d)

    for base in cl.base_classes or []:
        if base:
            b.edge_to_symbol("PY_EXTENDS", ref, _symbol_ref(base, externals, sig_to_id))

    for m in (cl.callables or {}).values():
        _project_callable(b, file_key, ref, "PY_HAS_METHOD", m, externals, sig_to_id,
                          source)
    for a in (cl.attributes or {}).values():
        _project_attribute(b, file_key, ref, cl.signature, a)
    for ic in (cl.types or {}).values():
        _project_class(b, file_key, ref, "PY_DECLARES", ic, externals, sig_to_id, source)


def _project_callable(
    b: RowBuilder, file_key: str, owner: NodeRef, owner_rel: str, c: PyCallable,
    externals: dict, sig_to_id: dict, source: str,
) -> None:
    ref = b.node(
        ["PySymbol", "PyCallable"],
        "id",
        c.id,
        _callable_props(c, file_key, source),
    )
    b.edge(owner_rel, owner, ref)

    for d in c.decorators or []:
        _project_decorator(b, ref, d)

    for v in c.local_variables or []:
        _project_variable(b, file_key, ref, c.signature, v)
    for ic in (c.callables or {}).values():
        _project_callable(b, file_key, ref, "PY_DECLARES", ic, externals, sig_to_id,
                          source)
    for cl in (c.types or {}).values():
        _project_class(b, file_key, ref, "PY_DECLARES", cl, externals, sig_to_id, source)


def _project_attribute(
    b: RowBuilder, file_key: str, owner: NodeRef, owner_sig: str, a: PyClassAttribute
) -> None:
    attr_id = f"{owner_sig}.{a.name}"
    ref = b.node(["PyAttribute"], "id", attr_id, _attribute_props(a, attr_id, file_key))
    b.edge("PY_HAS_ATTRIBUTE", owner, ref)


def _project_variable(
    b: RowBuilder,
    file_key: str,
    owner: NodeRef,
    owner_id: str,
    v: PyVariableDeclaration,
) -> None:
    var_id = f"{owner_id}#{v.name}@{v.start_line}"
    ref = b.node(["PyVariable"], "id", var_id, _variable_props(v, var_id, file_key))
    b.edge("PY_DECLARES_VAR", owner, ref)


def _project_decorator(b: RowBuilder, on: NodeRef, decorator: PyDecorator) -> None:
    """Project one decorator application (#128).

    The merge key is the resolved ``qualified_name`` when Jedi supplies one, so
    ``@lru_cache`` and ``@lru_cache(maxsize=128)`` land on one node instead of two,
    and two spellings of one decorator stop being separate nodes. Unresolved
    decorators fall back to the written spelling. Per-application facts (the
    arguments) ride on the relationship, not the shared node -- ``:PyDecorator``
    has no ``_module`` and is never pruned, so anything application-specific on it
    would accumulate across every project in the database.
    """
    key = decorator.qualified_name or decorator.name
    dec = b.node(
        ["PyDecorator"],
        "name",
        key,
        {"name": key, "qualified_name": decorator.qualified_name or ""},
    )
    b.edge(
        "PY_DECORATED_BY",
        on,
        dec,
        {
            "expression": decorator.expression or "",
            "positional_arguments": list(decorator.positional_arguments or []),
            "keyword_arguments_json": json.dumps(
                dict(decorator.keyword_arguments or {}), sort_keys=True
            ),
        },
    )


# ----------------------------------------------------------------------------------------------
# Property flattening
# ----------------------------------------------------------------------------------------------


def _module_props(mod: PyModule, file_key: str) -> Props:
    return prune(
        {
            "id": mod.id,
            "file_key": file_key,
            "module_name": mod.module_name,
            "content_hash": mod.content_hash,
            "last_modified": mod.last_modified,
            "file_size": mod.file_size,
            "_module": file_key,
        }
    )


def _span_code(source: str, span) -> str | None:
    """A declaration's text: the owning module's ``source`` sliced by the node's
    utf-8 byte span. Schema v2 stores source once per module, so the graph's
    ``code`` property (declared on :PyClass/:PyCallable and indexed by
    ``py_code_fts``) is derived here at projection time (#104)."""
    if span is None or not source:
        return None
    lo, hi = span.bytes
    return source.encode("utf-8")[lo:hi].decode("utf-8")


def _class_props(cl: PyClass, file_key: str, source: str) -> Props:
    return prune(
        {
            "id": cl.id,
            "signature": cl.signature,
            "name": cl.name,
            "code": _span_code(source, cl.span),
            "base_classes": list(cl.base_classes or []),
            "decorators": [d.qualified_name or d.name for d in (cl.decorators or [])],
            "docstring": _docstring_of(cl.comments),
            "start_line": cl.start_line,
            "end_line": cl.end_line,
            "_module": file_key,
            "is_entrypoint": bool(cl.entrypoints),
            "entrypoint_frameworks": sorted({e.framework for e in (cl.entrypoints or [])}),
        }
    )


def _callable_props(c: PyCallable, file_key: str, source: str) -> Props:
    return prune(
        {
            "id": c.id,
            "signature": c.signature,
            "name": c.name,
            "path": c.path,
            "return_type": c.return_type,
            "cyclomatic_complexity": c.cyclomatic_complexity,
            "code": _span_code(source, c.span),
            "code_start_line": c.code_start_line,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "docstring": _docstring_of(c.comments),
            "decorators": [d.qualified_name or d.name for d in (c.decorators or [])],
            "parameters_json": _stringify_if(c.parameters),
            "accessed_symbols_json": _stringify_if(c.accessed_symbols),
            "_module": file_key,
            "is_entrypoint": bool(c.entrypoints),
            "entrypoint_frameworks": sorted({e.framework for e in (c.entrypoints or [])}),
        }
    )


def _attribute_props(a: PyClassAttribute, attr_id: str, file_key: str) -> Props:
    return prune(
        {
            "id": attr_id,
            "name": a.name,
            "type": a.type,
            "initializer": a.initializer,
            "docstring": _docstring_of(a.comments),
            "start_line": a.start_line,
            "end_line": a.end_line,
            "_module": file_key,
        }
    )


def _variable_props(v: PyVariableDeclaration, var_id: str, file_key: str) -> Props:
    return prune(
        {
            "id": var_id,
            "name": v.name,
            "type": v.type,
            "initializer": v.initializer,
            "scope": v.scope,
            "start_line": v.start_line,
            "end_line": v.end_line,
            "_module": file_key,
        }
    )




def _call_edge_props(weight: int, prov: List[str]) -> Props:
    return prune({"weight": weight, "prov": list(prov)})


def _docstring_of(comments: Optional[List[PyComment]]) -> Optional[str]:
    docs = [c.content for c in (comments or []) if c.is_docstring]
    return "\n".join(docs) if docs else None


def _stringify_if(value: Any) -> Optional[str]:
    """JSON-encode a list/dict of pydantic models, or None when empty."""
    if value is None:
        return None
    if isinstance(value, (list, dict)) and len(value) == 0:
        return None
    return json.dumps(value, default=_jsonable, sort_keys=True)


def _jsonable(o: Any) -> Any:
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if hasattr(o, "dict"):
        return o.dict()
    if isinstance(o, Path):
        return str(o)
    return str(o)
