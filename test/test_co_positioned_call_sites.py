"""Two `ast.Call` nodes can begin at the same position (#215).

`getattr(o, n)(x)` is that shape exactly: the outer application and the inner
`getattr` both start at the `g`. Keying `body` on the start position alone kept
one of them -- the inner `getattr`, since it is written last -- so the dynamic
invocation vanished from the payload, from the L3/L4 graphs that key off the same
format, and from the Neo4j projection that iterates `body`.

The key gains a disambiguator: `line:col`, then `/2`, `/3`, ... for each further
call site at that position, outermost first. The spelling is adopted verbatim from
codeanalyzer-typescript's `callBodyKeys` (`src/schema/l1Body.ts`).

Spec: docs/design/specs/2026-09-14-co-positioned-call-site-identity.md
"""
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.neo4j.project import project
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema.assign_ids import assign_ids

# `getattr(o, n)(1)` starts at line 2, column 11 -- and so does the `getattr` call
# inside it.
DYNAMIC = "def f(o, n):\n    return getattr(o, n)(1)\n"


def _analyze(tmp_path, source, level=1, name="m.py"):
    proj = tmp_path / "p"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / name).write_text(source)
    app = Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=level, no_venv=True, cache_dir=tmp_path / "c",
    )).analyze().application
    return app


def _callable(app, file_key="m.py", name="f"):
    return app.symbol_table[file_key].functions[name]


def test_each_co_positioned_call_gets_its_own_body_node(tmp_path):
    f = _callable(_analyze(tmp_path, DYNAMIC))
    assert sorted(f.body) == ["2:11", "2:11/2"]


def test_the_outermost_call_keeps_the_bare_key(tmp_path):
    f = _callable(_analyze(tmp_path, DYNAMIC))
    outer, inner = f.body["2:11"], f.body["2:11/2"]
    assert outer.kind == "call" and inner.kind == "call"
    # The invocation is of whatever `getattr` returned -- not of `getattr`.
    assert outer.method_name == "<unknown>"
    assert inner.method_name == "getattr"


def test_a_call_of_a_call_resolves_to_no_callee(tmp_path):
    """`_callee_anchor` used to fall back to the call expression's start for any
    non-attribute callee, so the outer call was anchored on `getattr` and Jedi
    labelled it a call to `builtins.getattr`."""
    f = _callable(_analyze(tmp_path, DYNAMIC, level=2))
    by_pos = {(cs.start_line, cs.start_column, cs.method_name): cs for cs in f.call_sites}
    outer = by_pos[(2, 11, "<unknown>")]
    inner = by_pos[(2, 11, "getattr")]
    assert outer.callee_signature is None
    assert inner.callee_signature == "builtins.getattr"
    assert f.body["2:11"].callee is None
    assert f.body["2:11/2"].callee.endswith("/@external/builtins/getattr")


def test_a_chain_of_applications_gets_one_key_each(tmp_path):
    f = _callable(_analyze(tmp_path, "def f(g):\n    return g()()()\n"))
    assert sorted(f.body) == ["2:11", "2:11/2", "2:11/3"]


def test_keys_that_do_not_collide_are_untouched(tmp_path):
    f = _callable(_analyze(tmp_path, "def f(a, b):\n    return len(a) + str(b)\n"))
    assert sorted(f.body) == ["2:11", "2:20"]


def test_the_key_sequence_is_stable_across_runs(tmp_path):
    first = sorted(_callable(_analyze(tmp_path, DYNAMIC)).body)
    second = sorted(_callable(_analyze(tmp_path / "again", DYNAMIC)).body)
    assert first == second == ["2:11", "2:11/2"]


def test_the_graph_carries_both_nodes_and_joins_signatures_per_call_site(tmp_path):
    """`sig_by_pos` joined `callee_signature` on (line, column), so two call sites
    at one position shared whichever signature the dict kept last."""
    app = _analyze(tmp_path, DYNAMIC, level=2)
    f = _callable(app)
    rows = project(app, "p", assign_ids(app, "p"))
    body = {
        n.value: n.props for n in rows.nodes
        if n.labels[0] == "PyBodyNode" and n.value.startswith(f.id + "@")
    }
    assert f.id + "@2:11" in body and f.id + "@2:11/2" in body
    assert "callee_signature" not in body[f.id + "@2:11"]
    assert body[f.id + "@2:11/2"]["callee_signature"] == "builtins.getattr"
