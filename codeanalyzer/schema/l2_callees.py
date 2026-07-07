"""L2 refinement: fill each L1 `call` body node's `callee` (null→id) from the
call site's resolved signature — the one sanctioned value change. A declared
target becomes its can:// id; an external/library target keeps its dotted
signature; an unresolved call site leaves `callee` absent."""
from __future__ import annotations
from codeanalyzer.schema.py_schema import PyApplication, PyClass, PyCallable


def _do_callable(c: PyCallable, sig_to_id: dict) -> None:
    for cs in c.call_sites or []:
        if cs.callee_signature is None:
            continue
        key = f"{cs.start_line}:{cs.start_column}"
        node = c.body.get(key)
        if node is None or node.kind != "call":
            continue
        node.callee = sig_to_id.get(cs.callee_signature, cs.callee_signature)
    for ic in (c.inner_callables or {}).values():
        _do_callable(ic, sig_to_id)
    for icl in (c.inner_classes or {}).values():
        _do_class(icl, sig_to_id)


def _do_class(cl: PyClass, sig_to_id: dict) -> None:
    for m in (cl.methods or {}).values():
        _do_callable(m, sig_to_id)
    for ic in (cl.inner_classes or {}).values():
        _do_class(ic, sig_to_id)


def backfill_callees(app: PyApplication, sig_to_id: dict) -> None:
    for mod in app.symbol_table.values():
        for fn in (mod.functions or {}).values():
            _do_callable(fn, sig_to_id)
        for cl in (mod.classes or {}).values():
            _do_class(cl, sig_to_id)
