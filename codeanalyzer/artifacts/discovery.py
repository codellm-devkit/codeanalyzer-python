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
    ("kind/*.yml", "yaml", ["service-topology"]),
    ("kind/*.yaml", "yaml", ["service-topology"]),
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
    ("MANIFEST.in", "text", ["packaging"]),
    ("LICENSE*", "text", ["legal"]),
    ("COPYRIGHT*", "text", ["legal"]),
    ("NOTICE*", "text", ["legal"]),
    ("*.md", "text", ["docs"]),
    ("*.rst", "text", ["docs"]),
    ("*.cfg", "ini", ["unknown"]),
    ("*.toml", "toml", ["unknown"]),
]

_IGNORED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".tox", ".nox",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea",
    "build", "dist", ".eggs", ".codeanalyzer", "virtualenv", "site-packages",
}


def _classify(rel_posix: str) -> Tuple[str, List[str]] | None:
    name = rel_posix.rsplit("/", 1)[-1]
    for pattern, fmt, roles in RULES:
        target = rel_posix if ("/" in pattern or pattern.startswith("**")) else name
        if fnmatch.fnmatch(target, pattern):
            return fmt, roles
    return None


def _capture_source(
    raw: bytes, text: str, capture_text: bool, text_max_bytes: int
) -> Tuple[str, bool]:
    """Decide ``(source, text_truncated)`` for a decodable file.

    Slices ``raw`` (not ``text``) for the cap, so it is a true byte cap even
    when it lands inside a multi-byte character -- ``errors="ignore"`` drops
    the dangling partial char at the cut, so this never raises."""
    if not capture_text:
        return "", False
    if len(raw) <= text_max_bytes:
        return text, False
    return raw[:text_max_bytes].decode("utf-8", errors="ignore"), True


def discover_artifacts(
    project_dir: Path,
    app_name: str,
    *,
    capture_text: bool = True,
    text_max_bytes: int = 262144,
) -> Dict[str, PyArtifact]:
    """Walk the project and return every file as an artifact, sorted by path.

    Never-drop inventory (issue #157 follow-up): a rule-matched file keeps its
    RULES format/roles; everything else falls back to ``text``/``["unknown"]``
    (``source`` captured), or ``binary``/empty ``source`` when it is not UTF-8
    decodable -- rule-matched but undecodable files downgrade to ``binary``
    too, keeping the rule's roles. The one exclusion is a `.py` file no RULES
    entry names: the symbol table already owns it. ``setup.py`` is the
    deliberate exception -- it IS rule-matched (a dependency-manifest), so it
    is captured like any other manifest despite the `.py` suffix.

    ``capture_text=False`` empties ``source`` everywhere (inventory otherwise
    identical); a decodable file over ``text_max_bytes`` gets a truncated
    ``source`` and ``text_truncated=True``. ``sha256``/``size_bytes`` always
    reflect the full file regardless of either knob."""
    out: Dict[str, PyArtifact] = {}
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project_dir)
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        rel_posix = rel.as_posix()
        name = rel_posix.rsplit("/", 1)[-1]
        hit = _classify(rel_posix)
        if hit is None and name.endswith(".py"):
            continue  # symbol table's domain (setup.py is rule-matched above)

        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
            decodable = True
        except UnicodeDecodeError:
            text, decodable = "", False

        if hit is not None:
            fmt, roles = hit
        else:
            fmt, roles = "text", ["unknown"]
            # Extensionless shebang script (e.g. odoo-bin): no RULES glob can
            # name these (nothing to match on but the shebang itself), so this
            # is the one deterministic content-sniff refinement.
            if decodable and "." not in name and text.startswith("#!"):
                roles = ["script"]
        if decodable:
            source, text_truncated = _capture_source(raw, text, capture_text, text_max_bytes)
        else:
            fmt, source, text_truncated = "binary", "", False

        out[rel_posix] = PyArtifact(
            id=artifact_id(app_name, rel_posix), path=rel_posix, format=fmt,
            roles=list(roles), size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            source=source, text_truncated=text_truncated,
        )
    return out
