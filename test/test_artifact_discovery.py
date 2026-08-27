"""Task 2: every file becomes a PyArtifact node except `.py` files and ignored
dirs (never-drop inventory, issue #157 follow-up). Rule-matched files keep
their format/roles; unmatched files fall back to text/unknown (or binary)."""
import hashlib
from pathlib import Path
from codeanalyzer.artifacts.discovery import discover_artifacts


def _mk(tmp_path: Path, rel: str, text: str = "x: 1\n") -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_discovers_known_shapes(tmp_path):
    _mk(tmp_path, "pyproject.toml", "[project]\nname='a'\n")
    _mk(tmp_path, "requirements-dev.txt", "pytest\n")
    _mk(tmp_path, "deploy/docker-compose.yml")
    _mk(tmp_path, "Dockerfile", "FROM python:3.12\n")
    _mk(tmp_path, "svc/Dockerfile", "FROM alpine:latest\n")
    _mk(tmp_path, ".github/workflows/ci.yml")
    _mk(tmp_path, "k8s/deploy.yaml")
    _mk(tmp_path, "src/app.py", "x = 1\n")          # code: never an artifact
    _mk(tmp_path, "notes.md", "hi\n")               # *.md -> docs
    _mk(tmp_path, "data.bin", "hi\n")               # unmatched, decodable: text/unknown
    arts = discover_artifacts(tmp_path, "myapp")
    assert sorted(arts) == [
        ".github/workflows/ci.yml", "Dockerfile", "data.bin", "deploy/docker-compose.yml",
        "k8s/deploy.yaml", "notes.md", "pyproject.toml", "requirements-dev.txt",
        "svc/Dockerfile",
    ]
    assert arts["notes.md"].roles == ["docs"]
    py = arts["pyproject.toml"]
    assert py.id == "can://artifact/myapp/pyproject.toml"
    assert py.format == "toml" and "dependency-manifest" in py.roles
    assert arts["Dockerfile"].roles == ["container-image"]
    assert arts["svc/Dockerfile"].roles == ["container-image"]
    assert arts["deploy/docker-compose.yml"].roles == ["service-topology"]
    assert arts["k8s/deploy.yaml"].roles == ["service-topology"]
    assert arts[".github/workflows/ci.yml"].roles == ["ci"]
    assert arts["data.bin"].format == "text" and arts["data.bin"].roles == ["unknown"]


def test_source_hash_and_ignores(tmp_path):
    _mk(tmp_path, "pyproject.toml", "content-here\n")
    _mk(tmp_path, ".venv/pyvenv.cfg", "home = /x\n")
    _mk(tmp_path, ".git/config", "[core]\n")
    _mk(tmp_path, "node_modules/a/package.json", "{}")
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["pyproject.toml"]
    a = arts["pyproject.toml"]
    assert a.source == "content-here\n"
    assert a.sha256 == hashlib.sha256(b"content-here\n").hexdigest()
    assert a.size_bytes == len(b"content-here\n")


def test_matched_non_utf8_file_becomes_binary_artifact(tmp_path):
    """Rule-matched (pyproject.toml) but not UTF-8 decodable: never dropped --
    format downgrades to binary, the rule's roles survive, source is empty."""
    raw = b"\xff\xfe\x00bad"
    (tmp_path / "pyproject.toml").write_bytes(raw)
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["pyproject.toml"]
    a = arts["pyproject.toml"]
    assert a.format == "binary"
    assert a.roles == ["dependency-manifest", "tool-config"]
    assert a.source == ""
    assert a.sha256 == hashlib.sha256(raw).hexdigest()
    assert a.size_bytes == len(raw)


def test_ignores_own_codeanalyzer_venv(tmp_path):
    """Regression: a default (non --no-venv) run provisions its own analysis
    virtualenv under <project>/.codeanalyzer/<name>/virtualenv/ before
    discovery runs (Codeanalyzer.__enter__). Without an ignore, discovery
    walks straight into it and picks up pyvenv.cfg (machine-specific `home=`
    -> breaks determinism) and site-packages *.toml files."""
    _mk(tmp_path, ".codeanalyzer/x/virtualenv/pyvenv.cfg", "home = /machine/specific\n")
    _mk(tmp_path, "pyproject.toml", "[project]\nname='a'\n")
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["pyproject.toml"]


