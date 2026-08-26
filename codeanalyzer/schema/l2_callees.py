"""L2 refinement: fill each L1 `call` body node's `callee` (null→id) from the
call site's resolved signature — the one sanctioned value change. A declared
target becomes its can:// id; an external/library target keeps its dotted
signature; an unresolved call site leaves `callee` absent.

Two resolution sources feed the backfill: Jedi's `callee_signature` on the
call site itself, and the defuse linker's returned map (keyed by caller
signature + "line:col"). The linker's resolutions are deliberately NOT written
into `callee_signature` — the symbol table round-trips through the analysis
cache, and a persisted resolution would resurface on a warm run as a Jedi
edge, silently changing provenance."""
from __future__ import annotations
from codeanalyzer.schema.py_schema import PyApplication, PyClass, PyCallable


def _do_callable(c: PyCallable, sig_to_id: dict, resolutions: dict) -> None:
    for cs in c.call_sites or []:
        key = f"{cs.start_line}:{cs.start_column}"
        jedi_sig = cs.callee_signature
        if jedi_sig and jedi_sig.startswith("typing."):
            # A decorator-typed callable resolved to its annotation, not a
            # target; the linker's resolution (if any) is the real callee.
            jedi_sig = None
        sig = jedi_sig or resolutions.get((c.signature, key))
        if not sig:
            continue
        node = c.body.get(key)
        if node is None or node.kind != "call":
            continue
        node.callee = sig_to_id.get(sig, sig)
    for ic in (c.callables or {}).values():
        _do_callable(ic, sig_to_id, resolutions)
    for icl in (c.types or {}).values():
        _do_class(icl, sig_to_id, resolutions)


def _do_class(cl: PyClass, sig_to_id: dict, resolutions: dict) -> None:
    for m in (cl.callables or {}).values():
        _do_callable(m, sig_to_id, resolutions)
    for ic in (cl.types or {}).values():
        _do_class(ic, sig_to_id, resolutions)


def backfill_callees(
    app: PyApplication, sig_to_id: dict, resolutions: dict | None = None
) -> None:
    resolutions = resolutions or {}
    for mod in app.symbol_table.values():
        for fn in (mod.functions or {}).values():
            _do_callable(fn, sig_to_id, resolutions)
        for cl in (mod.types or {}).values():
            _do_class(cl, sig_to_id, resolutions)
