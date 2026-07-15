"""L4 summary gate: the interprocedural summary must capture a KNOWN transitive
flow. `identity` passes its parameter straight through to its return; `caller`
binds that return to `r` and returns it. So at the `identity(a)` callsite the
argument flows to the callsite's result, and the analyzer must summarize that
pass-through on `caller` as a SummaryEdge actual_in → actual_out.
"""


IDENTITY_FIXTURE = (
    "def identity(x):\n"
    "    return x\n"
    "\n\n"
    "def caller(a):\n"
    "    r = identity(a)\n"
    "    return r\n"
)


def _analyze_l4(proj, cache_dir):
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions

    opts = AnalysisOptions(
        input=proj,
        analysis_level=4,
        graph_field_depth=3,
        no_venv=True,
        cache_dir=cache_dir,
    )
    with Codeanalyzer(opts) as an:
        return an.analyze()


def test_summary_captures_known_transitive_flow(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text(IDENTITY_FIXTURE, encoding="utf-8")

    analysis = _analyze_l4(proj, tmp_path / "cache")
    app = analysis.application
    by_name = {}
    for module in app.symbol_table.values():
        for fn in module.functions.values():
            by_name[fn.name] = fn
    caller = by_name["caller"]

    # caller must carry a pass-through summary edge for the identity(a) callsite:
    # the actual_in (argument `a`) flows to the actual_out (the callsite result),
    # which is exactly the transitive x → <return> flow of `identity` summarized
    # at the call.
    assert caller.summary, "caller should carry a summary edge for the identity(a) callsite"
    pass_through = []
    for s in caller.summary:
        src = caller.body.get(s.src)
        dst = caller.body.get(s.dst)
        if src and dst and src.kind == "actual_in" and dst.kind == "actual_out":
            pass_through.append(s)

    assert pass_through, (
        "expected a SummaryEdge actual_in → actual_out at the identity(a) callsite; "
        f"got {[(s.src, s.dst) for s in caller.summary]}"
    )

    # Both endpoints of each pass-through are rooted at the same callsite (they
    # share the owning callsite's local id), confirming they model one call.
    for s in pass_through:
        ai, ao = caller.body[s.src], caller.body[s.dst]
        assert ai.parent is not None
        assert ai.parent == ao.parent, (
            f"actual_in/actual_out of a summary edge must share a callsite: "
            f"{ai.parent!r} vs {ao.parent!r}"
        )
