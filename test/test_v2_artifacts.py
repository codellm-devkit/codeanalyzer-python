"""Repository-artifact layer end-to-end tests.

Drives the real CLI over a fixture project and asserts the ``artifacts`` branch:
classification, dependency scopes/version specs, config keys + placeholder
references, binary/over-cap text handling, conformance (no null leaves), the
ungated all-levels invariant (byte-identical across ``-a 1|2|3|4``), and the two
CLI knobs (``--no-artifact-text`` / ``--artifact-text-max-bytes``).
"""
import json
import subprocess
import sys
from pathlib import Path

from conftest_v2 import assert_conformant


def _run(proj, level=1, *extra):
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "codeanalyzer",
            "-i",
            str(proj),
            "-a",
            str(level),
            "--no-venv",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def _make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (proj / "pyproject.toml").write_text(
        "[project]\n"
        'name = "demo"\n'
        'dependencies = ["requests>=2.0", "click"]\n'
        "\n"
        "[project.optional-dependencies]\n"
        'dev = ["pytest>=7"]\n'
        "\n"
        "[build-system]\n"
        'requires = ["setuptools"]\n',
        encoding="utf-8",
    )
    (proj / "requirements-dev.txt").write_text("black==24.1.0\n", encoding="utf-8")
    # A poetry.lock owned by the pyproject.toml above: pins a declared dep's
    # resolved_version and contributes a lock-only transitive (direct=False).
    (proj / "poetry.lock").write_text(
        "[[package]]\n"
        'name = "requests"\n'
        'version = "2.31.0"\n'
        "\n"
        "[[package]]\n"
        'name = "urllib3"\n'
        'version = "2.2.1"\n',
        encoding="utf-8",
    )
    (proj / "Pipfile").write_text(
        "[packages]\n" 'flask = "*"\n' "\n" "[dev-packages]\n" 'mypy = "==1.8.0"\n',
        encoding="utf-8",
    )
    (proj / "setup.cfg").write_text(
        "[options]\n"
        "install_requires =\n"
        "    numpy>=1.26\n"
        "\n"
        "[options.extras_require]\n"
        "test =\n"
        "    pytest-cov\n",
        encoding="utf-8",
    )
    (proj / ".env").write_text("DB_URL=${DB_HOST}\nDEBUG=1\n", encoding="utf-8")
    (proj / "settings.yml").write_text(
        "service:\n  name: demo\n  port: 8080\n", encoding="utf-8"
    )
    (proj / "settings.json").write_text(
        '{"cache": {"ttl": 60}, "endpoint": "${API_URL}"}\n', encoding="utf-8"
    )
    (proj / "config.toml").write_text(
        '[server]\nhost = "0.0.0.0"\nport = 9000\n', encoding="utf-8"
    )
    (proj / "tox.ini").write_text("[testenv]\ndeps = pytest\n", encoding="utf-8")
    (proj / "app.properties").write_text("log.level=INFO\n", encoding="utf-8")
    # A binary file (invalid utf-8) — inventoried, no text.
    (proj / "logo.bin").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    # An over-cap text file (> the tiny cap we pass in one test).
    (proj / "big.txt").write_text("x" * 5000, encoding="utf-8")
    return proj


def _artifacts(app):
    return app["application"]["artifacts"]


def test_inventory_classification_and_content(tmp_path):
    proj = _make_project(tmp_path)
    app = _run(proj, 1)
    assert_conformant(app, max_level=1)
    arts = _artifacts(app)

    # Every non-source file is inventoried; app.py stays in the symbol table.
    assert "app.py" not in arts
    for expected in (
        "pyproject.toml",
        ".env",
        "settings.yml",
        "app.properties",
        "logo.bin",
        "big.txt",
        "requirements-dev.txt",
    ):
        assert expected in arts, f"{expected} missing from inventory"

    assert arts["pyproject.toml"]["artifact_kind"] == "build_manifest"
    assert arts[".env"]["artifact_kind"] == "configuration"
    assert arts["settings.yml"]["artifact_kind"] == "configuration"
    assert arts["settings.yml"]["format"] == "yaml"

    # Hash + size are always present; ids carry the @artifact/ marker verbatim
    # (the dotfile keeps its leading dot).
    assert arts[".env"]["id"].endswith("/@artifact/.env")
    assert len(arts["pyproject.toml"]["content_hash"]) == 64
    assert arts["pyproject.toml"]["size_bytes"] > 0


def test_dependency_scopes_and_specs(tmp_path):
    proj = _make_project(tmp_path)
    arts = _artifacts(_run(proj, 1))

    deps = arts["pyproject.toml"]["dependencies"]
    assert deps["requests"]["version_spec"] == ">=2.0"
    assert deps["requests"]["scope"] == "runtime"
    assert deps["requests"]["ecosystem"] == "pypi"
    assert deps["requests"]["direct"] is True
    assert deps["click"]["scope"] == "runtime"
    assert deps["pytest"]["scope"] == "development"
    assert deps["setuptools"]["scope"] == "build"

    dev = arts["requirements-dev.txt"]["dependencies"]
    assert dev["black"]["scope"] == "development"
    assert dev["black"]["version_spec"] == "==24.1.0"


