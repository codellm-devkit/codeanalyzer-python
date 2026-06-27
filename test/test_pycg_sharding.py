"""Tests for PyCG executor scoping: dependency exclusion and the max_iter cap.

These drive the real PyCG wrapper, so they require ``pycg`` (a level-2 install
dependency).  They are deliberately tiny (a few files) so they run fast.
"""
from pathlib import Path

import pytest

pytest.importorskip("PyCG")

from codeanalyzer.semantic_analysis.pycg.pycg_analysis import (
    PyCG,
    _PyCGCallableResolver,
    _shard_symlink_root,
)


def test_max_iter_default_and_override(tmp_path):
    p = PyCG(tmp_path)
    assert p.max_iter == PyCG._PYCG_MAX_ITER == 50
    assert PyCG(tmp_path, max_iter=7).max_iter == 7
    assert PyCG(tmp_path, max_iter=-1).max_iter == -1


def test_adaptive_decomposition_splits_runaways(tmp_path, monkeypatch):
    """A shard that 'runs away' is re-decomposed until its pieces converge.

    Drives the real adaptive loop + planner, but stubs the PyCG runner with a
    size threshold: shards larger than the threshold time out (runaway); smaller
    ones converge and yield one synthetic edge per file.  A 16-file coupled
    cluster must therefore be split across rounds until every piece is small
    enough, with no files lost.
    """
    from codeanalyzer.schema.py_schema import PyCallEdge, PyCallable, PyModule
    from codeanalyzer.semantic_analysis.pycg.pycg_analysis import _PyCGCallableResolver

    # A loosely-coupled chain of 40 modules: splittable (not one atomic cycle).
    st, jedi = {}, []
    for i in range(40):
        path = f"/proj/m{i}.py"
        st[path] = PyModule(
            file_path=path, module_name=f"m{i}",
            functions={"f": PyCallable(signature=f"m{i}.f", name="f", path=path)},
        )
        if i:
            jedi.append(PyCallEdge(source=f"m{i-1}.f", target=f"m{i}.f", weight=1,
                                   provenance=["jedi"]))

    # threshold >= the decomposition floor (10) so pieces can shrink enough to converge.
    pycg = PyCG(tmp_path, shard_ceiling=40)
    threshold = 12  # shards with > 12 files "diverge"
    rounds_seen = []

    def fake_runner(shards):
        rounds_seen.append([len(s) for s in shards])
        edges, runaways = [], []
        for files in shards:
            if len(files) > threshold:
                runaways.append(files)
            else:
                edges += [PyCallEdge(source=f, target="x", weight=1, provenance=["pycg"])
                          for f in files]
        return edges, runaways

    monkeypatch.setattr(pycg, "_run_fileset_shards_seq", fake_runner)
    edges = pycg._build_sharded_planned(jedi, st, _PyCGCallableResolver(set()))

    assert len(rounds_seen) >= 2, "runaway shard was never decomposed"
    # Every shard that was finally accepted is within the convergence threshold.
    assert all(sz <= threshold for sz in rounds_seen[-1])
    # No files lost: one pycg edge per file across all 16 modules.
    assert len({e.source for e in edges}) == 40


def test_pycg_does_not_follow_into_in_tree_dependency(tmp_path):
    """An in-tree ``.codeanalyzer`` venv under project_dir must stay a ghost.

    PyCG bounds analysis to its ``package`` directory; running inside the
    symlink mini-project keeps that bound on project source only, so imports
    into a bundled dependency are recorded as ghost edges but never analysed.
    Regression guard for the dep-reach blowup.
    """
    proj = tmp_path
    app = proj / "app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "main.py").write_text("import bigdep\ndef run():\n    return bigdep.work()\n")

    # A bundled dependency with many internal functions: if PyCG followed into
    # it, dozens of bigdep.fN definitions/edges would appear.
    dep = proj / ".codeanalyzer" / "venv" / "site-packages" / "bigdep"
    dep.mkdir(parents=True)
    body = "".join(f"def f{i}(x):\n    return f{(i + 1) % 50}(x)\n" for i in range(50))
    body += "def work():\n    return f0(1)\n"
    (dep / "__init__.py").write_text(body)

    pycg = PyCG(proj)
    pycg._ensure_pycg_loaded()
    resolver = _PyCGCallableResolver(set())
    entry_points = [str(app / "__init__.py"), str(app / "main.py")]
    with _shard_symlink_root(entry_points, proj) as (root, eps):
        edges = pycg._run_pycg_batch(eps, root, resolver, prefix="")

    nodes = {n for e in edges for n in (e.source, e.target)}
    # bigdep is reachable as a ghost target ...
    assert any(n.startswith("bigdep") for n in nodes)
    # ... but none of its internals were analysed.
    assert not [n for n in nodes if n.startswith("bigdep.f")]
    # and the real app edge is present.
    assert any(e.source == "app.main.run" and e.target == "bigdep.work" for e in edges)
