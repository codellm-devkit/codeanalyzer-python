from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from codeanalyzer.schema.ids import artifact_id
from codeanalyzer.schema.py_schema import PyArtifact
from codeanalyzer.utils import logger

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
    ("config/*.yml", "yaml", ["tool-config"]),
    ("config/*.yaml", "yaml", ["tool-config"]),
    ("*.tf", "text", ["iac"]),
    (".github/workflows/*.yml", "yaml", ["ci"]),
    (".github/workflows/*.yaml", "yaml", ["ci"]),
    (".gitlab-ci.yml", "yaml", ["ci"]),
    (".env", "text", ["env"]),
    (".env.*", "text", ["env"]),
    (".flaskenv", "text", ["env"]),
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
    ("*.properties", "properties", ["tool-config"]),
    # Generic fallback AFTER the specific tox.ini rule above, so a non-tox
    # *.ini file (mypy.ini, pytest.ini, ...) still reaches format="ini" --
    # config-key extraction (#152) is namespace-eligible by format.
    ("*.ini", "ini", ["tool-config"]),
]

_IGNORED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".tox", ".nox",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea",
    "build", "dist", ".eggs", ".codeanalyzer", "virtualenv", "site-packages",
}


def _resolve_exclusions(project_dir: Path, exclude_paths: Iterable[Path]) -> List[Path]:
    """Resolve the paths a run writes to, dropping any that would take the whole
    project with them (#207).

    A *directory* exclusion at or above the project root would empty the
    inventory, which is worse than the bug it guards against, so it is refused.
    That case is still covered, because the caller also passes the individual
    output *files* -- excluding ``<project>/analysis.json`` costs one file
    instead of the entire tree.
    """
    root = project_dir.resolve()
    kept = []
    for given in exclude_paths:
        resolved = given.resolve()
        if root.is_relative_to(resolved):
            logger.warning(
                f"Not excluding {resolved} from artifact discovery: it holds the "
                f"project itself. Only this run's own output files under it are "
                f"skipped; anything else written there is ingested as an artifact."
            )
            continue
        kept.append(resolved)
    return kept


def _is_excluded(path: Path, excluded: List[Path]) -> bool:
    """True when ``path`` is, or sits inside, one of ``excluded`` (a file entry
    matches only itself). Checked on the path as walked *and* on its resolved
    form, so an output directory reached through a symlink is caught from either
    side."""
    if not excluded:
        return False
    if any(path.is_relative_to(directory) for directory in excluded):
        return True
    return any(path.resolve().is_relative_to(directory) for directory in excluded)


def _classify(rel_posix: str) -> Tuple[str, List[str]] | None:
    name = rel_posix.rsplit("/", 1)[-1]
    for pattern, fmt, roles in RULES:
        target = rel_posix if ("/" in pattern or pattern.startswith("**")) else name
        if fnmatch.fnmatch(target, pattern):
            return fmt, roles
    return None


def discover_artifacts(
    project_dir: Path,
    app_name: str,
    *,
    capture_text: bool = True,
    exclude_paths: Iterable[Path] = (),
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

    ``exclude_paths`` names what this run writes -- the ``--output`` and cache
    directories, and the output files inside them (#207). Without them a run
    whose output lands inside the project ingests the previous run's whole
    ``analysis.json``, and each run embeds the one before it until the process is
    killed decoding its own output. Matching is on resolved paths, so a
    relative, `..`-laden or symlinked target excludes the same tree, and a target
    outside the project excludes nothing. A directory that holds the project
    itself is refused (it would empty the inventory); the file entries still
    cover that case.

    ``source`` is the WHOLE file or nothing -- never a prefix (#172). A
    decodable file is captured in full; ``capture_text=False`` empties
    ``source`` everywhere (inventory otherwise identical), and an undecodable
    file gets ``""`` as ``binary``. ``sha256``/``size_bytes`` always reflect
    the full file regardless."""
    out: Dict[str, PyArtifact] = {}
    excluded = _resolve_exclusions(project_dir, exclude_paths)
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        if _is_excluded(path, excluded):
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
            source = text if capture_text else ""
        else:
            fmt, source = "binary", ""

        out[rel_posix] = PyArtifact(
            id=artifact_id(app_name, rel_posix), path=rel_posix, format=fmt,
            roles=list(roles), size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            source=source,
        )
    return out
