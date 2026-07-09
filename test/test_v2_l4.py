import ast
import logging
import sys

import pytest

from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.dataflow.identity import IdentityMap
from codeanalyzer.dataflow.sdg import ParamNode
from codeanalyzer.utils import logger as ca_logger

COPY_CHAIN = "def f(a):\n    b = a\n    c = b\n    return c\n"


class _Node:
    def __init__(self, id, start_line, start_column):
        self.id, self.start_line, self.start_column = id, start_line, start_column


class _CFG:
    def __init__(self, nodes, entry_id, exit_id):
        self.nodes = nodes
        self.entry_id, self.exit_id = entry_id, exit_id


class _PDG:
    def __init__(self, cfg):
        self.cfg = cfg


def test_identity_map_param_vertex_local_and_global_ids():
    """L4 synthetic param vertices fold into the same id space as CFG nodes:
    formal_in/out carry the callable-scoped ``@formal_*`` locals, actuals carry
    ``<callsite-local>/actual_*`` locals rooted at the owning callsite."""
    # CFG: entry (0), a callsite statement (3) at line 5 col 4 → local "5:4",
    # and exit (4).
    nodes = [_Node(0, 1, 0), _Node(3, 5, 4), _Node(4, 6, 0)]
    pdg = _PDG(_CFG(nodes, entry_id=0, exit_id=4))
    params = [
        ParamNode(id=10, kind="formal_in", var="a"),
        ParamNode(id=11, kind="formal_out", var="<return>"),
        ParamNode(id=12, kind="actual_in", var="arg0", call_node=3),
        ParamNode(id=13, kind="actual_out", var="<return>", call_node=3),
    ]
    cid = "can://python/app/m.py/f(a)"
    im = IdentityMap.for_function(cid, pdg, param_nodes=params)

    # LOCAL ids
    assert im.local(10) == "@formal_in:0"
    assert im.local(11) == "@formal_out"           # sole formal_out: no idx
    assert im.local(12) == "5:4/actual_in:0"        # rooted at callsite local
    assert im.local(13) == "5:4/actual_out"         # sole actual_out: no idx

    # GLOBAL ids (single '@' composition works for both forms)
    assert im.global_id(10) == f"{cid}@formal_in:0"
    assert im.global_id(12) == f"{cid}@5:4/actual_in:0"
    assert im.global_id(11) == f"{cid}@formal_out"
    assert im.global_id(13) == f"{cid}@5:4/actual_out"

    # CFG nodes still resolve unchanged.
    assert im.local(0) == "@entry"
    assert im.local(3) == "5:4"


def test_identity_map_param_vertices_multiplicity_gets_idx_suffix():
    """>1 formal_out (or >1 actual_out sharing a callsite) each get an idx
    suffix; formal_in / actual_in are always indexed; and formal vs actual are
    grouped independently by (kind, call_node)."""
    nodes = [_Node(0, 1, 0), _Node(3, 5, 4), _Node(4, 6, 0)]
    pdg = _PDG(_CFG(nodes, entry_id=0, exit_id=4))
    params = [
        ParamNode(id=20, kind="formal_in", var="a"),
        ParamNode(id=21, kind="formal_in", var="b"),
        ParamNode(id=22, kind="formal_out", var="<return>"),
        ParamNode(id=23, kind="formal_out", var="a"),        # param mutated
        ParamNode(id=24, kind="actual_in", var="arg0", call_node=3),
        ParamNode(id=25, kind="actual_in", var="arg1", call_node=3),
        ParamNode(id=26, kind="actual_out", var="<return>", call_node=3),
        ParamNode(id=27, kind="actual_out", var="a", call_node=3),
    ]
    im = IdentityMap.for_function("can://python/app/m.py/f(a,b)", pdg, param_nodes=params)

    assert im.local(20) == "@formal_in:0"
    assert im.local(21) == "@formal_in:1"
    assert im.local(22) == "@formal_out:0"          # >1 → idx suffix
    assert im.local(23) == "@formal_out:1"
    assert im.local(24) == "5:4/actual_in:0"
    assert im.local(25) == "5:4/actual_in:1"
    assert im.local(26) == "5:4/actual_out:0"       # >1 sharing callsite → idx
    assert im.local(27) == "5:4/actual_out:1"


class _ListHandler(logging.Handler):
    """Capture records straight off the (non-propagating) codeanalyzer logger."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_make_alias_oracle_falls_back_when_scalpel_absent(monkeypatch):
    """`make_alias_oracle` degrades to TypeBasedAliasOracle behavior and logs
    once when Scalpel cannot be imported — regardless of whether the optional
    dependency is actually installed in the runner."""
    import codeanalyzer.dataflow.scalpel_oracle as so

    # Force `import scalpel...` to raise ImportError even if it is installed:
    # a None entry in sys.modules makes the import machinery halt. Cover the
    # top-level package and every submodule the oracle imports so a previously
    # cached submodule cannot satisfy the import.
    for mod in ("scalpel", "scalpel.cfg", "scalpel.SSA", "scalpel.SSA.const"):
        monkeypatch.setitem(sys.modules, mod, None)

    # Reset the process-wide "logged once" guard and capture on the actual
    # (propagate=False) codeanalyzer logger.
    monkeypatch.setattr(so, "_fallback_logged", False)
    handler = _ListHandler()
    ca_logger.addHandler(handler)
    old_level = ca_logger.level
    ca_logger.setLevel(logging.INFO)
    try:
        func_ast = ast.parse(COPY_CHAIN).body[0]
        oracle = so.make_alias_oracle(None, func_ast, {})
    finally:
        ca_logger.removeHandler(handler)
        ca_logger.setLevel(old_level)

    # Fell back to the type-based oracle (never a ScalpelAliasOracle).
    assert isinstance(oracle, TypeBasedAliasOracle)
    # Frozen fallback behavior: identical paths alias; two distinct bare bases
    # (non-addressable locals) do not.
    assert oracle.may_alias("a", "a") is True
    assert oracle.may_alias("a", "b") is False
    # Exactly the fallback notice was emitted.
    assert any("scalpel" in r.getMessage().lower() for r in handler.records), (
        f"expected a fallback log record, got {[r.getMessage() for r in handler.records]}"
    )


def test_scalpel_oracle_copy_chain():
    """When Scalpel is importable, the copy chain `b = a; c = b` places a, b, c
    in one copy-closure class: a/b may-alias, an unrelated name does not."""
    pytest.importorskip("scalpel")
    from codeanalyzer.dataflow.scalpel_oracle import ScalpelAliasOracle

    func_ast = ast.parse(COPY_CHAIN).body[0]
    oracle = ScalpelAliasOracle.from_function(func_ast)

    assert oracle.may_alias("a", "b") is True
    assert oracle.may_alias("a", "x") is False
