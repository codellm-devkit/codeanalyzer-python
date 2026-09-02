"""Task 2: every file becomes a PyArtifact node except `.py` files and ignored
dirs (never-drop inventory, issue #157 follow-up). Rule-matched files keep
their format/roles; unmatched files fall back to text/unknown (or binary).
Also covers the text-capture control (`capture_text`)."""
import hashlib
from pathlib import Path
from codeanalyzer.schema import model_dump
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


def test_source_is_the_whole_file_however_large(tmp_path):
    """#172: there is no byte cap. `source` is the whole file or "" -- never a
    prefix -- so a large file round-trips verbatim, multi-byte chars included."""
    content = ("café " * 100_000) + "tail"  # ~600 KB, well past the old 262144 cap
    _mk(tmp_path, "notes.md", content)
    raw = content.encode("utf-8")
    assert len(raw) > 262144
    art = discover_artifacts(tmp_path, "a")["notes.md"]
    assert art.source == content
    assert art.sha256 == hashlib.sha256(raw).hexdigest()   # sha256 always full-file
    assert art.size_bytes == len(raw)


def test_capture_text_false_empties_source_everywhere_else_identical(tmp_path):
    _mk(tmp_path, "pyproject.toml", "[project]\nname='a'\n")
    _mk(tmp_path, "data.csv", "a,b\n1,2\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01")
    with_text = discover_artifacts(tmp_path, "a")
    without_text = discover_artifacts(tmp_path, "a", capture_text=False)
    assert set(with_text) == set(without_text)
    for path in with_text:
        b = without_text[path]
        assert b.source == ""
        a_dict = model_dump(with_text[path])
        b_dict = model_dump(b)
        a_dict["source"] = b_dict["source"] = ""
        assert a_dict == b_dict


def test_dependency_manifest_source_is_empty_with_capture_text_false(tmp_path):
    """capture_text=False empties source for manifests exactly like everything
    else. (The old cap exemption for dependency-manifests went away with the
    cap itself -- every decodable file is captured in full now, #172.)"""
    _mk(tmp_path, "pyproject.toml", '[project]\ndependencies = ["requests"]\n')
    arts = discover_artifacts(tmp_path, "a", capture_text=False)
    assert arts["pyproject.toml"].source == ""


def test_discovers_terraform_flaskenv_properties_and_generic_ini(tmp_path):
    """Task 3 riders (#152): *.tf -> new role iac; .flaskenv joins the env
    basename family; *.properties is a new format; a generic *.ini rule
    (placed after the specific tox.ini rule) makes non-tox ini files
    namespace-eligible for config-key extraction too."""
    _mk(tmp_path, "main.tf", 'resource "x" "y" {}\n')
    _mk(tmp_path, ".flaskenv", "FLASK_ENV=production\n")
    _mk(tmp_path, "app.properties", "key=value\n")
    _mk(tmp_path, "mypy.ini", "[mypy]\nstrict = true\n")
    _mk(tmp_path, "tox.ini", "[tox]\nenvlist = py312\n")
    arts = discover_artifacts(tmp_path, "a")
    assert arts["main.tf"].format == "text" and arts["main.tf"].roles == ["iac"]
    assert arts[".flaskenv"].format == "text" and arts[".flaskenv"].roles == ["env"]
    assert arts["app.properties"].format == "properties"
    assert arts["app.properties"].roles == ["tool-config"]
    assert arts["mypy.ini"].format == "ini" and arts["mypy.ini"].roles == ["tool-config"]
    # tox.ini keeps matching its own specific (pre-existing) rule, unshadowed.
    assert arts["tox.ini"].format == "ini" and arts["tox.ini"].roles == ["tool-config"]
