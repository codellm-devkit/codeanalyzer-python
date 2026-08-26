"""Regression tests for #119 (Neo4j emission is always full-depth — enforced,
not just documented) and #118 (the msgpack output format is gone).
"""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeanalyzer.__main__ import app

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


# ----------------------------------------------------------------------------------------------
# #119 — --emit neo4j is always full-depth.
# ----------------------------------------------------------------------------------------------


def test_emit_neo4j_rejects_explicit_analysis_level(cli_runner, tiny_project, tmp_path):
    r = _invoke(
        cli_runner,
        "--input", str(tiny_project), "--output", str(tmp_path / "out"),
        "--no-venv", "--emit", "neo4j", "-a", "2",
    )
    assert r.exit_code != 0, "--emit neo4j with an explicit -a must be an error"


def test_emit_neo4j_rejects_explicit_graphs(cli_runner, tiny_project, tmp_path):
    r = _invoke(
        cli_runner,
        "--input", str(tiny_project), "--output", str(tmp_path / "out"),
        "--no-venv", "--emit", "neo4j", "--graphs", "cfg",
    )
    assert r.exit_code != 0, "--emit neo4j with an explicit --graphs must be an error"


def test_emit_neo4j_runs_full_depth_by_default(cli_runner, tiny_project, tmp_path):
    """Plain --emit neo4j must produce the FULL graph: the cypher snapshot has
    to contain the L3/L4 families (PyBodyNode rows, PY_CFG_NEXT/PY_DDG edges,
    param vertices), not just the L1 symbol table."""
    out = tmp_path / "out"
    r = _invoke(
        cli_runner,
        "--input", str(tiny_project), "--output", str(out),
        "--no-venv", "--emit", "neo4j",
    )
    assert r.exit_code == 0, f"plain --emit neo4j must succeed: {r.output}"
    cypher = (out / "graph.cypher").read_text()
    for marker in ("PyBodyNode", "PY_CFG_NEXT", "PY_DDG", "formal_in"):
        assert marker in cypher, (
            f"full-depth neo4j emission must contain {marker}; "
            "the graph appears to be shallow"
        )


# ----------------------------------------------------------------------------------------------
# #118 — msgpack is gone.
# ----------------------------------------------------------------------------------------------


def test_format_flag_is_gone(cli_runner, tiny_project, tmp_path):
    """#118 removed msgpack; with one format left the flag itself is gone —
    any --format spelling is an unknown-option error."""
    for value in ("json", "msgpack"):
        r = _invoke(
            cli_runner,
            "--input", str(tiny_project), "--output", str(tmp_path / "out"),
            "--no-venv", "--format", value,
        )
        assert r.exit_code != 0, f"--format {value} must be rejected"


def test_msgpack_absent_from_model_and_help(cli_runner):
    r = _invoke(cli_runner, "--help")
    assert "msgpack" not in r.output.lower()
    from codeanalyzer.schema.py_schema import PyModule

    assert not hasattr(PyModule, "to_msgpack_bytes"), (
        "the msgpack serialization mixin must be removed"
    )
