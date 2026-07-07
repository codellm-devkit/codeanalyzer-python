from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.schema.py_schema import PyApplication, PyModule, PyClass, PyCallable


def test_ids_assigned_down_the_tree():
    fn = PyCallable(name="hash", path="m.py", signature="m.Hasher.hash",
                    parameters=[])
    cl = PyClass(name="Hasher", signature="m.Hasher", methods={"hash": fn})
    mod = PyModule(file_path="pkg/m.py", module_name="m", classes={"m.Hasher": cl})
    app = PyApplication(symbol_table={"pkg/m.py": mod})
    assign_ids(app, "myapp")
    assert app.id == "can://python/myapp"
    assert mod.id == "can://python/myapp/pkg/m.py"
    assert cl.id == "can://python/myapp/pkg/m.py/Hasher"
    assert fn.id == "can://python/myapp/pkg/m.py/Hasher/hash()"
