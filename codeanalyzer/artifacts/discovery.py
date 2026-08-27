from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple

from codeanalyzer.schema.ids import artifact_id
from codeanalyzer.schema.py_schema import PyArtifact

# (glob pattern against the repo-relative POSIX path, format, roles).
# First match wins; patterns are checked in order.
RULES: List[Tuple[str, str, List[str]]] = [
    ("requirements*.txt", "requirements", ["dependency-manifest"]),
    ("pyproject.toml", "toml", ["dependency-manifest", "tool-config"]),
    ("setup.py", "text", ["dependency-manifest"]),
    ("setup.cfg", "ini", ["dependency-manifest", "tool-config"]),
    ("Pipfile", "toml", ["dependency-manifest"]),
    ("Pipfile.lock", "json", ["dependency-manifest"]),
    ("poetry.lock", "toml", ["dependency-manifest"]),
    ("uv.lock", "toml", ["dependency-manifest"]),
    ("environment.yml", "yaml", ["dependency-manifest"]),
    ("environment.yaml", "yaml", ["dependency-manifest"]),
    ("Dockerfile", "dockerfile", ["container-image"]),
    ("*.dockerfile", "dockerfile", ["container-image"]),
    ("docker-compose*.yml", "yaml", ["service-topology"]),
    ("docker-compose*.yaml", "yaml", ["service-topology"]),
    ("compose.yml", "yaml", ["service-topology"]),
    ("compose.yaml", "yaml", ["service-topology"]),
    ("k8s/*.yml", "yaml", ["service-topology"]),
    ("k8s/*.yaml", "yaml", ["service-topology"]),
    ("Chart.yaml", "yaml", ["service-topology"]),
    ("values.yaml", "yaml", ["service-topology"]),
    (".github/workflows/*.yml", "yaml", ["ci"]),
    (".github/workflows/*.yaml", "yaml", ["ci"]),
    (".gitlab-ci.yml", "yaml", ["ci"]),
    (".env", "text", ["env"]),
    (".env.*", "text", ["env"]),
    ("tox.ini", "ini", ["tool-config"]),
    ("noxfile.py", "text", ["tool-config"]),
    ("Makefile", "text", ["tool-config"]),
    ("*.cfg", "ini", ["unknown"]),
    ("*.toml", "toml", ["unknown"]),
]

_IGNORED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".tox", ".nox",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea",
    "build", "dist", ".eggs",
}


def _classify(rel_posix: str) -> Tuple[str, List[str]] | None:
    name = rel_posix.rsplit("/", 1)[-1]
    for pattern, fmt, roles in RULES:
        target = rel_posix if ("/" in pattern or pattern.startswith("**")) else name
        if fnmatch.fnmatch(target, pattern):
            return fmt, roles
    return None


def discover_artifacts(project_dir: Path, app_name: str) -> Dict[str, PyArtifact]:
    """Walk the project and return rule-matched files as artifacts, sorted by path."""
    out: Dict[str, PyArtifact] = {}
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project_dir)
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        rel_posix = rel.as_posix()
        hit = _classify(rel_posix)
        if hit is None:
            continue
        fmt, roles = hit
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # text-only by spec; binaries never become artifacts
        out[rel_posix] = PyArtifact(
            id=artifact_id(app_name, rel_posix), path=rel_posix, format=fmt,
            roles=list(roles), size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(), source=text,
        )
    return out
