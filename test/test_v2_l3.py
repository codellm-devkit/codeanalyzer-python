import textwrap
from pathlib import Path

from codeanalyzer.dataflow.builder import build_function_pdgs
from codeanalyzer.dataflow.identity import IdentityMap
from codeanalyzer.dataflow.syntactic import SyntacticOracle
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

def test_ordinal_ids_for_entry_exit_and_statements():
    nodes = [_Node(0, 1, 0, "entry"), _Node(1, 2, 4, "statement"), _Node(2, 3, 4, "exit")]
    pdg = _PDG(_CFG(nodes, entry_id=0, exit_id=2))
    im = IdentityMap.for_function("can://python/app/m.py/f()", pdg)
    assert im.ordinal(0) == "can://python/app/m.py/f()@entry"
    assert im.ordinal(1) == "can://python/app/m.py/f()@2:4"
    assert im.ordinal(2) == "can://python/app/m.py/f()@exit"
    assert set(im.node_ids()) == {0, 1, 2}


def test_syntactic_oracle_only_identity_aliases():
    o = SyntacticOracle()
    assert o.may_alias("x.f", "x.f") is True
    assert o.may_alias("x.f", "y.f") is False
    assert o.may_alias("a", "b") is False


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
