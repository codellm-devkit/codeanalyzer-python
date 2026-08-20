from pathlib import Path

from codeanalyzer.entrypoints.pipeline import detect_entrypoints
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
