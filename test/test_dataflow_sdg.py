"""Stage 6–7 gates: summaries and SDG assembly on the dataflow fixture.

Contract assertions (dataflow-graphs § verification gates):
- summary gate: a composed summary routes a parameter to the return value
  across a call chain; the mutual-recursion SCC reaches fixpoint and its
  summary is identical across two runs;
- SDG gate: no dangling (signature, node_id) endpoints; PARAM_IN targets
  match the callee's declared formals; SUMMARY edges exist for a known
  transitive flow; the module-global write/read pair is stitched across
  files; closure captures bind at the definition site.
"""

from pathlib import Path

import pytest

from codeanalyzer.dataflow.builder import build_program_graphs
from codeanalyzer.dataflow.sdg import CAPTURE_PREFIX, GLOBAL_PREFIX
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.core import Codeanalyzer

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "dataflow"


@pytest.fixture(scope="module")
def fixture_app(tmp_path_factory):
    cache = tmp_path_factory.mktemp("dataflow-cache")
    options = AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=cache
    )
    with Codeanalyzer(options) as analyzer:
        return analyzer.analyze()


@pytest.fixture(scope="module")
def ir(fixture_app):
    return build_program_graphs(fixture_app)


def _sig(ir_or_app, suffix: str) -> str:
    functions = ir_or_app.functions
    matches = [s for s in functions if s == suffix or s.endswith("." + suffix)]
    assert matches, f"no function graph for *{suffix}: have {sorted(functions)[:10]}..."
    assert len(matches) == 1, f"ambiguous suffix {suffix}: {matches}"
    return matches[0]


def _valid_ids(ir, sig) -> set:
    fg = ir.functions[sig]
    return {n.id for n in fg.pdg.cfg.nodes} | {p.id for p in fg.param_nodes}


# ------------------------------------------------------------- summary gate


def test_summary_routes_parameter_through_the_call_chain(ir):
    for name in ("chain_a", "chain_b", "chain_c"):
        summary = ir.functions[_sig(ir, name)].summary
        assert ("param:v", "return") in summary.flows, name


def test_mutual_recursion_scc_reaches_identical_fixpoint(fixture_app):
    first = build_program_graphs(fixture_app)
    second = build_program_graphs(fixture_app)
    for name in ("even", "odd"):
        s1 = first.functions[_sig(first, name)].summary
        s2 = second.functions[_sig(second, name)].summary
        assert ("param:n", "return") in s1.flows, name
        assert s1 == s2, name


def test_bump_summary_records_the_global_write(ir):
    summary = ir.functions[_sig(ir, "bump")].summary
    assert any(g.endswith("::counter") for g in summary.global_writes)
    assert any(
        key == "param:amount" and out.startswith("global:") and out.endswith("::counter")
        for key, out in summary.flows
    )


def test_mutate_summary_records_caller_visible_param_mutation(ir):
    summary = ir.functions[_sig(ir, "mutate")].summary
    assert "items" in summary.mutated_params


# ----------------------------------------------------------------- SDG gate


def test_no_dangling_sdg_endpoints(ir):
    for e in ir.sdg_edges:
        assert e.source_sig in ir.functions, e
        assert e.target_sig in ir.functions, e
        assert e.source_node in _valid_ids(ir, e.source_sig), e
        assert e.target_node in _valid_ids(ir, e.target_sig), e


def test_param_in_arity_matches_callee_formals(ir):
    for e in ir.sdg_edges:
        if e.type != "PARAM_IN":
            continue
        callee = ir.functions[e.target_sig]
        formal = next(p for p in callee.param_nodes if p.id == e.target_node)
        assert formal.kind == "formal_in"
        assert formal.var == e.var


def test_param_out_sources_are_formal_outs(ir):
    for e in ir.sdg_edges:
        if e.type != "PARAM_OUT":
            continue
        callee = ir.functions[e.source_sig]
        formal = next(p for p in callee.param_nodes if p.id == e.source_node)
        assert formal.kind == "formal_out"


def test_call_edges_target_callee_entry(ir):
    calls = [e for e in ir.sdg_edges if e.type == "CALL"]
    assert calls, "no CALL edges assembled"
    for e in calls:
        assert e.target_node == 0  # ENTRY


def test_summary_edge_exists_for_the_transitive_chain_flow(ir):
    drive = _sig(ir, "drive")
    chain_a = _sig(ir, "chain_a")
    # drive's callsite r = chain_a(n): the callee's param:v → return flow
    # must surface as an actual_in → actual_out SUMMARY edge at the site.
    summaries = [
        e for e in ir.sdg_edges
        if e.type == "SUMMARY" and e.source_sig == drive and e.target_sig == drive
    ]
    assert summaries, "no SUMMARY edge at drive's chain_a callsite"
    # And chain_a itself summarizes its call to chain_b.
    assert any(
        e.type == "SUMMARY" and e.source_sig == chain_a for e in ir.sdg_edges
    )


def test_global_flow_is_stitched_across_files(ir):
    drive = _sig(ir, "drive")
    bump = _sig(ir, "bump")
    read_counter = _sig(ir, "read_counter")
    # bump's write formal flows out to drive's callsite...
    out_edges = [
        e for e in ir.sdg_edges
        if e.type == "PARAM_OUT" and e.source_sig == bump and e.target_sig == drive
        and (e.var or "").startswith(GLOBAL_PREFIX)
    ]
    assert out_edges, "bump's global write does not reach drive"
    # ...and read_counter's read formal is fed from drive's callsite.
    in_edges = [
        e for e in ir.sdg_edges
        if e.type == "PARAM_IN" and e.source_sig == drive and e.target_sig == read_counter
        and (e.var or "").startswith(GLOBAL_PREFIX)
    ]
    assert in_edges, "read_counter's global read is not bound at drive"


def test_closure_capture_binds_at_definition_site(ir):
    make_adder = _sig(ir, "make_adder")
    add = _sig(ir, "make_adder.add")
    edges = [
        e for e in ir.sdg_edges
        if e.type == "PARAM_IN" and e.source_sig == make_adder and e.target_sig == add
        and e.var == CAPTURE_PREFIX + "base"
    ]
    assert edges, "capture formal for `base` is not bound at the def site"


def test_mutation_flows_back_through_param_out(ir):
    caller = _sig(ir, "caller_of_mutate")
    mutate = _sig(ir, "mutate")
    edges = [
        e for e in ir.sdg_edges
        if e.type == "PARAM_OUT" and e.source_sig == mutate and e.target_sig == caller
        and e.var == "items"
    ]
    assert edges, "mutate's param mutation does not flow back to the caller"


def test_assembly_is_deterministic(fixture_app):
    a = build_program_graphs(fixture_app)
    b = build_program_graphs(fixture_app)
    assert a.sdg_edges == b.sdg_edges
    for sig in a.functions:
        assert [
            (p.id, p.kind, p.var) for p in a.functions[sig].param_nodes
        ] == [(p.id, p.kind, p.var) for p in b.functions[sig].param_nodes]
