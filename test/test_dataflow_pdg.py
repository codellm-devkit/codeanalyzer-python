"""Stage-4 gate: PDG assembly and the exact intraprocedural backward slice.

The highest-value test of the intraprocedural half: the backward slice of a
named variable at a named line equals a hand-computed node set — exactly.
It catches both missing control dependences and missing def-use edges.
"""

import ast
from pathlib import Path

from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.dataflow.pdg import build_pdg, intraprocedural_backward_slice

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "dataflow"


def _pdg_of(file_name: str, func_name: str):
    tree = ast.parse((FIXTURE / file_name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return build_pdg(node, enclosing_locals=set(), oracle=TypeBasedAliasOracle())
    raise AssertionError(f"{func_name} not found")


def _id_at_line(pdg, line: int) -> int:
    return next(n.id for n in pdg.cfg.nodes if n.start_line == line and n.kind != "entry")


def _lines(pdg, ids) -> set:
    return {
        pdg.cfg.node_by_id(i).start_line
        for i in ids
        if pdg.cfg.node_by_id(i).kind not in ("entry", "exit")
    }


def test_pdg_edges_use_only_cdg_and_ddg_types():
    for func in ("branchy", "looped", "early_exit", "handles"):
        pdg = _pdg_of("main.py", func)
        assert {e.type for e in pdg.edges} <= {"CDG", "DDG"}
        for e in pdg.edges:
            assert (e.var is not None) == (e.type == "DDG")


def test_early_exit_slice_excludes_the_other_arm():
    pdg = _pdg_of("main.py", "early_exit")
    criterion = _id_at_line(pdg, 32)  # return y
    slice_ids = intraprocedural_backward_slice(pdg, criterion)
    # Hand-computed: ENTRY, the branch header (29), y = n * 2 (31), and the
    # criterion itself. `return -1` (30) is control-dependent on the same
    # branch but contributes nothing to y — it must NOT appear.
    assert _lines(pdg, slice_ids) == {29, 31, 32}
    assert pdg.cfg.entry_id in slice_ids
    assert _id_at_line(pdg, 30) not in slice_ids


def test_branchy_slice_includes_both_arms_and_the_branch():
    pdg = _pdg_of("main.py", "branchy")
    criterion = _id_at_line(pdg, 16)  # return x
    slice_ids = intraprocedural_backward_slice(pdg, criterion)
    assert _lines(pdg, slice_ids) == {12, 13, 15, 16}


def test_looped_slice_of_return_total_is_the_whole_loop():
    pdg = _pdg_of("main.py", "looped")
    criterion = _id_at_line(pdg, 25)  # return total
    slice_ids = intraprocedural_backward_slice(pdg, criterion)
    assert _lines(pdg, slice_ids) == {20, 21, 22, 23, 24, 25}
