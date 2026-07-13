import json
import subprocess
import sys
from pathlib import Path

from conftest_v2 import assert_conformant


def _run(proj, level):
    out = subprocess.run(
        [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", str(level), "--no-venv"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _callables(app):
    def wc(c):
        yield c
        for ic in (c.get("inner_callables") or {}).values():
            yield from wc(ic)
        for cl in (c.get("inner_classes") or {}).values():
            yield from wcl(cl)

    def wcl(cl):
        for m in (cl.get("methods") or {}).values():
            yield from wc(m)
        for ic in (cl.get("inner_classes") or {}).values():
            yield from wcl(ic)

    for mod in app["symbol_table"].values():
        for fn in (mod.get("functions") or {}).values():
            yield from wc(fn)
        for cl in (mod.get("classes") or {}).values():
            yield from wcl(cl)


def test_l1_subset_of_l2(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text("def g():\n    return 1\ndef f():\n    return g()\n", encoding="utf-8")
    l1, l2 = _run(proj, 1), _run(proj, 2)
    assert_conformant(l1, max_level=1)
    assert_conformant(l2, max_level=2)
    a1, a2 = l1["application"], l2["application"]
    # every module + callable id present at L1 is present at L2
    ids1 = {c["id"] for c in _callables(a1)}
    ids2 = {c["id"] for c in _callables(a2)}
    assert ids1 <= ids2, "L2 dropped a callable present at L1"
    # every L1 body node key is present at L2 (callee may refine null→id)
    for c1 in _callables(a1):
        c2 = next(c for c in _callables(a2) if c["id"] == c1["id"])
        assert set(c1.get("body", {})) <= set(c2.get("body", {})), \
            f"L2 dropped a body node from {c1['id']}"


def test_l2_subset_of_l3(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    # Multi-statement fixture with a bare call: the `g(b)` expression statement
    # is materialized at L1/L2 as a `call` body node keyed by its local
    # "line:col"; L3 lands its CFG statement node on the SAME key, so the fact
    # must survive (not be dropped or re-keyed).
    (proj / "m.py").write_text(
        "def g(x):\n    return x\ndef f(a):\n    b = a\n    g(b)\n    return b\n",
        encoding="utf-8",
    )
    l2, l3 = _run(proj, 2), _run(proj, 3)
    assert_conformant(l2, max_level=2)
    assert_conformant(l3, max_level=3)
    a2, a3 = l2["application"], l3["application"]
    # every callable id present at L2 is present at L3
    ids2 = {c["id"] for c in _callables(a2)}
    ids3 = {c["id"] for c in _callables(a3)}
    assert ids2 <= ids3, "L3 dropped a callable present at L2"
    # L3 only ADDS body statements (+ @entry/@exit); every L2 body key survives.
    for c2 in _callables(a2):
        c3 = next(c for c in _callables(a3) if c["id"] == c2["id"])
        assert set(c2.get("body", {})) <= set(c3.get("body", {})), \
            f"L3 dropped a body node from {c2['id']}"


def _ddg_edges(app):
    """Provenance-tagged ddg edge tuples, keyed per owning callable so local
    line:col ids never collide across functions."""
    edges = set()
    for c in _callables(app):
        for e in c.get("ddg", []):
            edges.add((c["id"], e["src"], e["dst"], e.get("var"), tuple(e.get("prov", []))))
    return edges


def test_l3_subset_of_l4(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    # Multi-statement, multi-call fixture: `f` threads `a` through two callees,
    # so L3 lands syntactic ssa def-use edges and L4 layers the interprocedural
    # delta (param vertices + param_in/param_out + summaries) additively on top.
    (proj / "m.py").write_text(
        "def g(x):\n    return x\n"
        "def h(y):\n    return y\n"
        "def f(a):\n    b = g(a)\n    c = h(b)\n    return c\n",
        encoding="utf-8",
    )
    l3, l4 = _run(proj, 3), _run(proj, 4)
    assert_conformant(l3, max_level=3)
    assert_conformant(l4, max_level=4)
    a3, a4 = l3["application"], l4["application"]
    # every callable id present at L3 is present at L4
    ids3 = {c["id"] for c in _callables(a3)}
    ids4 = {c["id"] for c in _callables(a4)}
    assert ids3 <= ids4, "L4 dropped a callable present at L3"
    # L4 only ADDS param vertices to bodies; every L3 body key survives.
    for c3 in _callables(a3):
        c4 = next(c for c in _callables(a4) if c["id"] == c3["id"])
        assert set(c3.get("body", {})) <= set(c4.get("body", {})), \
            f"L4 dropped a body node from {c3['id']}"
    # The L3 ddg (all ssa) is a subset of the L4 ddg (ssa + points-to): L4 keeps
    # every L3 ssa edge verbatim and only adds alias-derived points-to edges.
    ddg3, ddg4 = _ddg_edges(a3), _ddg_edges(a4)
    assert ddg3, "expected the L3 fixture to produce ssa ddg edges"
    assert all(prov == ("ssa",) for *_rest, prov in ddg3), "L3 ddg must be ssa-only"
    assert ddg3 <= ddg4, "L4 dropped an L3 ssa ddg edge (must be a superset)"
