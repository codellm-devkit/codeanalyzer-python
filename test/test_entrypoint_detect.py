from pathlib import Path

from codeanalyzer.entrypoints.detect import detected_frameworks
from codeanalyzer.entrypoints.rules import load_rules
from codeanalyzer.schema.py_schema import PyApplication, PyImport, PyModule


def _app(*modules: str) -> PyApplication:
    return PyApplication(
        symbol_table={
            "a.py": PyModule(
                file_path="a.py",
                module_name="a",
                imports=[PyImport(module=m, name=m.split(".")[-1]) for m in modules],
            )
        }
    )


def test_framework_detected_from_an_import(tmp_path: Path):
    got = detected_frameworks(_app("flask"), tmp_path, load_rules())
    assert "flask" in got


def test_absent_framework_is_not_detected(tmp_path: Path):
    got = detected_frameworks(_app("os"), tmp_path, load_rules())
    assert "celery" not in got


def test_manifest_entry_alone_is_sufficient(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["celery>=5"]\n'
    )
    got = detected_frameworks(_app("os"), tmp_path, load_rules())
    assert "celery" in got
