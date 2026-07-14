"""Stage-5 keystone conformance gates (issue #98).

Asserts key-for-key parity with the canonical schema-v2 keystone
(`codeanalyzer-typescript-v2/src/schema/v2/model.ts` is the conformant
reference): edge keys, endpoint identity, containment vocabulary, param
edges on an interprocedural chain, and the envelope.
"""
import json
import subprocess
import sys
from pathlib import Path


def _write_interproc_fixture(tmp_path: Path) -> Path:
    """entry() -> ResUsers.__init__ -> ResUsers.greet chain with args + returns,
    shaped like the paired fixture from issue #98."""
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "pkg" / "mod.py").write_text(
        "class ResUsers:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "    def greet(self, prefix):\n"
        "        return prefix + self.name\n",
        encoding="utf-8",
    )
    (proj / "entry.py").write_text(
        "from pkg.mod import ResUsers\n"
        "\n"
        "def entry():\n"
        "    u = ResUsers(\"bob\")\n"
        "    return u.greet(\"hi \")\n",
        encoding="utf-8",
    )
    return proj


def _run(proj: Path, level: int) -> dict:
    out = subprocess.run(
        [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", str(level), "--no-venv"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _tree_ids(app: dict) -> set:
    """Every id reachable in the containment tree (modules, types, callables)."""
    ids = {app["id"]}

    def walk_callable(c):
        ids.add(c["id"])
        for ic in (c.get("callables") or {}).values():
            walk_callable(ic)
        for cl in (c.get("types") or {}).values():
            walk_type(cl)

    def walk_type(cl):
        ids.add(cl["id"])
        for m in (cl.get("callables") or {}).values():
            walk_callable(m)
        for icl in (cl.get("types") or {}).values():
            walk_type(icl)

    for mod in app["symbol_table"].values():
        ids.add(mod["id"])
        for fn in (mod.get("functions") or {}).values():
            walk_callable(fn)
        for cl in (mod.get("types") or {}).values():
            walk_type(cl)
    return ids


# --- gap 5: envelope --------------------------------------------------------

def test_envelope_analyzer_identity(tmp_path: Path):
    proj = _write_interproc_fixture(tmp_path)
    payload = _run(proj, 1)
    analyzer = payload.get("analyzer")
    assert analyzer, "envelope must carry analyzer{name,version}"
    assert analyzer["name"] == "codeanalyzer-python"
    assert analyzer["version"]


def test_k_limit_only_at_dataflow_levels(tmp_path: Path):
    proj = _write_interproc_fixture(tmp_path)
    assert "k_limit" not in _run(proj, 1), "k_limit is an L3+ envelope key"
    assert "k_limit" not in _run(proj, 2), "k_limit is an L3+ envelope key"
    assert _run(proj, 3).get("k_limit") == 3
    assert _run(proj, 4).get("k_limit") == 3


# --- gap 1: edge keys -------------------------------------------------------

def test_call_edges_are_keystone_shaped(tmp_path: Path):
    proj = _write_interproc_fixture(tmp_path)
    payload = _run(proj, 2)
    edges = payload["application"]["call_graph"]
    assert edges, "fixture must produce call edges"
    for e in edges:
        assert set(e) == {"src", "dst", "prov", "weight"}, f"non-keystone edge keys: {sorted(e)}"
        assert isinstance(e["prov"], list) and e["prov"]


# --- gap 2: endpoint identity ----------------------------------------------

def test_call_edge_endpoints_join_the_id_space(tmp_path: Path):
    proj = _write_interproc_fixture(tmp_path)
    payload = _run(proj, 2)
    app = payload["application"]
    joinable = _tree_ids(app) | set(app.get("external_symbols") or {})
    for e in app["call_graph"]:
        assert e["src"] in joinable, f"dangling edge src {e['src']!r}"
        assert e["dst"] in joinable, f"dangling edge dst {e['dst']!r}"
    # first-party targets resolve into the tree, not to dotted signatures
    dsts = {e["dst"] for e in app["call_graph"]}
    assert any(d.endswith("__init__(self,name)") and d.startswith("can://") for d in dsts), (
        f"ResUsers.__init__ must resolve to its can:// id, got dsts: {sorted(dsts)}"
    )


def test_external_symbols_are_id_homed(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text(
        "import json\n\ndef f(x):\n    return json.dumps(x)\n", encoding="utf-8"
    )
    payload = _run(proj, 2)
    app = payload["application"]
    externals = app.get("external_symbols") or {}
    app_id = app["id"]
    for key, ext in externals.items():
        assert key == ext["id"], "external_symbols must be keyed by id"
        assert ext["id"].startswith(f"{app_id}/@external/"), ext["id"]
        assert ext["kind"] == "external"
        assert ext["name"]


# --- gap 3: containment vocabulary -------------------------------------------

def test_containment_vocabulary_is_keystone(tmp_path: Path):
    proj = _write_interproc_fixture(tmp_path)
    payload = _run(proj, 1)
    app = payload["application"]
    mod = app["symbol_table"]["pkg/mod.py"]
    assert "types" in mod and "classes" not in mod
    cls = next(iter(mod["types"].values()))
    assert cls["kind"] == "class"
    assert "callables" in cls and "methods" not in cls
    assert "inner_classes" not in cls
    meth = next(iter(cls["callables"].values()))
    assert "inner_classes" not in meth and "inner_callables" not in meth


# --- gap 4: interprocedural param edges --------------------------------------

def test_param_edges_nonempty_on_interproc_chain(tmp_path: Path):
    proj = _write_interproc_fixture(tmp_path)
    payload = _run(proj, 4)
    app = payload["application"]
    assert app.get("param_in"), "entry->__init__/greet chain must produce param_in edges"
    assert app.get("param_out"), "greet returns a value: param_out must be non-empty"


# --- self round-trip: the emitted artifact validates against the own model ----

def test_emitted_json_round_trips_through_the_analysis_model(tmp_path: Path):
    """exclude_none emission must stay re-validatable: a variable whose type
    Jedi cannot infer (dropped `type` key) broke Analysis.model_validate_json
    with 'Field required' at Odoo scale."""
    from codeanalyzer.schema.py_schema import Analysis
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text(
        "import not_a_real_package\n"
        "\n"
        "def f():\n"
        "    val = not_a_real_package.mystery()\n"
        "    return val\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", "1", "--no-venv"],
        capture_output=True, text=True, check=True,
    ).stdout
    a = Analysis.model_validate_json(out)
    assert a.schema_version == "2.0.0"
