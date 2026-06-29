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


def project(app: PyApplication, app_name: str) -> GraphRows:
    b = RowBuilder()

    app_ref = b.node(
        ["PyApplication"], "name", app_name, {"schema_version": SCHEMA_VERSION}
    )

    for file_key, mod in app.symbol_table.items():
        mod_ref = b.node(
            ["PyModule"], "file_key", file_key, _module_props(mod, file_key)
        )
        b.edge("PY_HAS_MODULE", app_ref, mod_ref)
        _project_module_body(b, file_key, mod_ref, mod)

    # The aggregated :PY_CALLS twin. Endpoints listed in app.external_symbols become
    # :PyExternal ghost nodes; the rest are declared :PySymbol nodes already emitted.
    externals = app.external_symbols or {}
    for e in app.call_graph:
        src = _call_endpoint(b, e.source, externals)
        tgt = _call_endpoint(b, e.target, externals)
        b.edge(
            "PY_CALLS", src, tgt, _call_edge_props(e.weight, list(e.provenance or []))
        )

    return b.finish()


def _sym(signature: str) -> NodeRef:
    return NodeRef("PySymbol", "signature", signature)


def _call_endpoint(b: RowBuilder, signature: str, externals: dict) -> NodeRef:
    """A call-graph endpoint: a declared callable already emitted, or an external
    symbol (imported library / builtin member) materialized as a :PyExternal ghost.

    Classification is authoritative -- it comes from ``app.external_symbols``, not a
    "present in the graph" heuristic -- so an imported module name (which exists only
    as a :PyPackage) can never shadow the call target. A small fallback still
    materializes an external for any endpoint that is neither declared nor listed."""
    ext = externals.get(signature)
    if ext is None and b.has_key("PySymbol", signature):
        return _sym(signature)
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
    b: RowBuilder, file_key: str, mod_ref: NodeRef, mod: PyModule
) -> None:
    for fn in (mod.functions or {}).values():
        _project_callable(b, file_key, mod_ref, "PY_DECLARES", fn)
    for cl in (mod.classes or {}).values():
        _project_class(b, file_key, mod_ref, "PY_DECLARES", cl)
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
    b: RowBuilder, file_key: str, parent: NodeRef, parent_rel: str, cl: PyClass
) -> None:
    ref = b.node(
        ["PySymbol", "PyClass"], "signature", cl.signature, _class_props(cl, file_key)
    )
    b.edge(parent_rel, parent, ref)

    for base in cl.base_classes or []:
        b.edge_to_symbol("PY_EXTENDS", ref, base)

    for m in (cl.methods or {}).values():
        _project_callable(b, file_key, ref, "PY_HAS_METHOD", m)
    for a in (cl.attributes or {}).values():
        _project_attribute(b, file_key, ref, cl.signature, a)
    for ic in (cl.inner_classes or {}).values():
        _project_class(b, file_key, ref, "PY_DECLARES", ic)


def _project_callable(
    b: RowBuilder, file_key: str, owner: NodeRef, owner_rel: str, c: PyCallable
) -> None:
    ref = b.node(
        ["PySymbol", "PyCallable"],
        "signature",
        c.signature,
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
            b.edge_to_symbol("PY_RESOLVES_TO", cs, s.callee_signature)

    for v in c.local_variables or []:
        _project_variable(b, file_key, ref, c.signature, v)
    for ic in (c.inner_callables or {}).values():
        _project_callable(b, file_key, ref, "PY_DECLARES", ic)
    for cl in (c.inner_classes or {}).values():
        _project_class(b, file_key, ref, "PY_DECLARES", cl)


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
            "name": cl.name,
            "code": cl.code,
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
            "name": c.name,
            "path": c.path,
            "return_type": c.return_type,
            "cyclomatic_complexity": c.cyclomatic_complexity,
            "code": c.code,
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