def test_discovers_kind_yaml(tmp_path):
    """kind/*.yml|yaml -> service-topology, alongside the existing k8s/ rules
    (user-surfaced miss on a real corpus)."""
    _mk(tmp_path, "kind/cluster.yml")
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["kind/cluster.yml"]
    assert arts["kind/cluster.yml"].roles == ["service-topology"]


def test_discovers_packaging_docs_legal_files(tmp_path):
    """Round-2 role vocabulary growth: packaging/docs/legal."""
    _mk(tmp_path, "MANIFEST.in", "include *.txt\n")
    _mk(tmp_path, "LICENSE", "MIT\n")
    _mk(tmp_path, "LICENSE.md", "MIT\n")   # legal-prefixed rule wins over *.md
    _mk(tmp_path, "COPYRIGHT.txt", "(c) 2026\n")
    _mk(tmp_path, "NOTICE", "third-party notices\n")
    _mk(tmp_path, "CONTRIBUTING.rst", "how to contribute\n")
    arts = discover_artifacts(tmp_path, "a")
    assert arts["MANIFEST.in"].roles == ["packaging"]
    assert arts["LICENSE"].roles == ["legal"]
    assert arts["LICENSE.md"].roles == ["legal"]
    assert arts["COPYRIGHT.txt"].roles == ["legal"]
    assert arts["NOTICE"].roles == ["legal"]
    assert arts["CONTRIBUTING.rst"].roles == ["docs"]


def test_extensionless_shebang_script_is_captured(tmp_path):
    """odoo-bin-style entrypoint: no extension, so no RULES glob can name it --
    the shebang fallback refines its roles to ["script"]."""
    _mk(tmp_path, "odoo-bin", "#!/usr/bin/env python3\nimport sys\n")
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["odoo-bin"]
    assert arts["odoo-bin"].format == "text" and arts["odoo-bin"].roles == ["script"]


def test_extensionless_binary_captured_as_binary_artifact(tmp_path):
    raw = b"\xff\xfe\x00#!bad"
    (tmp_path / "odoo-bin").write_bytes(raw)
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["odoo-bin"]
    a = arts["odoo-bin"]
    assert a.format == "binary" and a.roles == ["unknown"] and a.source == ""
    assert a.sha256 == hashlib.sha256(raw).hexdigest()


def test_extensionless_text_without_shebang_captured_as_unknown(tmp_path):
    _mk(tmp_path, "README", "just some notes, no shebang\n")
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["README"]
    assert arts["README"].format == "text" and arts["README"].roles == ["unknown"]
    assert arts["README"].source == "just some notes, no shebang\n"


def test_unmatched_text_file_captured_as_unknown(tmp_path):
    """No RULES glob names *.csv: still captured, never dropped."""
    _mk(tmp_path, "data.csv", "a,b\n1,2\n")
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["data.csv"]
    assert arts["data.csv"].format == "text"
    assert arts["data.csv"].roles == ["unknown"]
    assert arts["data.csv"].source == "a,b\n1,2\n"


def test_unmatched_binary_file_captured_with_empty_source(tmp_path):
    raw = b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03"
    (tmp_path / "logo.png").write_bytes(raw)
    arts = discover_artifacts(tmp_path, "a")
    assert list(arts) == ["logo.png"]
    a = arts["logo.png"]
    assert a.format == "binary"
    assert a.roles == ["unknown"]
    assert a.source == ""
    assert a.sha256 == hashlib.sha256(raw).hexdigest()
    assert a.size_bytes == len(raw)


def test_py_files_never_become_artifacts(tmp_path):
    """`.py` is the symbol table's domain -- an unmatched `.py` file never
    becomes an artifact (unlike every other unmatched extension). `setup.py`
    is the deliberate, pre-existing exception: it is rule-matched (as a
    dependency-manifest) despite the `.py` suffix, so "rule-matched: as
    today" still applies to it -- only the *unmatched* fallback excludes
    `.py`."""
    _mk(tmp_path, "src/app.py", "x = 1\n")
    _mk(tmp_path, "pkg/__init__.py", "")
    _mk(tmp_path, "setup.py", "from setuptools import setup\n")
    arts = discover_artifacts(tmp_path, "a")
    assert "src/app.py" not in arts and "pkg/__init__.py" not in arts
    assert "setup.py" in arts and arts["setup.py"].roles == ["dependency-manifest"]
