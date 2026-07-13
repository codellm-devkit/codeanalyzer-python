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


L4_FIXTURE = "def id_(x):\n    return x\n\n\ndef caller():\n    return id_(5)\n"


def test_emit_l4_end_to_end_param_vertices_summary_and_param_edges(tmp_path):
    """Drive the whole analyzer at ``-a 4`` over a pass-through call and assert
    the interprocedural L4 delta lands on the v2 tree, layered on top of the L3
    overlay: synthetic param vertices in each callable's ``body``, a ``summary``
    edge on the caller, and application-level ``param_in`` / ``param_out`` edges
    resolving actual↔formal across the two functions. ``CALL`` SDG edges are
    dropped (already in the call graph). Scalpel is the primary oracle here; the
    param/summary structure is oracle-independent, so this passes whether Scalpel
    runs or falls back to the type-based oracle.
    """
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text(L4_FIXTURE, encoding="utf-8")

    opts = AnalysisOptions(
        input=proj,
        analysis_level=4,
        graph_field_depth=3,
        no_venv=True,
        cache_dir=tmp_path / "cache",
    )
    with Codeanalyzer(opts) as an:
        analysis = an.analyze()

    app = analysis.application
    by_name = {}
    for module in app.symbol_table.values():
        for fn in module.functions.values():
            by_name[fn.name] = fn
    id_fn, caller_fn = by_name["id_"], by_name["caller"]

    # (a) id_ carries its formal_in(x) and its sole formal_out param vertices.
    assert id_fn.body["@formal_in:0"].kind == "formal_in"
    assert id_fn.body["@formal_in:0"].of == "x"
    assert id_fn.body["@formal_out"].kind == "formal_out"

    # caller carries actual_in / actual_out vertices at the id_(5) callsite,
    # each rooted at (and pointing back to) its owning callsite local id.
    actual_in_keys = [k for k, n in caller_fn.body.items() if n.kind == "actual_in"]
    actual_out_keys = [k for k, n in caller_fn.body.items() if n.kind == "actual_out"]
    assert actual_in_keys, "caller should have an actual_in at the id_(5) callsite"
    assert actual_out_keys, "caller should have an actual_out at the id_(5) callsite"
    for k in actual_in_keys + actual_out_keys:
        node = caller_fn.body[k]
        assert node.parent is not None
        assert k.startswith(node.parent + "/")

    # (c) param_in: an application edge whose dst is id_'s @formal_in:0 global id,
    # sourced at a caller actual_in (cross-function resolution via both maps).
    formal_in_gid = f"{id_fn.id}@formal_in:0"
    matching_in = [e for e in app.param_in if e.dst == formal_in_gid]
    assert matching_in, (
        f"param_in should target {formal_in_gid}; got {[e.dst for e in app.param_in]}"
    )
    assert all("actual_in" in e.src for e in matching_in)

    # param_out mirrors: an edge whose src is id_'s formal_out global id, landing
    # on a caller actual_out.
    formal_out_gid = f"{id_fn.id}@formal_out"
    matching_out = [e for e in app.param_out if e.src == formal_out_gid]
    assert matching_out, (
        f"param_out should originate at {formal_out_gid}; got {[e.src for e in app.param_out]}"
    )
    assert all("actual_out" in e.dst for e in matching_out)

    # (b) a pass-through summary edge on caller (actual_in → actual_out).
    assert caller_fn.summary, "caller should carry a pass-through summary edge"
    assert any(
        "actual_in" in s.src and "actual_out" in s.dst for s in caller_fn.summary
    )

    # (d) no CALL SDG edge leaks: no body vertex is a CALL, and no param edge
    # touches a callee @entry (the node a CALL edge would have targeted).
    for fn in (id_fn, caller_fn):
        assert all(n.kind != "CALL" for n in fn.body.values())
    for e in list(app.param_in) + list(app.param_out):
        assert not e.src.endswith("@entry") and not e.dst.endswith("@entry")
