"""L1 body population: materialize `call` nodes from existing call sites.
`callee` is left None here — the sanctioned null→id refinement happens at L2."""
from __future__ import annotations
from codeanalyzer.schema.py_schema import PyApplication, PyClass, PyCallable, BodyNode, Span, byte_offsets

def _do_callable(source: str, c: PyCallable) -> None:
    for cs in c.call_sites or []:
        key = f"{cs.start_line}:{cs.start_column}"
        span = Span(start=(cs.start_line, cs.start_column),
                    end=(cs.end_line, cs.end_column),
                    bytes=byte_offsets(source, cs.start_line, cs.start_column, cs.end_line, cs.end_column)) if source else None
        c.body[key] = BodyNode(kind="call", span=span, callee=None)
    for ic in (c.inner_callables or {}).values():
        _do_callable(source, ic)
    for icl in (c.inner_classes or {}).values():
        _do_class(source, icl)

def _do_class(source: str, cl: PyClass) -> None:
    for m in (cl.methods or {}).values():
        _do_callable(source, m)
    for ic in (cl.inner_classes or {}).values():
        _do_class(source, ic)

def populate_l1_body(app: PyApplication) -> None:
    for mod in app.symbol_table.values():
        for fn in (mod.functions or {}).values():
            _do_callable(mod.source, fn)
        for cl in (mod.classes or {}).values():
            _do_class(mod.source, cl)
