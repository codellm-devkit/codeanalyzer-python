"""Task 5: sections populated at every level, identically (L1-data posture)."""
import json
from pathlib import Path
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json


def _run(tmp_path, project, level):
    out = tmp_path / f"out{level}"
    opts = AnalysisOptions(
        input=project, output=out, analysis_level=level,
        no_venv=True, cache_dir=tmp_path / f"cache{level}",
    )
    artifacts = Codeanalyzer(opts).analyze()
    return artifacts.application


def _fixture(tmp_path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\ndependencies = ["requests"]\n')
    (proj / "Dockerfile").write_text("FROM python:3.12\n")
    (proj / "app.py").write_text("import requests\nimport colorama\n")
    return proj


def test_sections_identical_across_levels(tmp_path):
    proj = _fixture(tmp_path)
    a1 = _run(tmp_path, proj, 1)
    a2 = _run(tmp_path, proj, 2)
    d1 = json.loads(model_dump_json(a1))
    d2 = json.loads(model_dump_json(a2))
    for field in ("artifacts", "dependencies", "unresolved_imports"):
        assert d1[field] == d2[field]
    assert sorted(d1["artifacts"]) == ["Dockerfile", "pyproject.toml"]
    assert [d["name"] for d in d1["dependencies"]] == ["requests"]
    assert [u["module"] for u in d1["unresolved_imports"]] == ["colorama"]


def test_resolve_installed_flag_default_off():
    assert AnalysisOptions(input=Path(".")).resolve_installed is False
