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
from codeanalyzer.schema.py_schema import PyCallsite


def project(app: PyApplication, app_name: str, sig_to_id: dict) -> GraphRows:
    b = RowBuilder()

    app_ref = b.node(
        ["PyApplication"], "name", app_name, {"schema_version": SCHEMA_VERSION}
    )

    # Endpoints listed in app.external_symbols become :PyExternal ghost nodes; the
    # rest are declared :PySymbol nodes emitted here (keyed by their can:// id,
    # resolved through ``sig_to_id``). Both the module-body projection (for
    # PY_EXTENDS / PY_RESOLVES_TO) and the PY_CALLS twin below share this split.
    externals = app.external_symbols or {}

    for file_key, mod in app.symbol_table.items():
        mod_ref = b.node(["PyModule"], "id", mod.id, _module_props(mod, file_key))
        b.edge("PY_HAS_MODULE", app_ref, mod_ref)
        _project_module_body(b, file_key, mod_ref, mod, externals, sig_to_id)

    # The aggregated :PY_CALLS twin.
    for e in app.call_graph:
        src = _call_endpoint(b, e.source, externals, sig_to_id)
        tgt = _call_endpoint(b, e.target, externals, sig_to_id)
        b.edge(
            "PY_CALLS", src, tgt, _call_edge_props(e.weight, list(e.provenance or []))
        )

    # Level-3 CPG overlay: each callable's v2 body/cfg/cdg/ddg. Idempotent under
    # MERGE — a no-op when no callable carries L3 fields (levels 1/2).
    _project_program_graphs(b, app)

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


