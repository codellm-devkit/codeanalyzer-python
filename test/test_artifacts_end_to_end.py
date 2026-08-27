"""Task 7: full pipeline over the manifests_app fixture + determinism."""
import json
from pathlib import Path
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json

FIXTURE = Path(__file__).parent / "fixtures" / "whole_applications" / "manifests_app"


def _app(tmp_path, tag):
    return Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=tmp_path / tag,
    )).analyze().application


def test_full_surface(tmp_path):
    app = _app(tmp_path, "a")
    arts = app.artifacts
    assert {"pyproject.toml", "requirements-dev.txt", "setup.py", "uv.lock",
            "environment.yml", "Dockerfile", "docker-compose.yml",
            ".github/workflows/ci.yml"} <= set(arts)
    assert arts["setup.py"].extraction == "partial"      # computed install_requires
    assert arts["pyproject.toml"].extraction == "full"
    deps = {d.name: d for d in app.dependencies}
    assert deps["requests"].locked_version == "2.32.3"
    assert deps["requests"].prov == ["declared", "lockfile"]
    assert deps["pytest"].kind == "dev"
    assert deps["numpy"].spec == "=1.26"
    unresolved = sorted([u.module for u in app.unresolved_imports])
    assert "colorama" in unresolved


def test_determinism_two_runs(tmp_path):
    a = model_dump_json(_app(tmp_path, "r1"))
    b = model_dump_json(_app(tmp_path, "r2"))
    assert a == b


def test_level_invariance_artifacts_and_deps(tmp_path):
    """Artifacts, dependencies, and unresolved_imports must be identical at L1 and L4."""
    app_l1 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=tmp_path / "l1",
    )).analyze().application
    app_l4 = Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=4, no_venv=True, cache_dir=tmp_path / "l4",
    )).analyze().application

    # artifacts section
    assert set(app_l1.artifacts.keys()) == set(app_l4.artifacts.keys())
    for name in app_l1.artifacts:
        assert app_l1.artifacts[name] == app_l4.artifacts[name]

    # dependencies section
    deps_l1 = {d.name: d for d in app_l1.dependencies}
    deps_l4 = {d.name: d for d in app_l4.dependencies}
    assert set(deps_l1.keys()) == set(deps_l4.keys())
    for name in deps_l1:
        assert deps_l1[name] == deps_l4[name]

    # unresolved_imports section
    unres_l1 = sorted([u.module for u in app_l1.unresolved_imports])
    unres_l4 = sorted([u.module for u in app_l4.unresolved_imports])
    assert unres_l1 == unres_l4
