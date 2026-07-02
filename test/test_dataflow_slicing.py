"""Stage-8 gate: the two-phase context-sensitive backward slice.

The client gate demands an *exact* hand-computed node set for a named
criterion — this is the assertion that catches both missing dependence edges
and context-insensitive over-reach.
"""

from pathlib import Path

import pytest

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.dataflow.builder import build_program_graphs
from codeanalyzer.dataflow.slicing import backward_slice
from codeanalyzer.options import AnalysisOptions

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "dataflow"


@pytest.fixture(scope="module")
def ir(tmp_path_factory):
    cache = tmp_path_factory.mktemp("dataflow-slice-cache")
    options = AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=cache
    )
    with Codeanalyzer(options) as analyzer:
        return build_program_graphs(analyzer.analyze())


def _sig(ir, suffix: str) -> str:
    matches = [s for s in ir.functions if s == suffix or s.endswith("." + suffix)]
    assert len(matches) == 1, f"suffix {suffix}: {matches}"
    return matches[0]


def _cfg_id(ir, sig: str, line: int) -> int:
    fg = ir.functions[sig]
    return next(
        n.id for n in fg.pdg.cfg.nodes if n.start_line == line and n.kind != "entry"
    )


def _param_id(ir, sig: str, kind: str, var: str, call_node=None) -> int:
    fg = ir.functions[sig]
    matches = [
        p.id
        for p in fg.param_nodes
        if p.kind == kind and p.var == var and (call_node is None or p.call_node == call_node)
    ]
    assert len(matches) == 1, f"{sig} {kind} {var}: {matches}"
    return matches[0]


def test_caller_of_mutate_slice_is_exactly_the_hand_computed_set(ir):
    caller = _sig(ir, "caller_of_mutate")
    mutate = _sig(ir, "mutate")
    criterion = _cfg_id(ir, caller, 61)  # return xs

    got = backward_slice(ir, caller, criterion)

    call_node = _cfg_id(ir, caller, 60)  # mutate(xs)
    expected = {
        # caller: ENTRY, xs = [], the callsite, the criterion,
        (caller, ir.functions[caller].pdg.cfg.entry_id),
        (caller, _cfg_id(ir, caller, 59)),
        (caller, call_node),
        (caller, criterion),
        # the module binding `mutate` read at the callsite,
        (caller, _param_id(ir, caller, "formal_in", "<global>:pipeline::mutate")),
        # the callsite's parameter structure,
        (caller, _param_id(ir, caller, "actual_in", "items", call_node)),
        (caller, _param_id(ir, caller, "actual_out", "<return>", call_node)),
        (caller, _param_id(ir, caller, "actual_out", "items", call_node)),
        # mutate (phase-2 descent): ENTRY, items.append(1), its formals.
        (mutate, ir.functions[mutate].pdg.cfg.entry_id),
        (mutate, _cfg_id(ir, mutate, 55)),
        (mutate, _param_id(ir, mutate, "formal_in", "items")),
        (mutate, _param_id(ir, mutate, "formal_out", "<return>")),
        (mutate, _param_id(ir, mutate, "formal_out", "items")),
    }
    assert got == expected


def test_global_slice_descends_into_the_writing_function(ir):
    read_counter = _sig(ir, "read_counter")
    bump = _sig(ir, "bump")
    criterion = _cfg_id(ir, read_counter, 12)  # return counter

    got = backward_slice(ir, read_counter, criterion)

    # The write `counter = counter + amount` (state.py line 8) must be in the
    # slice: read_counter ascends to drive's callsite, whose incoming global
    # def comes from bump's PARAM_OUT.
    assert (bump, _cfg_id(ir, bump, 8)) in got


def test_slice_does_not_reascend_into_unrelated_callers(ir):
    # Criterion inside chain_c: its slice ascends to chain_b/chain_a/drive,
    # but must not pull in unrelated functions like alias_flow or gen.
    chain_c = _sig(ir, "chain_c")
    criterion = _cfg_id(ir, chain_c, 13)  # return v - 3
    got = backward_slice(ir, chain_c, criterion)
    sigs = {s for s, _ in got}
    assert _sig(ir, "alias_flow") not in sigs
    assert _sig(ir, "looped") not in sigs


def test_unknown_signature_raises(ir):
    with pytest.raises(KeyError):
        backward_slice(ir, "no.such.function", 0)