def _project_program_graphs(b: RowBuilder, app: PyApplication) -> None:
    """Level-3 CPG overlay, projected off each callable's v2 ``body``/``cfg``/
    ``cdg``/``ddg`` (populated by ``emit_l3_body`` at ``-a 3``; empty otherwise).

    Node label ``PyCFGNode`` (merge key ``id`` = the GLOBAL ordinal
    ``<callable can:// id>@<local body key>`` — identical to the JSON body key
    prefixed with the callable id, so the two projections agree). Edges:
    ``PY_HAS_CFG_NODE`` from the owning callable, ``PY_CFG_NEXT`` (prop ``kind``)
    over the CFG, ``PY_CDG`` over control dependence, and ``PY_DDG`` (props
    ``var``/``prov``) over data dependence. The vocabulary is cross-language in
    shape but PY_-namespaced like every other row family, so a multi-language
    database never mingles analyzers' dependence edges. Body-node ``var``/
    ``call_node`` props are an L4 parameter-node concern and are absent here."""
    from codeanalyzer.semantic_analysis.call_graph import _walk_module_callables

    for file_key, mod in app.symbol_table.items():
        for c in _walk_module_callables(mod):
            if not c.id:
                continue  # unstamped callable — assign_ids must run first
            owner = _sym(c.id)  # the :PyCallable node, keyed by its can:// id
            for local_key, node in (c.body or {}).items():
                span = node.span
                ref = b.node(
                    ["PyCFGNode"],
                    "id",
                    _global_ordinal(c.id, local_key),
                    prune(
                        {
                            "kind": node.kind,
                            "start_line": span.start[0] if span else None,
                            "end_line": span.end[0] if span else None,
                            "_module": file_key,
                        }
                    ),
                )
                b.edge("PY_HAS_CFG_NODE", owner, ref)
            for e in c.cfg or []:
                b.edge(
                    "PY_CFG_NEXT",
                    _cfg_ref(c.id, e.src),
                    _cfg_ref(c.id, e.dst),
                    {"kind": e.kind},
                )
            for e in c.cdg or []:
                b.edge("PY_CDG", _cfg_ref(c.id, e.src), _cfg_ref(c.id, e.dst))
            for e in c.ddg or []:
                b.edge(
                    "PY_DDG",
                    _cfg_ref(c.id, e.src),
                    _cfg_ref(c.id, e.dst),
                    prune({"var": e.var, "prov": list(e.prov) if e.prov else None}),
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

    Classification is authoritative -- it comes from ``app.external_symbols``, not a
    "present in the graph" heuristic -- so an imported module name (which exists only
    as a :PyPackage) can never shadow the call target. A declared endpoint resolves to
    its ``can://`` id; anything neither declared nor listed falls back to a
    signature-keyed :PyExternal ghost rather than raising."""
    ext = externals.get(signature)
    if ext is None:
        can_id = sig_to_id.get(signature)
        if can_id is not None:
            return _sym(can_id)
    name = (
        ext.name
        if ext is not None
        else (signature.rsplit(".", 1)[-1] if "." in signature else signature)
    )
    module = ext.module if ext is not None else None
    return b.node(
        ["PySymbol", "PyExternal"],
        "signature",
        signature,
        prune({"name": name, "module": module}),
    )


# ----------------------------------------------------------------------------------------------
# Module body
# ----------------------------------------------------------------------------------------------


def _project_module_body(
    b: RowBuilder, file_key: str, mod_ref: NodeRef, mod: PyModule,
    externals: dict, sig_to_id: dict,
) -> None:
    for fn in (mod.functions or {}).values():
        _project_callable(b, file_key, mod_ref, "PY_DECLARES", fn, externals, sig_to_id)
    for cl in (mod.classes or {}).values():
        _project_class(b, file_key, mod_ref, "PY_DECLARES", cl, externals, sig_to_id)
    for v in mod.variables or []:
        _project_variable(b, file_key, mod_ref, file_key, v)
    _project_imports(b, mod_ref, mod)


def _project_imports(b: RowBuilder, mod_ref: NodeRef, mod: PyModule) -> None:
    # Per-target-module aggregation: collapse all bindings for a given imported
    # module into one PY_IMPORTS edge to a shared :PyPackage node.
    agg: dict = {}
    for im in mod.imports or []:
        if not im.module:
            continue  # relative `from . import x` — no resolvable package
        a = agg.setdefault(im.module, {"names": set(), "aliases": set()})
        if im.name:
            a["names"].add(im.name)
        if im.alias:
            a["aliases"].add(im.alias)
    for module_name, a in agg.items():
        pkg = b.node(["PyPackage"], "name", module_name, {})
        b.edge(
            "PY_IMPORTS",
            mod_ref,
            pkg,
            prune(
                {
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
    externals: dict, sig_to_id: dict,
) -> None:
    ref = b.node(
        ["PySymbol", "PyClass"], "id", cl.id, _class_props(cl, file_key)
    )
    b.edge(parent_rel, parent, ref)

    for base in cl.base_classes or []:
        if base:
            b.edge_to_symbol("PY_EXTENDS", ref, _symbol_ref(base, externals, sig_to_id))

    for m in (cl.methods or {}).values():
        _project_callable(b, file_key, ref, "PY_HAS_METHOD", m, externals, sig_to_id)
    for a in (cl.attributes or {}).values():
        _project_attribute(b, file_key, ref, cl.signature, a)
    for ic in (cl.inner_classes or {}).values():
        _project_class(b, file_key, ref, "PY_DECLARES", ic, externals, sig_to_id)


def _project_callable(
    b: RowBuilder, file_key: str, owner: NodeRef, owner_rel: str, c: PyCallable,
    externals: dict, sig_to_id: dict,
) -> None:
    ref = b.node(
        ["PySymbol", "PyCallable"],
        "id",
        c.id,
        _callable_props(c, file_key),
    )
    b.edge(owner_rel, owner, ref)

    for d in c.decorators or []:
        _project_decorator(b, ref, d)

    for s in c.call_sites or []:
        # Key off the relative file (a call site lives in its callable's file) so ids stay portable.
        cs_id = (
            f"{file_key}#{s.start_line}:{s.start_column}-{s.end_line}:{s.end_column}"
        )
        cs = b.node(["PyCallSite"], "id", cs_id, _call_site_props(s, file_key))
        b.edge("PY_HAS_CALLSITE", ref, cs)
        if s.callee_signature:
            b.edge_to_symbol(
                "PY_RESOLVES_TO", cs,
                _symbol_ref(s.callee_signature, externals, sig_to_id),
            )

    for v in c.local_variables or []:
        _project_variable(b, file_key, ref, c.signature, v)
    for ic in (c.inner_callables or {}).values():
        _project_callable(b, file_key, ref, "PY_DECLARES", ic, externals, sig_to_id)
    for cl in (c.inner_classes or {}).values():
        _project_class(b, file_key, ref, "PY_DECLARES", cl, externals, sig_to_id)


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


def _project_decorator(b: RowBuilder, on: NodeRef, decorator: str) -> None:
    dec = b.node(["PyDecorator"], "name", decorator, {"name": decorator})
    b.edge("PY_DECORATED_BY", on, dec)


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


def _class_props(cl: PyClass, file_key: str) -> Props:
    return prune(
        {
            "id": cl.id,
            "signature": cl.signature,
            "name": cl.name,
            "code": getattr(cl, "code", None),
            "base_classes": list(cl.base_classes or []),
            "docstring": _docstring_of(cl.comments),
            "start_line": cl.start_line,
            "end_line": cl.end_line,
            "_module": file_key,
        }
    )


def _callable_props(c: PyCallable, file_key: str) -> Props:
    return prune(
        {
            "id": c.id,
            "signature": c.signature,
            "name": c.name,
            "path": c.path,
            "return_type": c.return_type,
            "cyclomatic_complexity": c.cyclomatic_complexity,
            "code": getattr(c, "code", None),
            "code_start_line": c.code_start_line,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "docstring": _docstring_of(c.comments),
            "decorators": list(c.decorators or []),
            "parameters_json": _stringify_if(c.parameters),
            "accessed_symbols_json": _stringify_if(c.accessed_symbols),
            "_module": file_key,
        }
    )


def _attribute_props(a: PyClassAttribute, attr_id: str, file_key: str) -> Props:
    return prune(
        {
            "id": attr_id,
            "name": a.name,
            "type": a.type,
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


def _call_site_props(s: PyCallsite, file_key: str) -> Props:
    cs_id = f"{file_key}#{s.start_line}:{s.start_column}-{s.end_line}:{s.end_column}"
    return prune(
        {
            "id": cs_id,
            "method_name": s.method_name,
            "receiver_expr": s.receiver_expr,
            "receiver_type": s.receiver_type,
            "argument_types": list(s.argument_types or []),
            "return_type": s.return_type,
            "callee_signature": s.callee_signature,
            "is_constructor_call": s.is_constructor_call,
            "start_line": s.start_line,
            "start_column": s.start_column,
            "end_line": s.end_line,
            "end_column": s.end_column,
            "_module": file_key,
        }
    )


def _call_edge_props(weight: int, provenance: List[str]) -> Props:
    return prune({"weight": weight, "provenance": list(provenance)})


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
