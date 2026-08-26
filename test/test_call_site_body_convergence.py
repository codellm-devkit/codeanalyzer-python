"""Call-site facts live on the body{} call node, not in a parallel list (#120).

`l1_body.py` derived `body{}` call nodes from `call_sites[]`, so the analyzer emitted
the same fact twice under two unrelated id schemes. The list stays in memory — it is
the producer that L2 resolution and L3/L4 dataflow read — but it leaves the wire, and
the detail it carried moves onto the call node.
"""
import json

from codeanalyzer.schema import strip_internal_only, model_dump
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.py_schema import (
    PyApplication, PyCallable, PyCallArgument, PyCallsite, PyModule,
)

SRC = "def f():\n    return g(1, x=2)\n"


def _app() -> tuple[PyApplication, PyCallable]:
    fn = PyCallable(name="f", path="a.py", signature="a.f")
    fn.call_sites.append(
        PyCallsite(
            method_name="g",
            receiver_expr="obj",
            receiver_type="Obj",
            return_type="int",
            is_constructor_call=False,
            arguments=[PyCallArgument(ast_kind="Constant", inferred_type="int")],
            start_line=2, start_column=11, end_line=2, end_column=22,
        )
    )
    mod = PyModule(file_path="a.py", module_name="a", source=SRC, functions={"f": fn})
    return PyApplication(symbol_table={"a.py": mod}), fn


def test_call_detail_lands_on_the_body_node():
    app, fn = _app()
    populate_l1_body(app)
    (node,) = [n for n in fn.body.values() if n.kind == "call"]
    assert node.method_name == "g"
    assert node.receiver_expr == "obj" and node.receiver_type == "Obj"
    assert node.return_type == "int"
    assert node.is_constructor_call is False
    assert [a.ast_kind for a in node.arguments] == ["Constant"]


def test_call_sites_stays_in_memory_for_the_internal_passes():
    """L2 resolution and L3/L4 dataflow read this list; it must not be emptied."""
    app, fn = _app()
    populate_l1_body(app)
    assert len(fn.call_sites) == 1
    assert fn.call_sites[0].method_name == "g"


def test_call_sites_is_stripped_at_emit_but_kept_when_serialized():
    """Both halves of the mechanism.

    Stripping happens at emit time, NOT via a field-level Pydantic `exclude`,
    because the analysis cache round-trips through the same serializer. Excluding
    at the field would drop `call_sites` from the cache too, and the next
    warm-cache run would rebuild from a payload with no call sites at all —
    silently losing the producer that `l1_body`, `l2_callees`, the call graph and
    the dataflow builder all read.
    """
    app, fn = _app()
    populate_l1_body(app)
    dumped = model_dump(app, mode="json")

    # Serialized form keeps it — this is what the cache persists.
    assert "call_sites" in dumped["symbol_table"]["a.py"]["functions"]["f"]

    # Emitted form drops it — this is what analysis.json carries.
    emitted = strip_internal_only(dumped)
    callable_json = emitted["symbol_table"]["a.py"]["functions"]["f"]
    assert "call_sites" not in callable_json
    (node,) = [n for n in callable_json["body"].values() if n["kind"] == "call"]
    assert node["method_name"] == "g"


def test_accessed_symbols_and_local_variables_are_untouched():
    """Only call_sites is redundant; these have no body{} representation to converge into."""
    app, fn = _app()
    populate_l1_body(app)
    callable_json = strip_internal_only(model_dump(app, mode="json"))["symbol_table"]["a.py"]["functions"]["f"]
    assert "accessed_symbols" in callable_json
    assert "local_variables" in callable_json
