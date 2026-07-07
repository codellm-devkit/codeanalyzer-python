from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.neo4j.project import project
from codeanalyzer.schema.py_schema import (
    PyApplication, PyModule, PyClass, PyCallable, PyCallsite,
)


def test_neo4j_callable_key_equals_json_id():
    fn = PyCallable(name="f", path="m.py", signature="m.f", parameters=[])
    mod = PyModule(file_path="m.py", module_name="m", source="def f():\n    pass\n",
                   functions={"f": fn})
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "myapp")
    rows = project(app, "myapp", sig_to_id)
    keys = {n.value for n in rows.nodes}
    assert fn.id in keys          # the callable node is keyed by its can:// id
    assert app.symbol_table["m.py"].id in keys


def test_py_resolves_to_edge_targets_declared_callee_by_can_id():
    callee = PyCallable(name="g", path="m.py", signature="m.g", parameters=[])
    cs = PyCallsite(method_name="g", start_line=2, start_column=4, end_line=2,
                    end_column=7, callee_signature="m.g")
    caller = PyCallable(name="f", path="m.py", signature="m.f", parameters=[],
                        call_sites=[cs])
    mod = PyModule(file_path="m.py", module_name="m", source="def f():\n    g()\n",
                   functions={"f": caller, "g": callee})
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "myapp")
    rows = project(app, "myapp", sig_to_id)
    resolves = [e for e in rows.edges if e.type == "PY_RESOLVES_TO"]
    # the callsite must resolve to g's can:// id — edge kept, not dropped
    assert any(e.to_ref.value == sig_to_id["m.g"] for e in resolves), \
        "PY_RESOLVES_TO must target the declared callee by can:// id"