def test_lockfile_pins_resolved_version_and_adds_transitives(tmp_path):
    """poetry.lock owned by pyproject.toml pins a declared dep's resolved_version
    and contributes lock-only packages as direct=False transitives."""
    proj = _make_project(tmp_path)
    deps = _artifacts(_run(proj, 1))["pyproject.toml"]["dependencies"]

    # A declared dep gets its resolved_version filled in from the lock.
    assert deps["requests"]["resolved_version"] == "2.31.0"
    assert deps["requests"]["direct"] is True

    # A lock-only package (not declared in the manifest) is a transitive node.
    assert deps["urllib3"]["resolved_version"] == "2.2.1"
    assert deps["urllib3"]["direct"] is False
    assert deps["urllib3"]["scope"] == "unknown"


def test_pipfile_and_setup_cfg_dependency_scopes(tmp_path):
    proj = _make_project(tmp_path)
    arts = _artifacts(_run(proj, 1))

    pip = arts["Pipfile"]["dependencies"]
    assert pip["flask"]["scope"] == "runtime"
    assert pip["mypy"]["scope"] == "development"
    assert pip["mypy"]["version_spec"] == "==1.8.0"

    cfg = arts["setup.cfg"]["dependencies"]
    assert cfg["numpy"]["scope"] == "runtime"
    assert cfg["numpy"]["version_spec"] == ">=1.26"
    assert cfg["pytest-cov"]["scope"] == "test"


def test_config_keys_across_structured_formats(tmp_path):
    """JSON / TOML / INI config-key extraction (the .env/.yaml/.properties cases
    are covered separately)."""
    proj = _make_project(tmp_path)
    arts = _artifacts(_run(proj, 1))

    json_keys = arts["settings.json"]["config_keys"]
    assert json_keys["cache.ttl"]["value"] == 60
    assert json_keys["endpoint"]["references"] == ["env:API_URL"]

    toml_keys = arts["config.toml"]["config_keys"]
    assert toml_keys["server.host"]["value"] == "0.0.0.0"
    assert toml_keys["server.port"]["value"] == 9000

    # A plain .ini file flattens to <section>.<option> keys.
    ini_keys = arts["tox.ini"]["config_keys"]
    assert ini_keys["testenv.deps"]["value"] == "pytest"


def test_config_keys_and_references(tmp_path):
    proj = _make_project(tmp_path)
    arts = _artifacts(_run(proj, 1))

    env_keys = arts[".env"]["config_keys"]
    assert env_keys["DB_URL"]["namespace"] == "env"
    assert env_keys["DB_URL"]["references"] == ["env:DB_HOST"]
    assert env_keys["DEBUG"]["value"] == "1"

    # Nested YAML flattens into dotted keys.
    yml_keys = arts["settings.yml"]["config_keys"]
    assert "service.name" in yml_keys
    assert yml_keys["service.port"]["value"] == 8080

    assert arts["app.properties"]["config_keys"]["log.level"]["value"] == "INFO"


def test_binary_inventoried_without_text(tmp_path):
    proj = _make_project(tmp_path)
    logo = _artifacts(_run(proj, 1))["logo.bin"]
    assert "text" not in logo  # exclude_none drops the null text leaf
    assert logo["content_hash"] and logo["size_bytes"] > 0


def test_over_cap_text_is_truncated(tmp_path):
    proj = _make_project(tmp_path)
    # big.txt is 5000 bytes; cap at 1000.
    arts = _artifacts(_run(proj, 1, "--artifact-text-max-bytes", "1000"))
    big = arts["big.txt"]
    assert big["text_truncated"] is True
    assert len(big["text"]) == 1000
    # A small file under the cap is not flagged.
    assert arts["app.properties"].get("text_truncated", False) is False


def test_no_artifact_text_drops_only_text(tmp_path):
    proj = _make_project(tmp_path)
    full = _artifacts(_run(proj, 1))
    lean = _artifacts(_run(proj, 1, "--no-artifact-text"))

    assert set(full) == set(lean), "inventory changed when text was disabled"
    for rel, node in lean.items():
        # Text payload gone ...
        assert "text" not in node
        assert "text_encoding" not in node
        # ... but structure (hash, size, kind, deps, config keys) preserved.
        assert node["content_hash"] == full[rel]["content_hash"]
        assert node["size_bytes"] == full[rel]["size_bytes"]
        assert node["artifact_kind"] == full[rel]["artifact_kind"]
        assert (
            node.get("dependencies", {}).keys()
            == full[rel].get("dependencies", {}).keys()
        )
        assert (
            node.get("config_keys", {}).keys()
            == full[rel].get("config_keys", {}).keys()
        )


def test_artifacts_identical_across_all_levels(tmp_path):
    """Ungated / application-anchored: the artifacts branch must be byte-identical
    at every -a level, so the monotonicity gate has nothing to refine."""
    proj = _make_project(tmp_path)
    branches = [
        json.dumps(_artifacts(_run(proj, lvl)), sort_keys=True) for lvl in (1, 2, 3, 4)
    ]
    assert branches[0], "empty artifacts branch"
    assert len(set(branches)) == 1, "artifacts branch differs across -a levels"
