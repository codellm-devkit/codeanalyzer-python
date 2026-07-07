import textwrap
from pathlib import Path

from codeanalyzer.dataflow.builder import build_function_pdgs, emit_l3_body
from codeanalyzer.dataflow.identity import IdentityMap
from codeanalyzer.dataflow.syntactic import SyntacticOracle
from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.py_schema import PyApplication
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder

class _Node:
    def __init__(self, id, start_line, start_column, kind):
        self.id, self.start_line, self.start_column, self.kind = id, start_line, start_column, kind

class _CFG:
    def __init__(self, nodes, entry_id, exit_id):
        self._n = {n.id: n for n in nodes}; self.nodes = nodes
        self.entry_id, self.exit_id = entry_id, exit_id
    def node_by_id(self, i): return self._n[i]

class _PDG:
    def __init__(self, cfg): self.cfg = cfg

def test_local_and_global_ids_for_entry_exit_and_statements():
    nodes = [_Node(0, 1, 0, "entry"), _Node(1, 2, 4, "statement"), _Node(2, 3, 4, "exit")]
    pdg = _PDG(_CFG(nodes, entry_id=0, exit_id=2))
    im = IdentityMap.for_function("can://python/app/m.py/f()", pdg)
    # LOCAL ids: intra-callable keys (match l1_body's "line:col" format).
    assert im.local(0) == "@entry"
    assert im.local(1) == "2:4"
    assert im.local(2) == "@exit"
    # GLOBAL id: fully addressable form for Neo4j / cross-callable use.
    assert im.global_id(0) == "can://python/app/m.py/f()@entry"   # entry (single @, no double)
    assert im.global_id(1) == "can://python/app/m.py/f()@2:4"     # statement
    assert im.global_id(2) == "can://python/app/m.py/f()@exit"    # exit
    assert set(im.node_ids()) == {0, 1, 2}


def test_syntactic_oracle_only_identity_aliases():
    o = SyntacticOracle()
    assert o.may_alias("x.f", "x.f") is True
    assert o.may_alias("x.f", "y.f") is False
    assert o.may_alias("a", "b") is False


def test_emit_l3_populates_body_and_cfg(tmp_path: Path):
    f = tmp_path / "m.py"
    f.write_text("def f(a):\n    b = a\n    g(b)\n    return b\n", encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "app")
    # L1 materializes the `g(b)` call as a LOCAL "line:col" body node; simulate
    # the L2 callee refinement so we can prove L3 preserves it (no re-key).
    populate_l1_body(app)
    fn = next(iter(mod.functions.values()))
    call_key = "3:4"
    assert fn.body[call_key].kind == "call"
    fn.body[call_key].callee = "m.g"

    infos, _func_asts = build_function_pdgs(
        app, k=3, oracle_factory=lambda c: SyntacticOracle()
    )
    emit_l3_body(app, infos, sig_to_id, graphs={"cfg", "dfg", "pdg"})

    # body keys are LOCAL: "@entry"/"@exit" bookends + bare "line:col" stmts,
    # never the full "<callable-id>@..." form.
    assert "@entry" in fn.body
    assert "@exit" in fn.body
    assert any(k not in ("@entry", "@exit") and ":" in k for k in fn.body)
    assert not any(k.startswith("can://") for k in fn.body)
    # the L1 call node is PRESERVED under its local key (not duplicated, not
    # re-keyed): still kind=="call" with its L2-resolved callee.
    assert fn.body[call_key].kind == "call"
    assert fn.body[call_key].callee == "m.g"

    assert len(fn.cfg) > 0
    # every cfg endpoint resolves to a (local) body node id
    body_ids = set(fn.body)
    for e in fn.cfg:
        assert e.src in body_ids and e.dst in body_ids
    # ddg (if any) carries ssa provenance
    for e in fn.ddg:
        assert e.prov == ["ssa"]


def test_build_function_pdgs_returns_pdg_per_callable(tmp_path: Path):
    f = tmp_path / "m.py"
    f.write_text(textwrap.dedent("def f(a):\n    b = a\n    return b\n"), encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"m.py": mod})
    infos, func_asts = build_function_pdgs(
        app, k=3, oracle_factory=lambda c: SyntacticOracle()
    )
    sig = next(iter(mod.functions.values())).signature
    assert sig in infos
    assert infos[sig].pdg.cfg.entry_id is not None
