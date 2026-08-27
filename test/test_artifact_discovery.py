"""Task 2: rule-matched files become PyArtifact nodes; nothing else does."""
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
    _mk(tmp_path, "notes.md", "hi\n")               # unmatched: no node
    arts = discover_artifacts(tmp_path, "myapp")
    assert sorted(arts) == [
        ".github/workflows/ci.yml", "Dockerfile", "deploy/docker-compose.yml",
        "k8s/deploy.yaml", "pyproject.toml", "requirements-dev.txt", "svc/Dockerfile",
    ]
    py = arts["pyproject.toml"]
    assert py.id == "can://artifact/myapp/pyproject.toml"
    assert py.format == "toml" and "dependency-manifest" in py.roles
    assert arts["Dockerfile"].roles == ["container-image"]
    assert arts["svc/Dockerfile"].roles == ["container-image"]
    assert arts["deploy/docker-compose.yml"].roles == ["service-topology"]
    assert arts["k8s/deploy.yaml"].roles == ["service-topology"]
    assert arts[".github/workflows/ci.yml"].roles == ["ci"]


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


def test_unreadable_binary_is_skipped(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(b"\xff\xfe\x00bad")
    arts = discover_artifacts(tmp_path, "a")
    assert arts == {}


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
