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


def test_l2_audit_gate_callee_name_equality(tmp_path):
    """Issue #87 acceptance (CLDK-001/002 v2 counterpart): for resolvable
    non-constructor calls, >=95% of resolved callee bindings must agree with
    the invoked attribute name, and no callsite may fall back to a class id
    when that class declares the exact method (the receiver-anchor defect
    bound `self.env['x'].search(...)` to the caller's own class)."""
    import json
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "single_functionalities" / "method_call_resolution"
    proj = tmp_path / "proj"
    shutil.copytree(fixture, proj)
    out = subprocess.run(
        [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", "2", "--no-venv"],
        capture_output=True, text=True, check=True,
    ).stdout
    app = json.loads(out)["application"]

    # collect declared types (name -> their method names) for fallback detection
    class_methods = {}
    def walk_type(t):
        class_methods[t["id"]] = set()
        for mname, m in (t.get("callables") or {}).items():
            class_methods[t["id"]].add(m["name"])
        for it in (t.get("types") or {}).values():
            walk_type(it)
    sites = []
    for mod in app["symbol_table"].values():
        for t in (mod.get("types") or {}).values():
            walk_type(t)
        def walk_callable(c):
            for cs in c.get("call_sites") or []:
                sites.append(cs)
            for ic in (c.get("callables") or {}).values():
                walk_callable(ic)
        for fn in (mod.get("functions") or {}).values():
            walk_callable(fn)
        for t in (mod.get("types") or {}).values():
            for m in (t.get("callables") or {}).values():
                walk_callable(m)

    resolved = [
        cs for cs in sites
        if cs.get("callee_signature") and not cs.get("is_constructor_call")
    ]
    assert resolved, "fixture must produce resolved non-constructor callsites"
    agree = sum(
        1 for cs in resolved
        if cs["callee_signature"].rsplit(".", 1)[-1] == cs["method_name"]
    )
    ratio = agree / len(resolved)
    assert ratio >= 0.95, (
        f"only {agree}/{len(resolved)} resolved callsites bind to the invoked "
        f"name: {[(cs['method_name'], cs['callee_signature']) for cs in resolved if cs['callee_signature'].rsplit('.', 1)[-1] != cs['method_name']]}"
    )
    # no class fallback when the exact method exists: a callee binding that
    # names a class which declares the invoked method is the defect's signature
    for cs in resolved:
        for cls_id, methods in class_methods.items():
            cls_sig_name = cls_id.rsplit("/", 1)[-1]
            if cs["callee_signature"].rsplit(".", 1)[-1] == cls_sig_name and cs["method_name"] in methods:
                raise AssertionError(
                    f"callsite {cs['method_name']!r} fell back to class {cls_sig_name!r} "
                    f"which declares the exact method"
                )


def test_l2_runs_are_byte_identical(tmp_path):
    """Issue #99: same flags, same input → byte-identical analysis.json.
    Exercises the deterministic PyCG shard root, sorted entry points, the
    canonical call_graph sort, the Jedi union tie-break, and the CLI's
    self-pinned PYTHONHASHSEED (each subprocess re-execs with seed 0)."""
    import filecmp
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "single_functionalities" / "decorators_and_hof"
    proj = tmp_path / "proj"
    shutil.copytree(fixture, proj)
    outs = []
    for i in (1, 2):
        out = tmp_path / f"out{i}"
        subprocess.run(
            [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", "2",
             "--no-venv", "-o", str(out), "-c", str(tmp_path / f"cache{i}")],
            capture_output=True, text=True, check=True,
        )
        outs.append(out / "analysis.json")
    assert filecmp.cmp(outs[0], outs[1], shallow=False), \
        "two identical -a 2 runs must emit byte-identical analysis.json"
