"""Walk the symbol-table tree and stamp every node with its can:// id."""
from __future__ import annotations
from typing import Dict
from codeanalyzer.schema import ids
from codeanalyzer.schema.py_schema import PyApplication, PyModule, PyClass, PyCallable


def assign_ids(app: PyApplication, app_name: str) -> Dict[str, str]:
    """Sets `.id` on the app + every module/class/callable. Returns a
    `signature -> can://id` map for later stages (identity layer input)."""
    app.id = ids.application_id(app_name); app.kind = "application"
    sig_to_id: Dict[str, str] = {}

    def do_callable(parent_id: str, c: PyCallable) -> None:
        seg = ids.callable_sig_segment(c.name, [p.name for p in c.parameters])
        c.id = ids.child_id(parent_id, seg)
        sig_to_id[c.signature] = c.id
        for ic in (c.inner_callables or {}).values():
            do_callable(c.id, ic)
        for icl in (c.inner_classes or {}).values():
            do_class(c.id, icl)

    def do_class(parent_id: str, cl: PyClass) -> None:
        cl.id = ids.child_id(parent_id, cl.name); cl.kind = "class"
        sig_to_id[cl.signature] = cl.id
        for m in (cl.methods or {}).values():
            do_callable(cl.id, m)
        for ic in (cl.inner_classes or {}).values():
            do_class(cl.id, ic)

    for file_key, mod in app.symbol_table.items():
        mod.id = ids.module_id(app_name, file_key); mod.kind = "module"
        for fn in (mod.functions or {}).values():
            do_callable(mod.id, fn)
        for cl in (mod.classes or {}).values():
            do_class(mod.id, cl)
    return sig_to_id
