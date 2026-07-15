from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.py_schema import PyApplication, PyModule, PyCallable, PyCallsite

def test_l1_body_has_call_nodes_with_null_callee():
    cs = PyCallsite(method_name="g", start_line=2, start_column=4, end_line=2,
                    end_column=7, callee_signature="m.g")
    fn = PyCallable(name="f", path="m.py", signature="m.f", call_sites=[cs])
    mod = PyModule(file_path="m.py", module_name="m", functions={"f": fn})
    app = PyApplication(symbol_table={"m.py": mod})
    populate_l1_body(app)
    node = fn.body["2:4"]
    assert node.kind == "call"
    assert node.callee is None  # L1: unresolved; backfilled at L2
