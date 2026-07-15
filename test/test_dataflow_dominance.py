"""Stage-2 gate: post-dominators and control dependence.

Contract assertions:
- the post-dominator tree is a tree with unique root EXIT (infinite loops
  included, thanks to the CFG's synthetic escape edge);
- hand-computed control dependences for the fixture's if / loop /
  early-return functions match exactly.
"""

import ast
from pathlib import Path

from codeanalyzer.dataflow.cfg import ControlFlowGraph, build_cfg
from codeanalyzer.dataflow.dominance import control_dependence, post_dominators

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "dataflow"


def _cfg_of(file_name: str, func_name: str) -> ControlFlowGraph:
    tree = ast.parse((FIXTURE / file_name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return build_cfg(node)
    raise AssertionError(f"{func_name} not found")


def _all_fixture_cfgs():
    cfgs = {}
    for file_name in ("main.py", "pipeline.py", "state.py"):
        tree = ast.parse((FIXTURE / file_name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cfgs[f"{file_name}::{node.name}"] = build_cfg(node)
    return cfgs


def _by_line(cfg: ControlFlowGraph):
    """id ↔ line helpers for hand-computed expectations."""
    return {n.start_line: n.id for n in cfg.nodes if n.kind not in ("entry", "exit")}


def test_post_dominator_tree_is_rooted_at_exit_for_every_function():
    for name, cfg in _all_fixture_cfgs().items():
        ipdom = post_dominators(cfg)
        ids = {n.id for n in cfg.nodes}
        assert set(ipdom) == ids, f"{name}: some node has no post-dominator"
        assert ipdom[cfg.exit_id] == cfg.exit_id, name
        # Tree: walking up from any node terminates at EXIT without cycles.
        for n in ids:
            seen = set()
            cur = n
            while cur != cfg.exit_id:
                assert cur not in seen, f"{name}: ipdom cycle at {cur}"
                seen.add(cur)
                cur = ipdom[cur]


def test_branchy_control_dependence_exact():
    cfg = _cfg_of("main.py", "branchy")
    line = _by_line(cfg)
    header, then_s, else_s, ret = line[12], line[13], line[15], line[16]
    expected = {
        (cfg.entry_id, header),
        (cfg.entry_id, ret),
        (header, then_s),
        (header, else_s),
    }
    assert set(control_dependence(cfg)) == expected


def test_looped_control_dependence_exact():
    cfg = _cfg_of("main.py", "looped")
    line = _by_line(cfg)
    s_total, s_i, header, s_add, s_inc, ret = (
        line[20], line[21], line[22], line[23], line[24], line[25],
    )
    expected = {
        (cfg.entry_id, s_total),
        (cfg.entry_id, s_i),
        (cfg.entry_id, header),
        (cfg.entry_id, ret),
        (header, s_add),
        (header, s_inc),
    }
    assert set(control_dependence(cfg)) == expected


def test_early_exit_control_dependence_exact():
    cfg = _cfg_of("main.py", "early_exit")
    line = _by_line(cfg)
    header, ret1, s_y, ret2 = line[29], line[30], line[31], line[32]
    expected = {
        (cfg.entry_id, header),
        (header, ret1),
        (header, s_y),
        (header, ret2),
    }
    assert set(control_dependence(cfg)) == expected


def test_infinite_loop_post_dominance_well_formed():
    cfg = _cfg_of("main.py", "infinite")
    ipdom = post_dominators(cfg)
    assert set(ipdom) == {n.id for n in cfg.nodes}
