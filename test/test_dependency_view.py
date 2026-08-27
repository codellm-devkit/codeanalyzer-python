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


def test_requirement_ref_chased_kind_from_real_basename(tmp_path):
    (tmp_path / "reqs").mkdir()
    (tmp_path / "reqs" / "requirements.txt").write_text("-r dev.txt\n")
    (tmp_path / "reqs" / "dev.txt").write_text("mypy\n")
    arts = discover_artifacts(tmp_path, "app")
    assert "reqs/dev.txt" not in arts  # unmatched by discovery rules
    deps, _ = build_dependency_view(arts, {}, tmp_path, None, False)
    mypy = next(d for d in deps if d.name == "mypy")
    assert mypy.kind == "dev"  # from dev.txt's real basename, not the forced "requirements.txt"


def test_requirement_ref_to_discovered_target_not_duplicated(tmp_path):
    (tmp_path / "requirements.txt").write_text("-r requirements-extra.txt\n")
    (tmp_path / "requirements-extra.txt").write_text("rich\n")
    arts = discover_artifacts(tmp_path, "app")
    assert "requirements-extra.txt" in arts  # discovered and parsed on its own
    deps, _ = build_dependency_view(arts, {}, tmp_path, None, False)
    rich_deps = [d for d in deps if d.name == "rich"]
    assert len(rich_deps) == 1  # not duplicated by the chase
    assert rich_deps[0].declared_in == arts["requirements-extra.txt"].id


def test_resolve_ref_does_not_over_reject_dotdot_prefixed_name(tmp_path):
    (tmp_path / "requirements.txt").write_text("-r ..bak.txt\n")
    (tmp_path / "..bak.txt").write_text("click\n")
    arts = discover_artifacts(tmp_path, "app")
    deps, _ = build_dependency_view(arts, {}, tmp_path, None, False)
    assert {d.name for d in deps} == {"click"}  # "..bak.txt" != escaping ".."/"../..."


def test_dotted_alias_does_not_falsely_unresolve_top_level(tmp_path):
    """Regression: protobuf's alias table entry maps to the dotted
    "google.protobuf". provides_imports keeps that full dotted string, but
    the unresolved check must compare TOP-LEVEL segments -- else "google"
    falsely resurfaces as unresolved even though protobuf declares it."""
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["protobuf"]\n')
    arts = discover_artifacts(tmp_path, "app")
    mods = {"app.py": _module("app", ["google.protobuf"])}
    deps, unresolved = build_dependency_view(arts, mods, tmp_path, None, False)
    assert "google" not in {u.module for u in unresolved}
    protobuf_dep = next(d for d in deps if d.name == "protobuf")
    assert "google.protobuf" in protobuf_dep.provides_imports


def test_same_name_match_carries_no_heuristic_prov(tmp_path):
    """Regression: identity entries ("setuptools": "setuptools", "pymongo":
    "pymongo") in the alias table minted a spurious "heuristic" prov on a
    plain same-name match. A same-name match must be prov == ["declared"]."""
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["setuptools"]\n')
    arts = discover_artifacts(tmp_path, "app")
    mods = {"app.py": _module("app", ["setuptools"])}
    deps, _ = build_dependency_view(arts, mods, tmp_path, None, False)
    setuptools_dep = next(d for d in deps if d.name == "setuptools")
    assert setuptools_dep.prov == ["declared"]
    assert setuptools_dep.provides_imports == ["setuptools"]


def test_local_package_top_level_not_falsely_unresolved(tmp_path):
    """Regression (confirmed on odoo-slim): module_name is py_file.stem --
    the leaf filename only ("api" for "odoo/api.py") -- so a local set built
    from module_name alone never contains the top-level PACKAGE name itself.
    A sibling module doing `import odoo` then falsely lands in
    unresolved_imports. Fix: also derive local tops from the symbol-table
    keys (first path segment when nested)."""
    mods = {
        "odoo/api.py": _module("api", []),
        "pkg/main.py": _module("main", ["odoo"]),
    }
    _, unresolved = build_dependency_view({}, mods, tmp_path, None, False)
    assert "odoo" not in {u.module for u in unresolved}
