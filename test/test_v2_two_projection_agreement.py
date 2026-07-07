from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.neo4j.project import project, _global_ordinal
from codeanalyzer.schema.py_schema import (
    PyApplication, PyModule, PyClass, PyCallable, PyCallsite,
)
from codeanalyzer.dataflow.builder import build_function_pdgs, emit_l3_body
from codeanalyzer.dataflow.syntactic import SyntacticOracle
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder


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


# ----------------------------------------------------------------------------------------------
# Level-3 CPG overlay: PyCFGNode merge keys are the callable can:// id + local body key,
# proving the Neo4j projection and the JSON `body`/`cfg` land on the same node identity.
# ----------------------------------------------------------------------------------------------


def _build_l3_app(tmp_path):
    """A one-function project carried through the real L1 → L3 pipeline (mirrors
    test_v2_l3) so each callable has a populated ``body``/``cfg``/``cdg``/``ddg``."""
    f = tmp_path / "m.py"
    f.write_text("def f(a):\n    b = a\n    g(b)\n    return b\n", encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "app")
    populate_l1_body(app)
    infos, _func_asts = build_function_pdgs(
        app, k=3, oracle_factory=lambda c: SyntacticOracle()
    )
    emit_l3_body(app, infos, sig_to_id, graphs={"cfg", "dfg", "pdg"})
    return app, sig_to_id, mod


def test_cpg_overlay_pycfgnode_keys_equal_callable_id_plus_body_key(tmp_path):
    app, sig_to_id, mod = _build_l3_app(tmp_path)
    c = next(iter(mod.functions.values()))
    assert c.body, "precondition: L3 must populate the callable body"

    rows = project(app, "app", sig_to_id)

    # (a) two-projection agreement: the set of PyCFGNode merge values for this
    # callable == { <callable can:// id> @ <JSON local body key> }.
    expected = {_global_ordinal(c.id, k) for k in c.body}
    emitted = {
        n.value
        for n in rows.nodes
        if n.labels[0] == "PyCFGNode" and n.value.startswith(c.id + "@")
    }
    assert emitted == expected, f"PyCFGNode keys {emitted} != body ordinals {expected}"

    # (b) a PY_CFG_NEXT edge whose endpoints are two of those global ids.
    cfg_next = [e for e in rows.edges if e.type == "PY_CFG_NEXT"]
    assert any(
        e.from_ref.value in expected and e.to_ref.value in expected for e in cfg_next
    ), "expected a PY_CFG_NEXT edge over the callable's CFG-node ids"

    # (c) PY_HAS_CFG_NODE from the callable to its @entry CFG node.
    entry_id = _global_ordinal(c.id, "@entry")
    assert any(
        e.type == "PY_HAS_CFG_NODE"
        and e.from_ref.value == c.id
        and e.to_ref.value == entry_id
        for e in rows.edges
    ), "expected PY_HAS_CFG_NODE from the callable to its @entry CFG node"
