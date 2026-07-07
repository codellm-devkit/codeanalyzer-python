from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.neo4j.project import project
from codeanalyzer.schema.py_schema import PyApplication, PyModule, PyCallable


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
