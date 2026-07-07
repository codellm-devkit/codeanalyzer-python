import json
import subprocess
import sys
from pathlib import Path

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


def test_cli_emits_v2_envelope(tmp_path: Path):
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", "1", "--no-venv"],
        capture_output=True, text=True, check=True,
    ).stdout
    payload = json.loads(out)
    assert payload["schema_version"] == "2.0.0"
    assert payload["language"] == "python"
    assert payload["max_level"] == 1
    assert payload["application"]["kind"] == "application"
    assert "program_graphs" not in payload  # dissolved into the tree


def test_l1_output_is_conformant(tmp_path: Path):
    from test.conftest_v2 import assert_conformant
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / "pkg").mkdir()
    (proj / "pkg" / "m.py").write_text("def f(a):\n    return a\n", encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", "1", "--no-venv"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert_conformant(json.loads(out), max_level=1)
