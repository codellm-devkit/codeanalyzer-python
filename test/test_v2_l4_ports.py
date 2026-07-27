"""Regression tests for #115: the L4 SDG port layer must be *connected* to the
statement-level ddg, and call vertices must be anchored to their statement.

Before the fix, the interprocedural port lattice (``actual_in → formal_in``,
``formal_out → actual_out`` via param_in/param_out/summary) was an island: no
ddg edge touched any port, so an end-to-end ``flows_to(def, callee_formal)``
walk was inexpressible. The wiring existed in the IR (``fg.extra_edges``,
emitted by the old v1 ``program_graphs`` projection) but the v2 emission
dropped it. The restored edges carry ``prov=["reaching-defs"]`` — the same
label codeanalyzer-typescript ships for its port-routing ddg edges.
"""
from pathlib import Path

from codeanalyzer.dataflow.builder import (
    _base_types,
    build_function_pdgs,
    build_program_graphs,
    emit_ddg_pointsto_delta,
    emit_l3_body,
    emit_l4,
)
from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle
from codeanalyzer.dataflow.syntactic import SyntacticOracle
from codeanalyzer.schema import PyApplication
from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder

_SOURCE = """\
def build(flag):
    result = flag + 1
    return result


def main():
    x = 5
    y = build(x)
    z = y * 2
    return z
"""


def _build_l4_app(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text(_SOURCE, encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"app.py": mod})
    sig_to_id = assign_ids(app, "portfix")
    app.call_graph = [
        PyCallEdge(src="app.main", dst="app.build", prov=["jedi"], weight=1)
    ]
    populate_l1_body(app)
    syn_infos, _ = build_function_pdgs(
        app, k=3, oracle_factory=lambda c, fast: SyntacticOracle()
    )
    emit_l3_body(app, syn_infos, sig_to_id, graphs={"cfg", "dfg", "pdg"})
    ir = build_program_graphs(
        app, k=3,
        oracle_factory=lambda c, fast: make_alias_oracle(c, fast, _base_types(c)),
    )
    emit_l4(app, ir, sig_to_id)
    emit_ddg_pointsto_delta(app, syn_infos, ir, sig_to_id)
    mod = app.symbol_table["app.py"]
    return app, mod.functions["build"], mod.functions["main"]


def _edges(c, prov=None):
    out = set()
    for e in c.ddg or []:
        if prov is None or e.prov == prov:
            out.add((e.src, e.dst, e.var))
    return out


def test_def_stmt_flows_into_actual_in(tmp_path):
    """`x = 5` (7:4) must feed the argument port of the `build(x)` callsite."""
    _, _, main = _build_l4_app(tmp_path)
    rd = _edges(main, prov=["reaching-defs"])
    assert any(
        src == "7:4" and dst.endswith("/actual_in:0") for src, dst, _ in rd
    ), f"missing def→actual_in binding edge; reaching-defs edges: {sorted(rd)}"


def test_actual_out_flows_back_to_callsite(tmp_path):
    """The return-value port must flow back into the callsite statement."""
    _, _, main = _build_l4_app(tmp_path)
    rd = _edges(main, prov=["reaching-defs"])
    assert any(
        src.endswith("/actual_out") and dst == "8:4" for src, dst, _ in rd
    ), f"missing actual_out→use binding edge; reaching-defs edges: {sorted(rd)}"


def test_formal_in_flows_to_first_use(tmp_path):
    """Inside the callee, the parameter port must reach its first-use stmt."""
    _, build, _ = _build_l4_app(tmp_path)
    rd = _edges(build, prov=["reaching-defs"])
    assert any(
        src == "@formal_in:0" and dst == "2:4" for src, dst, _ in rd
    ), f"missing formal_in→use edge; reaching-defs edges: {sorted(rd)}"


def test_return_stmt_flows_into_formal_out(tmp_path):
    """`return result` (3:4) must feed the callee's formal_out port."""
    _, build, _ = _build_l4_app(tmp_path)
    rd = _edges(build, prov=["reaching-defs"])
    assert any(
        src == "3:4" and dst == "@formal_out" for src, dst, _ in rd
    ), f"missing return→formal_out edge; reaching-defs edges: {sorted(rd)}"


