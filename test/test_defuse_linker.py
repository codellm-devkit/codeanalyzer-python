"""Unit + integration tests for the defuse linker (#148).

One small two-module project exercises every MVP resolution rule; the
pipeline runs once per session and individual tests assert against it.
"""
import json

import pytest
from typer.testing import CliRunner

from codeanalyzer.__main__ import app as cli_app
from codeanalyzer.semantic_analysis.defuse_linker import (
    _module_qual,
    defuse_linker_edges,
)

_ENV = {"NO_COLOR": "1", "TERM": "dumb"}

_HELPERS = '''\
def helper(x):
    return x


def unused(x):
    return x
'''

_MAIN = '''\
from helpers import helper
from helpers import unused as spare


class Base:
    def shared(self):
        return 1


class Mixin:
    pass


class Thing(Base):
    def caller(self):
        return self.shared()

    def sibling(self):
        return 2

    def not_reachable(self):
        # bare-name call to a sibling method: class scope is transparent,
        # so this must NOT resolve to Thing.sibling
        return sibling()  # noqa: F821


def target(v):
    return v


def recurse(n):
    if n:
        return recurse(n - 1)
    return 0


def aliased():
    f = target
    g = f
    return g(1)


def imported():
    return helper(2)


def re_imported():
    return spare(3)


def outer():
    def inner():
        return 4

    use = inner
    return use()
'''


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    proj = tmp_path_factory.mktemp("linkerproj")
    (proj / "helpers.py").write_text(_HELPERS, encoding="utf-8")
    (proj / "main.py").write_text(_MAIN, encoding="utf-8")
    return proj


@pytest.fixture(scope="module")
def stripped_edges(project, tmp_path_factory):
    """Linker output with every Jedi resolution stripped first.

    On code this small Jedi resolves nearly every site itself, and the linker
    only looks at what Jedi left unresolved — so exercising the linker's own
    resolution rules requires blanking ``callee_signature`` before the call.
    """
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions

    opts = AnalysisOptions(
        input=project,
        output=None,
        cache_dir=tmp_path_factory.mktemp("cache"),
        no_venv=True,
        analysis_level=1,
    )
    table = Codeanalyzer(opts)._build_symbol_table({})
    for m in table.values():
        for c in _walk(m):
            for site in c.call_sites:
                site.callee_signature = None
    edges, resolutions = defuse_linker_edges(table)
    return {(e.src, e.dst) for e in edges}, resolutions


def _has(pairs, src_part, dst_part):
    return any(src_part in s and dst_part in d for s, d in pairs)


@pytest.fixture(scope="module")
def analysis(project, tmp_path_factory):
    out = tmp_path_factory.mktemp("linkerout")
    r = CliRunner().invoke(
        cli_app,
        ["--input", str(project), "--output", str(out), "--no-venv", "-a", "2"],
        env=_ENV,
    )
    assert r.exit_code == 0, r.output
    return json.loads((out / "analysis.json").read_text())


def test_alias_chain_resolves(stripped_edges):
    pairs, _ = stripped_edges
    assert _has(pairs, "aliased", "target")


def test_from_import_resolves_cross_module(stripped_edges):
    pairs, _ = stripped_edges
    assert _has(pairs, "imported", "helpers.helper")


def test_import_alias_resolves(stripped_edges):
    pairs, _ = stripped_edges
    assert _has(pairs, "re_imported", "helpers.unused")


def test_self_call_resolves_through_base(stripped_edges):
    pairs, _ = stripped_edges
    assert _has(pairs, "Thing.caller", "Base.shared")


def test_recursion_self_loop_kept(stripped_edges):
    pairs, _ = stripped_edges
    assert ("main.recurse", "main.recurse") in pairs


def test_nested_def_via_local_alias(stripped_edges):
    pairs, _ = stripped_edges
    assert _has(pairs, "outer", "outer.inner")


def test_class_scope_is_transparent(stripped_edges):
    """`sibling()` bare inside a method must not resolve to the sibling method."""
    pairs, _ = stripped_edges
    assert not _has(pairs, "not_reachable", "sibling")


def test_resolutions_key_by_caller_and_position(stripped_edges):
    _, resolutions = stripped_edges
    assert all(
        isinstance(k, tuple) and len(k) == 2 and ":" in k[1] for k in resolutions
    )


def test_prov_vocabulary(analysis):
    provs = {p for e in analysis["application"]["call_graph"] for p in e["prov"]}
    assert provs <= {"jedi", "defuse"}


def test_callee_backfilled_on_body_nodes(analysis):
    """A linker resolution must reach the L1 call node's `callee` (null->id)."""
    st = analysis["application"]["symbol_table"]
    mod = st["main.py"]
    fn = mod["functions"]["imported"]
    call_nodes = [n for n in fn["body"].values() if n["kind"] == "call"]
    assert call_nodes and any(
        n.get("callee") and "helper" in n["callee"] for n in call_nodes
    ), call_nodes


def test_linker_does_not_mutate_call_sites(project, tmp_path):
    """Cache safety: resolutions are returned, never written into
    callee_signature — a persisted resolution would resurface as a Jedi edge."""
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions

    opts = AnalysisOptions(
        input=project, output=None, cache_dir=tmp_path, no_venv=True, analysis_level=1
    )
    table = Codeanalyzer(opts)._build_symbol_table({})
    before = {
        (c.signature, s.start_line, s.start_column): s.callee_signature
        for m in table.values()
        for c in _walk(m)
        for s in c.call_sites
    }
    edges, resolutions = defuse_linker_edges(table)
    after = {
        (c.signature, s.start_line, s.start_column): s.callee_signature
        for m in table.values()
        for c in _walk(m)
        for s in c.call_sites
    }
    assert edges and resolutions
    assert before == after, "linker must not write callee_signature"

    edges2, resolutions2 = defuse_linker_edges(table)
    assert [
        (e.src, e.dst, e.weight, e.prov) for e in edges
    ] == [(e.src, e.dst, e.weight, e.prov) for e in edges2]
    assert resolutions == resolutions2


def _walk(mod):
    def from_callable(c):
        yield c
        for n in (c.callables or {}).values():
            yield from from_callable(n)
        for cls in (c.types or {}).values():
            yield from from_class(cls)

    def from_class(cls):
        for m in (cls.callables or {}).values():
            yield from from_callable(m)
        for i in (cls.types or {}).values():
            yield from from_class(i)

    for f in (mod.functions or {}).values():
        yield from from_callable(f)
    for cls in (mod.types or {}).values():
        yield from from_class(cls)


def test_module_qual():
    assert _module_qual("requests/api.py") == "requests.api"
    assert _module_qual("requests/__init__.py") == "requests"
    assert _module_qual("src/flask/app.py") == "src.flask.app"
