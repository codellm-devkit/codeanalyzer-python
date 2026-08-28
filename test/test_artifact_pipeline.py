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


def test_artifact_text_flags_defaults():
    opts = AnalysisOptions(input=Path("."))
    assert opts.artifact_text is True
    assert opts.artifact_text_max_bytes == 262144


def test_artifact_text_options_thread_through_core(tmp_path):
    """core.py must pass artifact_text/artifact_text_max_bytes to
    discover_artifacts -- verified end to end, not just at the discovery unit."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "notes.md").write_text("0123456789abcdefGHIJ")  # 21 bytes

    capped = Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=1, no_venv=True, cache_dir=tmp_path / "cache-capped",
        artifact_text_max_bytes=16,
    )).analyze().application
    art = capped.artifacts["notes.md"]
    assert art.text_truncated is True
    assert len(art.source.encode("utf-8")) <= 16

    no_text = Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=1, no_venv=True, cache_dir=tmp_path / "cache-no-text",
        artifact_text=False,
    )).analyze().application
    assert no_text.artifacts["notes.md"].source == ""
    assert no_text.artifacts["notes.md"].text_truncated is False


def test_config_keys_present_and_identical_across_levels(tmp_path):
    """Task 3: config-key extraction is wired into core.analyze() beside
    build_dependency_view -- L1 data, identical at every analysis level."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests"]\n\n[tool.demo]\nkey = "val"\n'
    )
    (proj / "app.py").write_text("import requests\n")
    a1 = _run(tmp_path, proj, 1)
    a2 = _run(tmp_path, proj, 2)
    d1 = json.loads(model_dump_json(a1))
    d2 = json.loads(model_dump_json(a2))
    keys1 = d1["artifacts"]["pyproject.toml"]["config_keys"]
    keys2 = d2["artifacts"]["pyproject.toml"]["config_keys"]
    assert keys1 == keys2
    assert keys1, "expected non-empty config_keys on pyproject.toml"
    assert {k["key"] for k in keys1} >= {"tool.demo.key"}


def test_config_key_eligibility_and_partial_on_parse_failure(tmp_path):
    """Namespace-eligibility: env-family by basename regardless of format
    (.env is format="text"), a non-eligible format never even attempts
    extraction (config_keys stays []), and a parse failure never drops the
    artifact -- it downgrades extraction to "partial" instead."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("SECRET=${TOKEN}\n")
    (proj / "notes.md").write_text("just docs, not config\n")
    (proj / "broken.toml").write_text("key = [unterminated\n")
    app = _run(tmp_path, proj, 1)

    env_keys = {k.key: k for k in app.artifacts[".env"].config_keys}
    assert env_keys["SECRET"].namespace == "env"
    assert env_keys["SECRET"].references == ["${TOKEN}"]

    assert app.artifacts["notes.md"].config_keys == []

    assert app.artifacts["broken.toml"].config_keys == []
    assert app.artifacts["broken.toml"].extraction == "partial"


def test_config_key_success_upgrades_none_to_full_on_non_manifest(tmp_path):
    """Review fix (HIGH): a clean config-key parse on a non-manifest artifact
    (extraction still "none" -- build_dependency_view never touched it)
    upgrades extraction to "full", not just left at "none"."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "values.yaml").write_text("replicas: 3\n")
    app = _run(tmp_path, proj, 1)
    art = app.artifacts["values.yaml"]
    assert art.extraction == "full"
    assert art.config_keys and art.config_keys[0].key == "replicas"


def test_config_key_success_does_not_clear_existing_partial(tmp_path):
    """Review fix (HIGH), other half: a Pipfile with `packages` written as a
    TOML array (not a table) breaks the Pipfile-specific dependency parser
    (AttributeError on `.items()` -> partial=True) but is still perfectly
    valid, flattenable TOML -- config-key extraction on the same text
    succeeds. That unrelated success must not clear the dependency parse's
    "partial" already recorded on the artifact."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "Pipfile").write_text('packages = ["requests"]\n')
    app = _run(tmp_path, proj, 1)
    art = app.artifacts["Pipfile"]
    assert art.extraction == "partial"  # from the broken dependency parse
    assert art.config_keys and art.config_keys[0].key == "packages.0"  # still extracted
