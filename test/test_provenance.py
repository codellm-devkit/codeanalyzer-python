import subprocess
from pathlib import Path

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.provenance import analyzer_info, repository_info
from codeanalyzer.schema.py_schema import PyAnalyzerInfo, PyApplication


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def test_repository_info_for_a_git_checkout(tmp_path):
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "t@example.invalid")
    _run_git(tmp_path, "config", "user.name", "t")
    _run_git(tmp_path, "remote", "add", "origin", "https://example.invalid/repo.git")
    (tmp_path / "a.py").write_text("x = 1\n")
    _run_git(tmp_path, "add", "a.py")
    _run_git(tmp_path, "commit", "-m", "init")

    info = repository_info(tmp_path)

    assert info is not None
    assert info.uri == "https://example.invalid/repo.git"
    assert len(info.revision) == 40
    assert info.dirty is False

    (tmp_path / "a.py").write_text("x = 2\n")
    assert repository_info(tmp_path).dirty is True


def test_repository_info_outside_a_repo_is_none(tmp_path):
    assert repository_info(tmp_path) is None


def test_analyzer_info_carries_identity_and_config():
    info = analyzer_info(2)

    assert info.name == "codeanalyzer-python"
    assert info.version
    assert info.config == {"analysis_level": 2}


def test_app_node_carries_provenance_props():
    from codeanalyzer.neo4j.project import project
    from sample_graph_app import make_sample_app

    rows = project(make_sample_app(), "sample")
    app_row = next(n for n in rows.nodes if "PyApplication" in n.labels)
    assert app_row.props["source_revision"] == "deadbeef"
    assert app_row.props["analyzer_name"] == "codeanalyzer-python"
    assert app_row.props["repo_dirty"] is False


def test_repo_uri_credentials_are_stripped(tmp_path):
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "t@example.invalid")
    _run_git(tmp_path, "config", "user.name", "t")
    _run_git(tmp_path, "remote", "add", "origin", "https://ci-user:supersecret@example.invalid/org/repo.git")
    (tmp_path / "a.py").write_text("x = 1\n")
    _run_git(tmp_path, "add", "a.py")
    _run_git(tmp_path, "commit", "-m", "init")

    info = repository_info(tmp_path)

    assert info.uri == "https://example.invalid/org/repo.git"
    assert "supersecret" not in (info.uri or "")


def test_scp_style_remote_is_left_intact(tmp_path):
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "t@example.invalid")
    _run_git(tmp_path, "config", "user.name", "t")
    _run_git(tmp_path, "remote", "add", "origin", "git@example.invalid:org/repo.git")
    (tmp_path / "a.py").write_text("x = 1\n")
    _run_git(tmp_path, "add", "a.py")
    _run_git(tmp_path, "commit", "-m", "init")

    assert repository_info(tmp_path).uri == "git@example.invalid:org/repo.git"


def test_untracked_files_do_not_mark_the_checkout_dirty(tmp_path):
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "t@example.invalid")
    _run_git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
    _run_git(tmp_path, "add", "a.py")
    _run_git(tmp_path, "commit", "-m", "init")

    (tmp_path / "scratch.py").write_text("y = 2\n")

    info = repository_info(tmp_path)
    assert info.dirty is False


def test_cache_analyzer_matches_none_app_is_false():
    assert Codeanalyzer._cache_analyzer_matches(None, "0.3.1") is False


def test_cache_analyzer_matches_no_analyzer_is_false():
    app = PyApplication(symbol_table={})
    assert Codeanalyzer._cache_analyzer_matches(app, "0.3.1") is False


def test_cache_analyzer_matches_version_mismatch_is_false():
    app = PyApplication(symbol_table={})
    app.analyzer = PyAnalyzerInfo(name="codeanalyzer-python", version="0.3.0", config={})
    assert Codeanalyzer._cache_analyzer_matches(app, "0.3.1") is False


def test_cache_analyzer_matches_version_match_is_true():
    app = PyApplication(symbol_table={})
    app.analyzer = PyAnalyzerInfo(name="codeanalyzer-python", version="0.3.1", config={})
    assert Codeanalyzer._cache_analyzer_matches(app, "0.3.1") is True
