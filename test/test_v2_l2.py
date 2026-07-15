from codeanalyzer.schema.l2_callees import backfill_callees
from codeanalyzer.schema.call_graph_ids import reidentify_call_graph
from codeanalyzer.schema.py_schema import (
    PyApplication, PyModule, PyCallable, PyCallsite, BodyNode, PyCallEdge,
)

def _app_with_one_call(callee_sig):
    cs = PyCallsite(method_name="g", start_line=2, start_column=4, end_line=2,
                    end_column=7, callee_signature=callee_sig)
    fn = PyCallable(name="f", path="m.py", signature="m.f", call_sites=[cs],
                    body={"2:4": BodyNode(kind="call", callee=None)})
    mod = PyModule(file_path="m.py", module_name="m", functions={"f": fn})
    return PyApplication(symbol_table={"m.py": mod}), fn

def test_declared_callee_resolves_to_can_id():
    app, fn = _app_with_one_call("m.g")
    backfill_callees(app, {"m.g": "can://python/app/m.py/g()"})
    assert fn.body["2:4"].callee == "can://python/app/m.py/g()"

def test_external_callee_keeps_dotted_signature():
    app, fn = _app_with_one_call("requests.get")
    backfill_callees(app, {"m.g": "can://python/app/m.py/g()"})  # requests.get not declared
    assert fn.body["2:4"].callee == "requests.get"

def test_unresolved_callsite_leaves_callee_absent():
    app, fn = _app_with_one_call(None)
    backfill_callees(app, {})
    assert fn.body["2:4"].callee is None

def test_call_graph_endpoints_reidentified():
    edge = PyCallEdge(src="m.f", dst="m.g")
    app = PyApplication(symbol_table={}, call_graph=[edge])
    reidentify_call_graph(app, {"m.f": "can://python/app/m.py/f()",
                                "m.g": "can://python/app/m.py/g()"})
    assert app.call_graph[0].src == "can://python/app/m.py/f()"
    assert app.call_graph[0].dst == "can://python/app/m.py/g()"

def test_call_graph_external_target_unchanged():
    edge = PyCallEdge(src="m.f", dst="requests.get")
    app = PyApplication(symbol_table={}, call_graph=[edge])
    reidentify_call_graph(app, {"m.f": "can://python/app/m.py/f()"})
    assert app.call_graph[0].src == "can://python/app/m.py/f()"
    assert app.call_graph[0].dst == "requests.get"
