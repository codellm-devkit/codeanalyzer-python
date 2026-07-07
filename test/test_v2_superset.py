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
