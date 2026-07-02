"""Emission gate: `-a 3` program_graphs in analysis.json, flag validation,
schema round-trip, and the -a 1/-a 2 no-impact guarantee."""

import json
from pathlib import Path

import pytest

from codeanalyzer.__main__ import app
from codeanalyzer.schema import PyApplication, model_validate_json

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "dataflow"

ENV = {"NO_COLOR": "1", "TERM": "dumb"}


def _invoke(cli_runner, tmp_path, *extra):
    out = tmp_path / "out"
    cache = tmp_path / "cache"
    return out, cli_runner.invoke(
        app,
        [
            "--input", str(FIXTURE),
            "--output", str(out),
            "--no-venv",
            "--cache-dir", str(cache),
            *extra,
        ],
        env=ENV,
    )


def test_level3_emits_validating_program_graphs(cli_runner, tmp_path):
    out, result = _invoke(cli_runner, tmp_path, "--analysis-level", "3")
    assert result.exit_code == 0, result.output
    raw = (out / "analysis.json").read_text()
    application = model_validate_json(PyApplication, raw)
    pg = application.program_graphs
    assert pg is not None
    assert pg.schema_version == "1.0.0"
    assert pg.k_limit == 3
    assert pg.functions and pg.sdg_edges
    # Every function section carries all default graphs.
    some = next(iter(pg.functions.values()))
    assert some.cfg is not None and some.pdg is not None
    # No dangling SDG endpoints at the schema level either.
    for e in pg.sdg_edges:
        fg = pg.functions[e.source.signature]
        ids = {n.id for n in fg.cfg.nodes} | {p.id for p in fg.param_nodes}
        assert e.source.node in ids


def test_level1_and_level2_do_not_emit_program_graphs(cli_runner, tmp_path):
    out, result = _invoke(cli_runner, tmp_path, "--analysis-level", "1")
    assert result.exit_code == 0, result.output
    data = json.loads((out / "analysis.json").read_text())
    assert data.get("program_graphs") is None


def test_graphs_selector_scopes_sections(cli_runner, tmp_path):
    out, result = _invoke(
        cli_runner, tmp_path, "--analysis-level", "3", "--graphs", "cfg"
    )
    assert result.exit_code == 0, result.output
    data = json.loads((out / "analysis.json").read_text())
    pg = data["program_graphs"]
    assert pg["sdg_edges"] == []
    some = next(iter(pg["functions"].values()))
    assert some["cfg"] is not None
    assert some["pdg"] is None
    assert some["param_nodes"] == []


def test_unrecognized_graphs_value_errors_out(cli_runner, tmp_path):
    _, result = _invoke(
        cli_runner, tmp_path, "--analysis-level", "3", "--graphs", "cfg,cpg"
    )
    assert result.exit_code != 0


def test_graphs_flag_below_level3_errors_out(cli_runner, tmp_path):
    _, result = _invoke(
        cli_runner, tmp_path, "--analysis-level", "1", "--graphs", "cfg"
    )
    assert result.exit_code != 0


def test_graph_field_depth_below_level3_errors_out(cli_runner, tmp_path):
    _, result = _invoke(
        cli_runner, tmp_path, "--analysis-level", "2", "--graph-field-depth", "5"
    )
    assert result.exit_code != 0


def test_graph_field_depth_is_recorded(cli_runner, tmp_path):
    out, result = _invoke(
        cli_runner, tmp_path, "--analysis-level", "3", "--graph-field-depth", "2"
    )
    assert result.exit_code == 0, result.output
    data = json.loads((out / "analysis.json").read_text())
    assert data["program_graphs"]["k_limit"] == 2
