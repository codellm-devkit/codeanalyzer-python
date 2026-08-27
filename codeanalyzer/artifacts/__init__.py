"""Repository-artifact layer: the non-source file inventory.

``artifact_inventory(project_dir, app_name, capture_text=...)`` walks the project
directory once and returns the ``PyApplication.artifacts`` map — a ``PyArtifact``
per non-source file, with parsed ``PyDependency`` / ``PyConfigKey`` children where
the file is a recognized manifest or config. Application-anchored and level-free:
attached the same way at every ``-a`` level, mirroring ``provenance.repository_info``.

Discovery skips the same directories the call-graph pass ignores
(``PyCG._SKIP_DIRS`` — venvs, VCS, caches). Python source stays in the symbol
table and is not re-inventoried here. Nothing else is dropped: an unrecognized
file is still an artifact, classified ``other``.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Optional

from codeanalyzer.artifacts import config as _config
from codeanalyzer.artifacts import deps as _deps
from codeanalyzer.options import DEFAULT_ARTIFACT_TEXT_MAX_BYTES
from codeanalyzer.schema.ids import artifact_id, dependency_id
from codeanalyzer.schema.py_schema import PyArtifact
from codeanalyzer.semantic_analysis.pycg.pycg_analysis import PyCG

DEFAULT_TEXT_CAP = DEFAULT_ARTIFACT_TEXT_MAX_BYTES  # single source of truth

_SKIP_DIRS = PyCG._SKIP_DIRS  # reuse the exact skip set (venvs, .git, caches …)

# A lockfile pins resolved_version on its OWNING manifest's dependencies (and
# contributes that manifest's lock-only transitives). Mapping lock -> manifest
# filename keeps a poetry.lock from bleeding versions onto a sibling
# requirements.txt that it does not describe.
_LOCKFILES = frozenset({"poetry.lock", "uv.lock", "Pipfile.lock"})
_LOCK_OWNER = {
    "poetry.lock": "pyproject.toml",
    "uv.lock": "pyproject.toml",
    "Pipfile.lock": "Pipfile",
}


def artifact_inventory(
    project_dir: Path,
    app_name: str,
    *,
    capture_text: bool = True,
    text_cap: int = DEFAULT_TEXT_CAP,
) -> Dict[str, PyArtifact]:
    project_dir = Path(project_dir)
    artifacts: Dict[str, PyArtifact] = {}
    texts: Dict[str, Optional[str]] = {}
    # Owning manifest's rel path -> {name: resolved_version}.
    locks: Dict[str, Dict[str, str]] = {}

    for path in sorted(_walk(project_dir)):
        rel = path.relative_to(project_dir).as_posix()
        raw = _read_bytes(path)
        if raw is None:
            continue  # unreadable (permissions, race) — skip, don't crash
        text, truncated = _decode(raw, text_cap)
        texts[rel] = text
        node = _build_artifact(app_name, rel, raw, text, truncated, capture_text)
        artifacts[rel] = node
        if path.name in _LOCKFILES and text is not None:
            owner_rel = (Path(rel).parent / _LOCK_OWNER[path.name]).as_posix()
            locks[owner_rel] = _deps.read_lock(path.name, text)

    _attach_dependencies(artifacts, texts, locks)
    return artifacts


def _walk(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in parts[:-1]):
            continue
        if path.suffix == ".py":
            continue  # source lives in the symbol table
        yield path


def _read_bytes(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _decode(raw: bytes, text_cap: int) -> "tuple[Optional[str], bool]":
    """Decode up to ``text_cap`` bytes as utf-8, returning ``(text, truncated)``
    or ``(None, False)`` for binary. Used for parsing regardless of
    ``capture_text`` — so disabling text capture never disables dependency/config
    extraction.

    A cap that lands mid-codepoint would make a strict decode raise on an
    otherwise-textual file; ``errors="ignore"`` drops only the split trailing
    bytes. Binary is still detected by decoding a small strict probe of the head
    (a real binary fails there long before the cap)."""
    truncated = len(raw) > text_cap
    head = raw[:text_cap]
    try:
        head[: min(len(head), 4096)].decode("utf-8")
    except UnicodeDecodeError:
        return None, False  # binary
    text = head.decode("utf-8", errors="ignore") if truncated else head.decode("utf-8")
    return text, truncated


def _build_artifact(
    app_name: str,
    rel: str,
    raw: bytes,
    text: Optional[str],
    truncated: bool,
    capture_text: bool,
) -> PyArtifact:
    node = PyArtifact(
        id=artifact_id(app_name, rel),
        path=rel,
        artifact_kind=_classify(rel),
        format=_format(rel),
        content_hash=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )
    if capture_text and text is not None:
        node.text = text
        node.text_encoding = "utf-8"
        node.text_truncated = truncated
    return node


def _attach_dependencies(
    artifacts: Dict[str, PyArtifact],
    texts: Dict[str, Optional[str]],
    locks: Dict[str, Dict[str, str]],
) -> None:
    for rel, node in artifacts.items():
        name = Path(rel).name
        text = texts.get(rel)  # parse buffer, present even when capture is off
        parsed = None
        if text is not None:
            if name == "pyproject.toml":
                parsed = _deps.parse_pyproject(text)
            elif name == "setup.cfg":
                parsed = _deps.parse_setup_cfg(text)
            elif name == "Pipfile":
                parsed = _deps.parse_pipfile(text)
            elif name.startswith("requirements") and name.endswith(".txt"):
                scope = "development" if "dev" in name else "runtime"
                parsed = _deps.parse_requirements(text, scope)
        if not parsed:
            _attach_config(node, text)
            continue
        lock = locks.get(rel, {})  # only this manifest's own lockfile
        if lock:
            _deps.apply_lockfile_versions(parsed, lock)
        for dep_name, dep in parsed.items():
            dep.id = dependency_id(node.id, dep_name)
        node.dependencies = parsed


def _attach_config(node: PyArtifact, text: Optional[str]) -> None:
    if text is None:
        return
    name = Path(node.path).name
    suffix = Path(node.path).suffix.lower()
    keys = _config.parse(node.id, name, suffix, text)
    if keys:
        node.config_keys = keys


# --- classification (name/extension -> artifact_kind + format) ---------------

_BUILD_MANIFESTS = frozenset(
    {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "Pipfile",
        "environment.yml",
    }
)
_KIND_BY_SUFFIX = {
    ".yml": "configuration",
    ".yaml": "configuration",
    ".json": "configuration",
    ".toml": "configuration",
    ".ini": "configuration",
    ".cfg": "configuration",
    ".properties": "configuration",
    ".conf": "configuration",
    ".tf": "infrastructure",
    ".tfvars": "infrastructure",
    ".md": "documentation",
    ".rst": "documentation",
    ".txt": "documentation",
    ".sh": "script",
    ".bash": "script",
    ".csv": "data",
    ".sql": "data",
}
_FORMAT_BY_SUFFIX = {
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".properties": "properties",
}


def _classify(rel: str) -> str:
    name = Path(rel).name
    suffix = Path(rel).suffix.lower()
    if name == ".env" or name.startswith(".env.") or name == ".flaskenv":
        return "configuration"
    if name in _LOCKFILES:
        return "dependency_lockfile"
    if name in _BUILD_MANIFESTS or (
        name.startswith("requirements") and name.endswith(".txt")
    ):
        return "build_manifest"
    if name == "Dockerfile" or name.startswith("Dockerfile"):
        return "container"
    if "compose" in name and suffix in (".yml", ".yaml"):
        return "container"
    if _is_ci(rel):
        return "ci"
    return _KIND_BY_SUFFIX.get(suffix, "other")


def _is_ci(rel: str) -> bool:
    posix = Path(rel).as_posix()
    return posix.startswith(".github/workflows/") or Path(rel).name in (
        ".gitlab-ci.yml",
        ".travis.yml",
        "azure-pipelines.yml",
    )


def _format(rel: str) -> Optional[str]:
    return _FORMAT_BY_SUFFIX.get(Path(rel).suffix.lower())
