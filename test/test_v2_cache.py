"""Cross-level cache safety.

A cache is keyed on file hash/mtime/size only, so a run that reuses a cache
built at a *different* ``analysis_level`` must not serve stale content: an
``-a 3`` cache carries L3 ``body`` statement nodes (``@entry``/``@exit`` +
``line:col`` statements) and ``cfg``/``cdg``/``ddg`` edges, none of which
belong in an ``-a 1`` output. The loader rejects a level mismatch and rebuilds.
"""
from pathlib import Path

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions


def _all_callables(app):
    """Yield every PyCallable in the application tree (pydantic objects)."""
    def walk_callable(c):
        yield c
        for ic in (c.callables or {}).values():
            yield from walk_callable(ic)
        for cl in (c.types or {}).values():
            yield from walk_class(cl)

    def walk_class(cl):
        for m in (cl.callables or {}).values():
            yield from walk_callable(m)
        for ic in (cl.types or {}).values():
            yield from walk_class(ic)

    for mod in app.symbol_table.values():
        for fn in (mod.functions or {}).values():
            yield from walk_callable(fn)
        for cl in (mod.types or {}).values():
            yield from walk_class(cl)


_SRC = (
    "def f(a):\n"
    "    b = a\n"
    "    g(b)\n"
    "    return b\n"
    "\n"
    "def g(x):\n"
    "    return x\n"
)


def test_cross_level_cache_no_l3_leak(tmp_path: Path):
    """Build at -a 3 (bodies + cfg), then reuse the SAME cache dir at -a 1.
    The L1 result must not carry any L3-only content."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text(_SRC, encoding="utf-8")

    # Build at L3 into a shared cache dir (writes bodies + cfg into the cache).
    opts3 = AnalysisOptions(
        input=proj, cache_dir=tmp_path, no_venv=True,
        analysis_level=3, graphs="cfg,dfg,pdg",
    )
    with Codeanalyzer(opts3) as an:
        res3 = an.analyze()
    assert res3.max_level == 3
    # Sanity: the L3 build really did populate cfg/body statements (so the
    # cross-level assertion below is meaningful, not vacuous).
    assert any(c.cfg for c in _all_callables(res3.application)), "L3 build produced no cfg"

    # Reuse the SAME cache dir at L1.
    opts1 = AnalysisOptions(input=proj, cache_dir=tmp_path, no_venv=True, analysis_level=1)
    with Codeanalyzer(opts1) as an:
        res1 = an.analyze()
    assert res1.max_level == 1

    # No L3 leak: L1 callables carry no cfg/cdg/ddg edges and their body holds
    # only `call` nodes (no @entry/@exit bookends, no statement nodes).
    for c in _all_callables(res1.application):
        assert not c.cfg, f"L3 cfg leaked into L1 output: {c.id}"
        assert not c.cdg, f"L3 cdg leaked into L1 output: {c.id}"
        assert not c.ddg, f"L3 ddg leaked into L1 output: {c.id}"
        for key, node in (c.body or {}).items():
            assert not key.startswith("@"), f"L3 body bookend leaked into L1: {c.id} {key}"
            assert node.kind == "call", (
                f"non-call L3 body node leaked into L1: {c.id} {key} kind={node.kind}"
            )


def test_same_level_cache_reuse_still_works(tmp_path: Path):
    """Same-level reuse is a genuine cache hit that still yields a conformant
    L1 envelope (the level guard is additive; it must not break reuse)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text("def f(a):\n    return a\n", encoding="utf-8")

    opts = AnalysisOptions(input=proj, cache_dir=tmp_path, no_venv=True, analysis_level=1)
    with Codeanalyzer(opts) as an:
        first = an.analyze()
    assert first.schema_version == "2.0.0"

    an2 = Codeanalyzer(opts)
    cache_file = an2.cache_dir / "analysis_cache.json"
    assert cache_file.exists()
    # Loader returns the cached envelope for a same-level request (cache hit,
    # no rebuild forced).
    loaded = an2._load_pyapplication_from_cache(cache_file)
    assert loaded is not None and loaded.max_level == 1

    # A full second run still produces a conformant L1 envelope.
    with Codeanalyzer(opts) as an:
        second = an.analyze()
    assert second.schema_version == "2.0.0"
    assert second.max_level == 1
