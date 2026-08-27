"""Task 4: declared records, lock backfill, provides_imports, unresolved imports."""
from pathlib import Path
from codeanalyzer.artifacts.discovery import discover_artifacts
from codeanalyzer.artifacts.dependencies import build_dependency_view
from codeanalyzer.schema.py_schema import PyImport, PyModule


def _module(name, imports):
    return PyModule.builder().file_path(f"/tmp/{name}.py").module_name(name).imports(
        [PyImport(module=m, name="*") for m in imports]
    ).build()


def _setup(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests>=2.31", "PyYAML"]\n'
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.32.3"\n'
    )
    arts = discover_artifacts(tmp_path, "app")
    mods = {
        "app.py": _module("app", ["requests", "yaml", "colorama", "os", "app.util"]),
        "app/util.py": _module("app.util", []),
    }
    return arts, mods


def test_records_lock_and_binding(tmp_path):
    arts, mods = _setup(tmp_path)
    deps, unresolved = build_dependency_view(arts, mods, tmp_path, None, False)
    by = {d.name: d for d in deps}
    assert by["requests"].prov == ["declared", "lockfile"]
    assert by["requests"].locked_version == "2.32.3"
    assert by["requests"].declared_in == "can://artifact/app/pyproject.toml"
    # pyyaml's prov gains "heuristic" via the alias-table binding below (same
    # cause as the dropped provides_imports==[] assert); see test_known_alias_binding.
    assert by["pyyaml"].locked_version is None
    # provides_imports: requests trivially; pyyaml binds via the alias table
    # (see test_known_alias_binding — yaml->pyyaml, not a same-name match).
    assert by["requests"].provides_imports == ["requests"]
    assert arts["pyproject.toml"].extraction == "full"


def test_unresolved_imports(tmp_path):
    arts, mods = _setup(tmp_path)
    _, unresolved = build_dependency_view(arts, mods, tmp_path, None, False)
    u = {b.module: b for b in unresolved}
    # yaml: known-alias heuristic binds it to declared pyyaml -> NOT unresolved
    # colorama: imported, never declared -> unresolved, unbound
    # os: stdlib; app.util: local module -> neither appears
    assert set(u) == {"colorama"}
    assert u["colorama"].bound_to is None and u["colorama"].prov == []


def test_known_alias_binding(tmp_path):
    arts, mods = _setup(tmp_path)
    deps, unresolved = build_dependency_view(arts, mods, tmp_path, None, False)
    yaml_dep = next(d for d in deps if d.name == "pyyaml")
    assert "yaml" in yaml_dep.provides_imports and "heuristic" in yaml_dep.prov


def test_installed_metadata_binding(tmp_path):
    arts, mods = _setup(tmp_path)
    venv = tmp_path / ".venv"
    di = venv / "lib" / "python3.12" / "site-packages" / "PyYAML-6.0.1.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: PyYAML\nVersion: 6.0.1\n")
    (di / "top_level.txt").write_text("yaml\n_yaml\n")
    deps, _ = build_dependency_view(arts, mods, tmp_path, venv, True)
    yaml_dep = next(d for d in deps if d.name == "pyyaml")
    assert "yaml" in yaml_dep.provides_imports
    assert "installed-metadata" in yaml_dep.prov


def test_requirement_refs_chased(tmp_path):
    (tmp_path / "reqs").mkdir()
    (tmp_path / "reqs" / "requirements.txt").write_text(
        "-r base.txt\n-r ../../etc/passwd\nrequests\n"
    )
    (tmp_path / "reqs" / "base.txt").write_text("flask>=2\n")
    arts = discover_artifacts(tmp_path, "app")
    assert "reqs/base.txt" not in arts  # unmatched by discovery rules
    deps, _ = build_dependency_view(arts, {}, tmp_path, None, False)
    manifest_id = arts["reqs/requirements.txt"].id
    by = {d.name: d for d in deps}
    assert set(by) == {"requests", "flask"}  # ../../etc/passwd ref ignored
    assert by["flask"].declared_in == manifest_id
    assert by["requests"].declared_in == manifest_id
