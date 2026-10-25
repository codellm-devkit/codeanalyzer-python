"""``--workspace-root``: published file keys and the ids built from them are repo-relative.

Same semantics as codeanalyzer-java and codeanalyzer-typescript, so a language-neutral artifact id
names the same node whichever analyzer emitted it. The re-key is the published name only:
signatures, test-file detection, import resolution and the cache keep the ``--input``-relative key.
"""
import json
import re
import shutil
from pathlib import Path

from codeanalyzer.__main__ import app as cli
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions

FIXTURES = Path(__file__).parent / "fixtures"
# A `test` segment ABOVE --input: test-file skipping must judge the --input-relative path, or every
# file here would read as a test and vanish.
NESTED = "test/services/app"
PREFIX = NESTED + "/"


def _repo_with(tmp_path: Path, fixture: str) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    project = repo / NESTED
    shutil.copytree(FIXTURES / fixture, project)
    return repo, project


def _analyze(project: Path, cache: Path, workspace_root=None, level=1):
    return Codeanalyzer(AnalysisOptions(
        input=project, app_name="app", analysis_level=level, no_venv=True, cache_dir=cache,
        workspace_root=workspace_root,
    )).analyze()


def _code_ids(payload: str) -> list[str]:
    return re.findall(r'"(can://app/python/[^"]*)"', payload)


def test_symbol_table_keys_and_every_code_id_are_repo_relative_through_l4(tmp_path):
    repo, project = _repo_with(tmp_path, "single_functionalities/dataflow")
    plain = _analyze(project, tmp_path / "c1", level=4).application
    moved = _analyze(project, tmp_path / "c2", workspace_root=repo, level=4).application

    assert moved.id == "can://app"  # the app segment does not move
    assert sorted(moved.symbol_table) == sorted(PREFIX + k for k in plain.symbol_table)
    for key, mod in moved.symbol_table.items():
        assert not key.startswith("/") and ".." not in key
        assert mod.id == f"can://app/python/{key}"

    payload = moved.model_dump_json()
    ids = _code_ids(payload)
    assert ids, "precondition: code ids were emitted"
    for i in ids:
        assert i.startswith(f"can://app/python/{PREFIX}"), i
    assert len(moved.call_graph) == len(plain.call_graph)
    assert len(moved.param_in) == len(plain.param_in) > 0  # L4 overlays rode along


def test_signatures_stay_input_relative(tmp_path):
    repo, project = _repo_with(tmp_path, "single_functionalities/internal_imports")
    plain = _analyze(project, tmp_path / "c1", level=2).application
    moved = _analyze(project, tmp_path / "c2", workspace_root=repo, level=2).application

    def sigs(app):
        return sorted(f.signature for m in app.symbol_table.values() for f in (m.functions or {}).values())

    assert sigs(moved) == sigs(plain)


def test_resolved_module_follows_the_prefixed_keys():
    from codeanalyzer.core import _rekey_to_workspace
    from codeanalyzer.schema.py_schema import PyApplication, PyImport, PyModule

    imp = PyImport(module="pkg.util", name="pkg.util", resolved_module="pkg/util.py")
    app = PyApplication(symbol_table={
        "main.py": PyModule(file_path="/x/main.py", module_name="main", imports=[imp]),
        "pkg/util.py": PyModule(file_path="/x/pkg/util.py", module_name="pkg.util"),
    })
    _rekey_to_workspace(app, PREFIX)
    assert imp.resolved_module == PREFIX + "pkg/util.py"
    assert imp.resolved_module in app.symbol_table


def test_artifacts_are_prefixed_and_classification_is_unchanged(tmp_path):
    repo, project = _repo_with(tmp_path, "whole_applications/manifests_app")
    plain = _analyze(project, tmp_path / "c1").application
    moved = _analyze(project, tmp_path / "c2", workspace_root=repo).application

    assert sorted(moved.artifacts) == sorted(PREFIX + k for k in plain.artifacts)
    for key, art in plain.artifacts.items():
        m = moved.artifacts[PREFIX + key]
        assert m.path == PREFIX + key
        assert m.id == f"can://app/artifact/{PREFIX}{key}"
        assert (m.format, m.roles, m.extraction) == (art.format, art.roles, art.extraction)
        assert len(m.config_keys) == len(art.config_keys)
        assert all(ck.id.startswith(m.id) for ck in m.config_keys)
    assert len(moved.dependencies) == len(plain.dependencies) > 0
    for d in moved.dependencies:
        assert d.declared_in.startswith(f"can://app/artifact/{PREFIX}"), d.declared_in


def test_the_cache_stays_input_relative_so_toggling_reuses_it(tmp_path):
    repo, project = _repo_with(tmp_path, "single_functionalities/internal_imports")
    cache = tmp_path / "shared"
    plain = _analyze(project, cache).application
    cached = json.loads((cache / ".codeanalyzer" / "analysis_cache.json").read_text())["application"]["symbol_table"]
    moved = _analyze(project, cache, workspace_root=repo).application
    after = json.loads((cache / ".codeanalyzer" / "analysis_cache.json").read_text())["application"]["symbol_table"]

    assert sorted(after) == sorted(cached) == sorted(plain.symbol_table)  # no prefix written to the cache
    assert sorted(moved.symbol_table) == sorted(PREFIX + k for k in plain.symbol_table)
    again = _analyze(project, cache).application
    assert again.model_dump_json() == plain.model_dump_json()  # and back again, unchanged


def test_a_workspace_root_equal_to_input_changes_nothing(tmp_path):
    _, project = _repo_with(tmp_path, "whole_applications/manifests_app")
    a = _analyze(project, tmp_path / "c1").application
    b = _analyze(project, tmp_path / "c2", workspace_root=project).application
    assert b.model_dump_json() == a.model_dump_json()


def test_cli_flag_reaches_the_analysis_and_an_outside_input_is_an_error(cli_runner, tmp_path):
    repo, project = _repo_with(tmp_path, "single_functionalities/internal_imports")
    out = tmp_path / "out"
    ok = cli_runner.invoke(cli, ["-i", str(project), "-o", str(out), "--no-venv", "--app-name", "app",
                                 "-c", str(tmp_path / "c"), "--workspace-root", str(repo)])
    assert ok.exit_code == 0, ok.output
    keys = json.loads((out / "analysis.json").read_text())["application"]["symbol_table"]
    assert keys and all(k.startswith(PREFIX) for k in keys)

    bad_out = tmp_path / "bad"
    bad = cli_runner.invoke(cli, ["-i", str(project), "-o", str(bad_out), "--no-venv",
                                  "--workspace-root", str(tmp_path / "elsewhere")])
    assert bad.exit_code != 0
    assert not (bad_out / "analysis.json").exists()
