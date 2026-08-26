"""Regression tests for #107: environment provisioning must prefer an
interpreter the installed jedi/parso stack can actually parse, and a run
where every module fails must not stay silent.

parso ships one hardcoded grammar file per Python minor (grammar313.txt,
grammar314.txt, ...). If the provisioned analysis venv is newer than the
newest shipped grammar, jedi rejects every file and the symbol table comes
back empty while the process still exits 0.
"""
import logging
from pathlib import Path

import pytest

from codeanalyzer.core import Codeanalyzer


# ----------------------------------------------------------------------------------------------
# The parso ceiling: derived from the shipped grammar files at runtime, never hardcoded.
# ----------------------------------------------------------------------------------------------


def test_grammar_stems_parse_to_versions():
    got = Codeanalyzer._versions_from_grammar_stems(
        ["grammar36", "grammar39", "grammar310", "grammar313", "grammar314"]
    )
    assert got == [(3, 6), (3, 9), (3, 10), (3, 13), (3, 14)]


def test_malformed_grammar_stems_are_ignored():
    got = Codeanalyzer._versions_from_grammar_stems(
        ["grammar", "grammarXY", "grammar3", "grammar312"]
    )
    assert got == [(3, 12)]


def test_parso_ceiling_reflects_installed_parso():
    """The ceiling must be the max of the grammars parso actually ships —
    on any env with parso >= 0.8.5 that is at least (3, 13)."""
    ceiling = Codeanalyzer._parso_supported_ceiling()
    assert ceiling is not None
    assert ceiling >= (3, 13)


# ----------------------------------------------------------------------------------------------
# Interpreter choice honors the ceiling.
# ----------------------------------------------------------------------------------------------


def test_pick_supported_interpreter_prefers_newest_within_ceiling():
    candidates = [
        (Path("/opt/py315"), (3, 15)),
        (Path("/opt/py313"), (3, 13)),
        (Path("/opt/py311"), (3, 11)),
    ]
    assert Codeanalyzer._pick_supported_interpreter(candidates, (3, 14)) == Path(
        "/opt/py313"
    )
    assert Codeanalyzer._pick_supported_interpreter(candidates, (3, 10)) is None


def test_base_interpreter_swaps_out_unsupported_default(monkeypatch):
    """If the default interpreter is newer than parso's ceiling, provisioning
    must pick a supported one instead."""
    fake_default = Path("/opt/py399/bin/python3")
    fake_supported = Path("/opt/py313/bin/python3")

    monkeypatch.setattr(
        Codeanalyzer, "_default_base_interpreter", staticmethod(lambda: fake_default)
    )
    monkeypatch.setattr(
        Codeanalyzer, "_parso_supported_ceiling", staticmethod(lambda: (3, 13))
    )
    monkeypatch.setattr(
        Codeanalyzer,
        "_interpreter_version",
        staticmethod(lambda p: (3, 99) if p == fake_default else (3, 13)),
    )
    monkeypatch.setattr(
        Codeanalyzer,
        "_find_supported_interpreter",
        staticmethod(lambda ceiling: fake_supported),
    )
    assert Codeanalyzer._get_base_interpreter() == fake_supported


def test_base_interpreter_keeps_supported_default(monkeypatch):
    fake_default = Path("/opt/py312/bin/python3")
    monkeypatch.setattr(
        Codeanalyzer, "_default_base_interpreter", staticmethod(lambda: fake_default)
    )
    monkeypatch.setattr(
        Codeanalyzer, "_parso_supported_ceiling", staticmethod(lambda: (3, 13))
    )
    monkeypatch.setattr(
        Codeanalyzer, "_interpreter_version", staticmethod(lambda p: (3, 12))
    )
    assert Codeanalyzer._get_base_interpreter() == fake_default


def test_base_interpreter_falls_back_loudly_when_nothing_supported(monkeypatch, caplog):
    fake_default = Path("/opt/py399/bin/python3")
    monkeypatch.setattr(
        Codeanalyzer, "_default_base_interpreter", staticmethod(lambda: fake_default)
    )
    monkeypatch.setattr(
        Codeanalyzer, "_parso_supported_ceiling", staticmethod(lambda: (3, 13))
    )
    monkeypatch.setattr(
        Codeanalyzer, "_interpreter_version", staticmethod(lambda p: (3, 99))
    )
    monkeypatch.setattr(
        Codeanalyzer, "_find_supported_interpreter", staticmethod(lambda ceiling: None)
    )
    # the codeanalyzer logger sets propagate=False (rich handler); caplog needs
    # propagation to observe records
    monkeypatch.setattr(logging.getLogger("codeanalyzer"), "propagate", True)
    with caplog.at_level(logging.WARNING, logger="codeanalyzer"):
        assert Codeanalyzer._get_base_interpreter() == fake_default
    assert any("parso" in r.getMessage() for r in caplog.records)


# ----------------------------------------------------------------------------------------------
# A run where every module failed must be loud, not silently empty.
# ----------------------------------------------------------------------------------------------


def test_all_files_failing_emits_an_error(tmp_path, monkeypatch, caplog):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (proj / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")

    from codeanalyzer.options.options import AnalysisOptions
    from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder

    def boom(self, py_file):
        raise RuntimeError("Python version 3.99 is currently not supported.")

    monkeypatch.setattr(SymbolTableBuilder, "build_pymodule_from_file", boom)

    opts = AnalysisOptions(
        input=proj, output=None,
        skip_tests=True, no_venv=True, cache_dir=tmp_path / "cache",
        rebuild_analysis=True,
    )
    analyzer = Codeanalyzer(opts)
    monkeypatch.setattr(logging.getLogger("codeanalyzer"), "propagate", True)
    with caplog.at_level(logging.ERROR, logger="codeanalyzer"):
        table = analyzer._build_symbol_table(cached_symbol_table={})
    assert table == {}
    assert any(
        "every" in r.getMessage().lower() or "all " in r.getMessage().lower()
        for r in caplog.records
    ), "an empty symbol table from total per-file failure must be reported loudly"
