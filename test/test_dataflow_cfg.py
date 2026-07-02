"""Stage-1 gate: the exceptional, statement-level CFG.

Contract assertions (dataflow-graphs § verification gates):
- every node maps to a real source span;
- single ENTRY (id 0) / single EXIT (last id), ids contiguous;
- every node is reachable from ENTRY and reaches EXIT;
- every throwing construct in the fixture produces its exception edges;
- node/edge sets are stable across two runs on identical content.
"""

import ast
from pathlib import Path

import pytest

from codeanalyzer.dataflow.cfg import EDGE_KINDS, NODE_KINDS, ControlFlowGraph, build_cfg

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "dataflow"


def _cfg_of(file_name: str, func_name: str) -> ControlFlowGraph:
    tree = ast.parse((FIXTURE / file_name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return build_cfg(node)
    raise AssertionError(f"{func_name} not found in {file_name}")


def _all_fixture_cfgs():
    cfgs = {}
    for file_name in ("main.py", "pipeline.py", "state.py"):
        tree = ast.parse((FIXTURE / file_name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cfgs[f"{file_name}::{node.name}"] = build_cfg(node)
    return cfgs


def _reachable(cfg: ControlFlowGraph, start: int, forward: bool = True) -> set:
    adj = {}
    for e in cfg.edges:
        a, b = (e.source, e.target) if forward else (e.target, e.source)
        adj.setdefault(a, []).append(b)
    seen, stack = set(), [start]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(adj.get(n, []))
    return seen


def _edges(cfg: ControlFlowGraph, kind: str = None):
    return [e for e in cfg.edges if kind is None or e.kind == kind]


def _node_at_line(cfg: ControlFlowGraph, line: int):
    matches = [n for n in cfg.nodes if n.start_line == line]
    assert matches, f"no CFG node at line {line}"
    return matches[0]


# --------------------------------------------------------------------- gates


def test_every_function_has_single_entry_and_exit_with_contiguous_ids():
    for name, cfg in _all_fixture_cfgs().items():
        entries = [n for n in cfg.nodes if n.kind == "entry"]
        exits = [n for n in cfg.nodes if n.kind == "exit"]
        assert len(entries) == 1 and entries[0].id == 0, name
        assert len(exits) == 1 and exits[0].id == len(cfg.nodes) - 1, name
        assert sorted(n.id for n in cfg.nodes) == list(range(len(cfg.nodes))), name


def test_every_node_reachable_from_entry_and_reaches_exit():
    for name, cfg in _all_fixture_cfgs().items():
        ids = {n.id for n in cfg.nodes}
        assert _reachable(cfg, cfg.entry_id, forward=True) == ids, name
        assert _reachable(cfg, cfg.exit_id, forward=False) == ids, name


def test_every_node_maps_to_a_real_source_span():
    for name, cfg in _all_fixture_cfgs().items():
        for n in cfg.nodes:
            assert n.start_line > 0, f"{name} node {n.id} has no source span"


def test_vocabulary_is_the_shared_contract():
    for name, cfg in _all_fixture_cfgs().items():
        for n in cfg.nodes:
            assert n.kind in NODE_KINDS, f"{name}: unknown node kind {n.kind}"
        for e in cfg.edges:
            assert e.kind in EDGE_KINDS, f"{name}: unknown edge kind {e.kind}"


def test_stable_across_two_runs_on_identical_content():
    first = {k: (tuple((n.id, n.kind, n.start_line) for n in c.nodes), tuple(c.edges))
             for k, c in _all_fixture_cfgs().items()}
    second = {k: (tuple((n.id, n.kind, n.start_line) for n in c.nodes), tuple(c.edges))
              for k, c in _all_fixture_cfgs().items()}
    assert first == second


# ----------------------------------------------------------- fixture lowering


def test_branchy_if_has_true_and_false_edges():
    cfg = _cfg_of("main.py", "branchy")
    branch = next(n for n in cfg.nodes if n.kind == "branch")
    kinds = {e.kind for e in cfg.edges if e.source == branch.id}
    assert {"true", "false"} <= kinds


def test_looped_has_loop_back_edge():
    cfg = _cfg_of("main.py", "looped")
    header = next(n for n in cfg.nodes if n.kind == "loop")
    loop_backs = [e for e in _edges(cfg, "loop_back") if e.target == header.id]
    assert loop_backs, "loop-carried back edge missing"


def test_early_exit_multi_exit_is_normalized():
    cfg = _cfg_of("main.py", "early_exit")
    returns = [n for n in cfg.nodes if n.kind == "return"]
    assert len(returns) == 2
    for r in returns:
        assert any(
            e.source == r.id and e.target == cfg.exit_id and e.kind == "return"
            for e in cfg.edges
        ), "return node must edge to EXIT with kind=return"


def test_risky_raise_has_exception_edge_to_exit():
    cfg = _cfg_of("main.py", "risky")
    raise_node = next(n for n in cfg.nodes if n.kind == "raise")
    assert any(
        e.source == raise_node.id and e.target == cfg.exit_id and e.kind == "exception"
        for e in cfg.edges
    )


def test_handles_call_exception_edge_targets_handler():
    cfg = _cfg_of("main.py", "handles")
    handler = next(n for n in cfg.nodes if n.kind == "handler")
    # `v = risky(n)` can raise; its exception edge goes to the handler chain.
    call_stmt = _node_at_line(cfg, 43)
    assert any(
        e.source == call_stmt.id and e.target == handler.id and e.kind == "exception"
        for e in cfg.edges
    )


def test_handles_finally_is_on_normal_and_handler_paths():
    cfg = _cfg_of("main.py", "handles")
    fin = _node_at_line(cfg, 49)  # done = True
    preds = {e.source for e in cfg.edges if e.target == fin.id}
    body_end = _node_at_line(cfg, 44)  # ok = 1
    handler_end = _node_at_line(cfg, 47)  # ok = 0
    assert body_end.id in preds and handler_end.id in preds


def test_with_block_header_defines_scope_and_can_raise():
    cfg = _cfg_of("main.py", "with_block")
    with_node = _node_at_line(cfg, 54)
    assert any(
        e.source == with_node.id and e.kind == "exception" for e in cfg.edges
    ), "with header (__enter__) must carry an exception edge"


def test_gen_yield_edges():
    cfg = _cfg_of("main.py", "gen")
    yield_stmt = _node_at_line(cfg, 68)
    out = [(e.target, e.kind) for e in cfg.edges if e.source == yield_stmt.id]
    kinds = {k for _, k in out}
    assert "yield" in kinds
    assert (cfg.exit_id, "yield") in out, "generator may be abandoned at any yield"


def test_fetch_await_resume_edge():
    cfg = _cfg_of("main.py", "fetch")
    await_stmt = _node_at_line(cfg, 77)
    assert any(
        e.source == await_stmt.id and e.kind == "await_resume" for e in cfg.edges
    )


def test_infinite_loop_gets_synthetic_escape_edge():
    cfg = _cfg_of("main.py", "infinite")
    header = next(n for n in cfg.nodes if n.kind == "loop")
    assert any(
        e.source == header.id and e.target == cfg.exit_id and e.kind == "exception"
        for e in cfg.edges
    ), "infinite loop header must get the documented synthetic edge to EXIT"
