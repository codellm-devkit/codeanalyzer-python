"""Stage-3 gate: access paths and reaching-definitions DDG edges.

Contract assertions:
- every DDG edge connects a node that writes the path to a node that reads
  an interfering path;
- the loop-carried dependency (``total = total + i`` in a loop) produces the
  loop-carried (self/cyclic) edge;
- comprehension target variables do not leak defs or uses into the enclosing
  scope (shadowing gate);
- the aliasing fixture (two names, write through one, read through the other)
  produces the may-alias edge.
"""

import ast
from pathlib import Path

from codeanalyzer.dataflow.access_paths import (
    RETURN_PATH,
    build_scope,
    k_limit,
    statement_facts,
)
from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.dataflow.cfg import build_cfg
from codeanalyzer.dataflow.defuse import ddg_edges

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "dataflow"


def _analyzed(file_name: str, func_name: str, k: int = 3, base_types=None):
    tree = ast.parse((FIXTURE / file_name).read_text())

    def find(node, enclosing):
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == func_name:
                return child
        raise AssertionError(f"{func_name} not found")

    func = find(tree, set())
    cfg = build_cfg(func)
    scope = build_scope(func, enclosing_locals=set())
    facts = statement_facts(cfg, func, scope, k)
    edges = ddg_edges(cfg, facts, TypeBasedAliasOracle(base_types or {}))
    return cfg, facts, edges


def _line_of(cfg, node_id):
    return cfg.node_by_id(node_id).start_line


def test_k_limit_contract_example():
    assert k_limit("x.f.g.h", 3) == "x.f.g.*"
    assert k_limit("x.f.g", 3) == "x.f.g"
    assert k_limit("arr[*].f", 3) == "arr[*].f"


def test_every_ddg_edge_connects_a_real_def_to_a_real_use():
    for file_name, func in (
        ("main.py", "branchy"),
        ("main.py", "looped"),
        ("main.py", "handles"),
        ("pipeline.py", "alias_flow"),
        ("state.py", "bump"),
    ):
        cfg, facts, edges = _analyzed(file_name, func)
        for e in edges:
            assert e.var in facts[e.target].uses, f"{func}: {e} is not a read"
            assert facts[e.source].defs, f"{func}: {e} source defines nothing"


def test_branchy_defs_reach_the_return_through_both_arms():
    cfg, facts, edges = _analyzed("main.py", "branchy")
    x_edges = [e for e in edges if e.var == "x"]
    sources = {_line_of(cfg, e.source) for e in x_edges}
    targets = {_line_of(cfg, e.target) for e in x_edges}
    assert sources == {13, 15}, "both arms' defs of x must reach the join"
    assert targets == {16}


def test_looped_produces_the_loop_carried_edge():
    cfg, facts, edges = _analyzed("main.py", "looped")
    # total = total + i (line 23) reads its own previous-iteration def.
    assert any(
        _line_of(cfg, e.source) == 23 and _line_of(cfg, e.target) == 23 and e.var == "total"
        for e in edges
    ), "loop-carried dependency missing"
    # i = i + 1 (line 24) feeds the loop test (line 22) around the back edge.
    assert any(
        _line_of(cfg, e.source) == 24 and _line_of(cfg, e.target) == 22 and e.var == "i"
        for e in edges
    )


def test_comprehension_targets_do_not_leak_across_scopes():
    cfg, facts, edges = _analyzed("main.py", "comprehend")
    comp_line, assign_line, ret_line = 60, 61, 62
    comp_node = next(n for n in cfg.nodes if n.start_line == comp_line)
    # The comprehension defines squares only — its `i` is its own scope.
    assert "i" not in facts[comp_node.id].defs
    assert "i" not in facts[comp_node.id].uses
    assert "items" in facts[comp_node.id].uses
    # The `i` read at the return resolves to line 61, never line 60.
    i_edges = [e for e in edges if e.var == "i" and _line_of(cfg, e.target) == ret_line]
    assert {_line_of(cfg, e.source) for e in i_edges} == {assign_line}


def test_alias_flow_write_through_one_name_reaches_read_through_other():
    cfg, facts, edges = _analyzed(
        "pipeline.py", "alias_flow", base_types={"p": "Box", "q": "Box"}
    )
    # q.value = 42 (line 39) must reach the whole-object read of p at
    # p.get() (line 40) through the type-based may-alias oracle.
    assert any(
        _line_of(cfg, e.source) == 39 and _line_of(cfg, e.target) == 40
        for e in edges
    ), "may-alias edge from q.value write to p read missing"


def test_alias_edge_suppressed_when_types_are_incompatible():
    cfg, facts, edges = _analyzed(
        "pipeline.py", "alias_flow", base_types={"p": "Box", "q": "int"}
    )
    alias_edges = [
        e
        for e in edges
        if _line_of(cfg, e.source) == 39 and _line_of(cfg, e.target) == 40 and e.var.startswith("p")
    ]
    assert not alias_edges, "incompatible types must not alias"


def test_bump_reads_incoming_global_and_param():
    cfg, facts, edges = _analyzed("state.py", "bump")
    assign = next(n for n in cfg.nodes if n.start_line == 8)
    assert {"counter", "amount"} <= facts[assign.id].uses
    assert "counter" in facts[assign.id].defs
    entry_edges = {e.var for e in edges if e.source == cfg.entry_id and e.target == assign.id}
    assert {"counter", "amount"} <= entry_edges


def test_return_defines_the_return_pseudo_path():
    cfg, facts, edges = _analyzed("main.py", "early_exit")
    for n in cfg.nodes:
        if n.kind == "return":
            assert RETURN_PATH in facts[n.id].defs
