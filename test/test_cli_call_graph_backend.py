"""Tests for --call-graph (#148 escape hatch): 'jedi' must skip PyCG entirely,
the default must keep running it, and 'jedi' + --pycg-shard is a flag error.
"""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeanalyzer.__main__ import app
from codeanalyzer.core import Codeanalyzer

_ENV = {"NO_COLOR": "1", "TERM": "dumb"}


@pytest.fixture()
def cli_runner():
    return CliRunner()


@pytest.fixture()
def tiny_project(tmp_path):
    proj = tmp_path / "tiny"
    proj.mkdir()
    (proj / "m.py").write_text(
        "def g(x):\n    return x\n\n\ndef f(a):\n    b = g(a)\n    return b\n",
        encoding="utf-8",
    )
    return proj


def _invoke(cli_runner, *args):
    return cli_runner.invoke(app, list(args), env=_ENV)


def test_call_graph_jedi_rejects_pycg_shard(cli_runner, tiny_project, tmp_path):
    r = _invoke(
        cli_runner,
        "--input", str(tiny_project), "--output", str(tmp_path / "out"),
        "--no-venv", "-a", "2", "--call-graph", "jedi", "--pycg-shard",
    )
    assert r.exit_code != 0, "--call-graph jedi with --pycg-shard must be an error"


def test_call_graph_jedi_never_reaches_pycg(cli_runner, tiny_project, tmp_path, monkeypatch):
    def _boom(self, *a, **k):
        raise AssertionError("PyCG ran despite --call-graph jedi")

    monkeypatch.setattr(Codeanalyzer, "_get_pycg_call_graph", _boom)
    out = tmp_path / "out"
    r = _invoke(
        cli_runner,
        "--input", str(tiny_project), "--output", str(out),
        "--no-venv", "-a", "2", "--call-graph", "jedi",
    )
    assert r.exit_code == 0, r.output
    payload = json.loads((out / "analysis.json").read_text())
    edges = payload["application"]["call_graph"]
    assert edges, "Jedi should still resolve f -> g"
    assert all(e["prov"] == ["jedi"] for e in edges)


def test_default_backend_still_runs_pycg(cli_runner, tiny_project, tmp_path, monkeypatch):
    calls = []

    def _spy(self, symbol_table, jedi_edges):
        calls.append(True)
        return []

    monkeypatch.setattr(Codeanalyzer, "_get_pycg_call_graph", _spy)
    r = _invoke(
        cli_runner,
        "--input", str(tiny_project), "--output", str(tmp_path / "out"),
        "--no-venv", "-a", "2",
    )
    assert r.exit_code == 0, r.output
    assert calls, "default --call-graph must still invoke PyCG"


def test_level_1_ignores_backend_choice(cli_runner, tiny_project, tmp_path, monkeypatch):
    """-a 1 never runs PyCG anyway; --call-graph jedi must not change that or error."""
    def _boom(self, *a, **k):
        raise AssertionError("PyCG ran at level 1")

    monkeypatch.setattr(Codeanalyzer, "_get_pycg_call_graph", _boom)
    r = _invoke(
        cli_runner,
        "--input", str(tiny_project), "--output", str(tmp_path / "out"),
        "--no-venv", "-a", "1", "--call-graph", "jedi",
    )
    assert r.exit_code == 0, r.output
