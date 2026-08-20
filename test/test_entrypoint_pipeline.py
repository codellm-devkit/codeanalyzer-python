from pathlib import Path

import pytest

from codeanalyzer.entrypoints.pipeline import detect_entrypoints
from codeanalyzer.entrypoints.rules import RulesError
from codeanalyzer.schema.py_schema import PyApplication


def test_pass_is_a_noop_on_an_empty_application(tmp_path: Path):
    app = PyApplication(symbol_table={})
    detect_entrypoints(app, tmp_path)
    assert app.entrypoint_report.errors == []


def test_pass_never_raises_and_records_the_failure(tmp_path: Path, monkeypatch):
    """A finder crash must lose flags, not the analysis."""
    import codeanalyzer.entrypoints.pipeline as p

    def boom(*a, **k):
        raise RuntimeError("finder exploded")

    monkeypatch.setattr(p, "_run_stages", boom)
    app = PyApplication(symbol_table={})
    detect_entrypoints(app, tmp_path)          # must not raise
    assert any("finder exploded" in e for e in app.entrypoint_report.errors)


def test_derives_is_entrypoint_from_the_list(tmp_path: Path):
    from codeanalyzer.schema.py_schema import PyCallable, PyEntrypoint, PyModule

    fn = PyCallable(name="f", path="a.py", signature="a.f")
    fn.entrypoints.append(
        PyEntrypoint(framework="flask", confidence="certain", rule="flask.route", ruleset="shipped")
    )
    app = PyApplication(
        symbol_table={
            "a.py": PyModule(file_path="a.py", module_name="a", functions={"f": fn})
        }
    )
    detect_entrypoints(app, tmp_path)
    assert fn.is_entrypoint is True


def test_malformed_user_rules_file_raises_instead_of_being_swallowed(tmp_path: Path):
    """A bad --entrypoint-rules file is a CONFIGURATION error, not a detection
    failure: it must stop the run via RulesError, not land quietly in
    entrypoint_report.errors like a finder crash would."""
    bad_rules = tmp_path / "bad_rules.yml"
    bad_rules.write_text("frameworks: not-a-mapping\n")
    app = PyApplication(symbol_table={})

    with pytest.raises(RulesError):
        detect_entrypoints(app, tmp_path, rule_paths=(bad_rules,))

    assert app.entrypoint_report.errors == []