def test_port_edges_never_replace_or_retag_l3_edges(tmp_path):
    """The wiring is additive: every ssa edge survives, and no reaching-defs
    edge duplicates an (src, dst, var) triple that ssa already carries."""
    _, build, main = _build_l4_app(tmp_path)
    for c in (build, main):
        ssa = _edges(c, prov=["ssa"])
        rd = _edges(c, prov=["reaching-defs"])
        assert ssa, "L3 ssa edges must still be present"
        assert not (ssa & rd), "reaching-defs must not duplicate ssa triples"


def test_port_edge_endpoints_exist_in_body(tmp_path):
    _, build, main = _build_l4_app(tmp_path)
    for c in (build, main):
        for e in c.ddg or []:
            assert e.src in c.body, f"dangling ddg src {e.src}"
            assert e.dst in c.body, f"dangling ddg dst {e.dst}"


def test_emission_is_idempotent_under_reemit(tmp_path):
    """Re-running emit_l4 + the delta against the same live tree (cache-reuse
    shape) must not duplicate the reaching-defs edges."""
    f = tmp_path / "app.py"
    f.write_text(_SOURCE, encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"app.py": mod})
    sig_to_id = assign_ids(app, "portfix")
    app.call_graph = [
        PyCallEdge(src="app.main", dst="app.build", prov=["jedi"], weight=1)
    ]
    populate_l1_body(app)
    syn_infos, _ = build_function_pdgs(
        app, k=3, oracle_factory=lambda c, fast: SyntacticOracle()
    )
    emit_l3_body(app, syn_infos, sig_to_id, graphs={"cfg", "dfg", "pdg"})
    ir = build_program_graphs(
        app, k=3,
        oracle_factory=lambda c, fast: make_alias_oracle(c, fast, _base_types(c)),
    )
    emit_l4(app, ir, sig_to_id)
    emit_ddg_pointsto_delta(app, syn_infos, ir, sig_to_id)
    main = app.symbol_table["app.py"].functions["main"]
    first = sorted((e.src, e.dst, e.var, tuple(e.prov)) for e in main.ddg)
    emit_l4(app, ir, sig_to_id)
    emit_ddg_pointsto_delta(app, syn_infos, ir, sig_to_id)
    main = app.symbol_table["app.py"].functions["main"]
    second = sorted((e.src, e.dst, e.var, tuple(e.prov)) for e in main.ddg)
    assert first == second, "re-emission must be idempotent"


# ----------------------------------------------------------------------------------------------
# #115 part 2: call vertices anchored to their statement via `parent`.
# ----------------------------------------------------------------------------------------------


def test_nested_call_vertex_is_parented_to_its_statement(tmp_path):
    """`y = build(x)`: the call vertex (8:8) floats off the CFG spine; from L3
    it must carry `parent` = its enclosing statement's local (8:4)."""
    _, _, main = _build_l4_app(tmp_path)
    call_nodes = {k: n for k, n in main.body.items() if n.kind == "call"}
    assert call_nodes, "fixture must materialize a call vertex"
    for key, node in call_nodes.items():
        assert node.parent == "8:4", (
            f"call vertex {key} must be parented to its statement, "
            f"got parent={node.parent!r}"
        )


def test_bare_call_statement_needs_no_parent(tmp_path):
    """A bare call (`g(b)`) shares its key with the statement node — no
    self-parent is emitted."""
    f = tmp_path / "m.py"
    f.write_text(
        "def g(x):\n    return x\n\n\ndef f(a):\n    g(a)\n    return a\n",
        encoding="utf-8",
    )
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "barefix")
    populate_l1_body(app)
    syn_infos, _ = build_function_pdgs(
        app, k=3, oracle_factory=lambda c, fast: SyntacticOracle()
    )
    emit_l3_body(app, syn_infos, sig_to_id, graphs={"cfg", "dfg", "pdg"})
    fcallable = app.symbol_table["m.py"].functions["f"]
    call_nodes = {k: n for k, n in fcallable.body.items() if n.kind == "call"}
    assert call_nodes, "fixture must materialize the bare call vertex"
    for key, node in call_nodes.items():
        assert node.parent != key, "a call must never parent to itself"
