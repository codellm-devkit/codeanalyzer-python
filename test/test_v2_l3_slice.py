import ast
from pathlib import Path

from codeanalyzer.dataflow.builder import build_function_pdgs
from codeanalyzer.dataflow.identity import IdentityMap
from codeanalyzer.dataflow.pdg import intraprocedural_backward_slice
from codeanalyzer.dataflow.syntactic import SyntacticOracle
from codeanalyzer.schema.py_schema import PyApplication
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder


def test_backward_slice_equals_hand_computed_set(tmp_path: Path):
    # A tiny fixture with a KNOWN def-use chain: return c <- (c = b) <- (b = a).
    f = tmp_path / "m.py"
    f.write_text("def f(a):\n    b = a\n    c = b\n    return c\n", encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"m.py": mod})

    infos, _func_asts = build_function_pdgs(
        app, k=3, oracle_factory=lambda c: SyntacticOracle()
    )
    fn = next(iter(mod.functions.values()))
    callable_id = fn.signature
    pdg = infos[callable_id].pdg

    # Criterion node: the `return c` statement, located by its AST node in the
    # CFG (there is exactly one Return in the fixture).
    criterion = next(n.id for n in pdg.cfg.nodes if isinstance(n.ast_node, ast.Return))

    slice_ids = intraprocedural_backward_slice(pdg, criterion)

    im = IdentityMap.for_function(callable_id, pdg)
    local_ids = {im.local(i) for i in slice_ids}

    # Hand-computed slice over ordinal ids, exact:
    #   4:4  `return c`  — the criterion itself
    #   3:4  `c = b`     — data dependence (reads c defined here)
    #   2:4  `b = a`     — data dependence (reads b defined here)
    #   @entry           — control-region root of every unconditional statement
    #                      AND the def site of the parameter `a` that `b = a` reads
    # @exit carries no dependence into the criterion, so it is NOT in the slice.
    assert local_ids == {"@entry", "2:4", "3:4", "4:4"}
