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


def test_l1_body_nodes_and_parameters_carry_global_ordinal_ids():
    # #176: a body node's `id` is the global ordinal the Neo4j projection merges on;
    # a parameter's `id` names the L4 formal_in vertex that will carry it.
    from codeanalyzer.schema.assign_ids import assign_ids
    from codeanalyzer.schema.py_schema import PyCallableParameter
    cs = PyCallsite(method_name="g", start_line=2, start_column=4, end_line=2,
                    end_column=7, callee_signature="m.g")
    fn = PyCallable(name="f", path="m.py", signature="m.f", call_sites=[cs],
                    parameters=[PyCallableParameter(name="a"), PyCallableParameter(name="b")])
    mod = PyModule(file_path="m.py", module_name="m", functions={"f": fn})
    app = PyApplication(symbol_table={"m.py": mod})
    assign_ids(app, "myapp")
    populate_l1_body(app)
    assert fn.body["2:4"].id == fn.id + "@2:4"
    assert [p.id for p in fn.parameters] == [fn.id + "@formal_in:0", fn.id + "@formal_in:1"]
