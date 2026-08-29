"""PyDependency / PyImportBinding construction from discovered artifacts.

Deterministic by default: reads only repo files. ``resolve_installed`` adds
filesystem reads of ``<venv>/**/site-packages/*.dist-info`` (never runs an
interpreter), tagged ``prov: installed-metadata``.

``-r``/``-c`` refs in a requirements-format manifest are chased one level
only, by design: a chased target's own refs are not followed further.

``unresolved_imports`` is byte-identical run-to-run only within one Python
minor version: it is filtered against ``sys.stdlib_module_names``, and that
set's membership varies across minors (a module added to or removed from the
stdlib)."""

import posixpath
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codeanalyzer.artifacts.parsers import (
    RawDep, _kind_for_requirements, normalize_name, parse_lock_pins, parse_manifest,
    parse_requirement_refs,
)
from codeanalyzer.schema.py_schema import (
    PyArtifact, PyDependency, PyImportBinding, PyModule,
)

_LOCK_BASENAMES = ("poetry.lock", "uv.lock", "Pipfile.lock")

# Small, non-exhaustive alias table for the worst offenders; everything else
# rides the same-name rule or --resolve-installed. prov: heuristic. Identity
# entries (key == value) do not belong here -- the same-name rule below
# already covers them, and a redundant identity entry only mints a spurious
# "heuristic" prov on what is actually a plain same-name match.
_KNOWN_IMPORT_ALIASES: Dict[str, str] = {
    "pyyaml": "yaml", "beautifulsoup4": "bs4", "pillow": "PIL",
    "scikit-learn": "sklearn", "opencv-python": "cv2", "python-dateutil": "dateutil",
    "msgpack-python": "msgpack", "protobuf": "google.protobuf", "attrs": "attr",
}


def _stdlib_names() -> set:
    return set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def _installed_top_levels(venv_dir: Optional[Path]) -> Dict[str, List[str]]:
    """{normalized dist name: [top-level import names]} from *.dist-info files."""
    out: Dict[str, List[str]] = {}
    if venv_dir is None or not venv_dir.exists():
        return out
    for di in sorted(venv_dir.glob("**/site-packages/*.dist-info")):
        name = None
        meta = di / "METADATA"
        if meta.exists():
            m = re.search(r"^Name:\s*(.+)$", meta.read_text(errors="replace"), re.M)
            if m:
                name = normalize_name(m.group(1).strip())
        if name is None:
            continue
        tl = di / "top_level.txt"
        if tl.exists():
            out[name] = [l.strip() for l in tl.read_text().splitlines() if l.strip()]
    return out


def _is_requirements_format(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.startswith("requirements") and base.endswith(".txt")


def _resolve_ref(manifest_path: str, ref: str) -> Optional[str]:
    """POSIX-join a ``-r``/``-c`` ref against its manifest's directory,
    normalized and repo-relative. ``None`` if it would escape ``project_dir``."""
    manifest_dir = manifest_path.rsplit("/", 1)[0] if "/" in manifest_path else ""
    joined = posixpath.normpath(posixpath.join(manifest_dir, ref) if manifest_dir else ref)
    if joined == ".." or joined.startswith("../") or posixpath.isabs(joined):
        return None
    return joined


def _full_text(project_dir: Path, path: str, art: PyArtifact) -> str:
    """Manifest/lock extraction must never depend on the stored ``source`` --
    that's capped by ``text_max_bytes`` and emptied by ``capture_text=False``
    (both payload-size controls on the JSON/Neo4j payload, not extraction
    controls). Read the real file fresh instead; fall back to ``art.source``
    only if it is gone (e.g. a synthetic artifact in a unit test, or the file
    vanished mid-run).

    Mirrored (not imported -- this name is module-private) by
    ``core._artifact_full_text`` for the same reason on config-key
    extraction (#152); keep the two in sync if this logic changes."""
    try:
        return (project_dir / path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return art.source


def build_dependency_view(
    artifacts: Dict[str, PyArtifact],
    modules: Dict[str, PyModule],
    project_dir: Path,
    venv_dir: Optional[Path],
    resolve_installed: bool,
) -> Tuple[List[PyDependency], List[PyImportBinding]]:
    deps: List[PyDependency] = []

    def _emit(raw: List[RawDep], declared_in: str, kind_override: Optional[str] = None) -> None:
        for r in raw:
            deps.append(PyDependency(
                name=r.name, ecosystem="pypi", spec=r.spec,
                kind=kind_override if kind_override is not None else r.kind,
                extras=sorted(r.extras), declared_in=declared_in, prov=["declared"],
            ))

    # 1. Declared records from every dependency-manifest artifact (non-lock).
    for path in sorted(artifacts):
        art = artifacts[path]
        if "dependency-manifest" not in art.roles:
            continue
        if path.rsplit("/", 1)[-1] in _LOCK_BASENAMES:
            continue
        text = _full_text(project_dir, path, art)
        raw, partial = parse_manifest(path, text)
        art.extraction = "partial" if partial else "full"
        _emit(raw, art.id)

        # 1b. -r/-c refs: a target that is itself a dependency-manifest is
        # parsed on its own above; a target with no RULES match for that role
        # (e.g. base.txt -- never-drop inventory still captures it, just not
        # as a manifest) is chased here and attributed to the referring
        # artifact. Gate on the role, not mere presence in `artifacts`: since
        # #157 every file is discovered, so presence alone no longer implies
        # "already parsed as a manifest above".
        if not _is_requirements_format(path):
            continue
        for ref in parse_requirement_refs(text):
            resolved = _resolve_ref(path, ref)
            if resolved is None:
                continue
            target_art = artifacts.get(resolved)
            if target_art is not None and "dependency-manifest" in target_art.roles:
                continue
            target = project_dir / resolved
            if not target.is_file():
                continue
            try:
                ref_text = target.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                continue
            # Force requirements-format dispatch (chased targets may not be
            # named requirements*.txt), but recompute kind from the real
            # basename so e.g. `-r dev.txt` still yields kind="dev".
            raw_ref, _ = parse_manifest("requirements.txt", ref_text)
            real_kind = _kind_for_requirements(resolved.rsplit("/", 1)[-1])
            _emit(raw_ref, art.id, kind_override=real_kind)

    # 2. Lock backfill (locked_version + prov "lockfile"). A pin with no
    # manifest declaration is a *transitive* dependency: emitted with
    # direct=False, attributed to the lock artifact (#152 reconciliation).
    pins: Dict[str, str] = {}
    pin_lock_artifact: Dict[str, str] = {}
    for path in sorted(artifacts):
        if path.rsplit("/", 1)[-1] in _LOCK_BASENAMES:
            lock_text = _full_text(project_dir, path, artifacts[path])
            lock_pins = parse_lock_pins(path, lock_text)
            pins.update(lock_pins)
            for name in lock_pins:
                pin_lock_artifact[name] = artifacts[path].id
            # A lock with real content that yields zero pins failed to parse
            # (corrupt/unrecognized shape) -- don't claim "full" extraction
            # for nothing extracted. An empty/whitespace-only lock is not a
            # failure (nothing to extract), so it still counts as "full".
            artifacts[path].extraction = (
                "full" if lock_pins or not lock_text.strip() else "partial"
            )
    for d in deps:
        if d.name in pins:
            d.locked_version = pins[d.name]
            d.prov = sorted(set(d.prov) | {"lockfile"})
    declared_names = {d.name for d in deps}
    for name in sorted(set(pins) - declared_names):
        deps.append(PyDependency(
            name=name, ecosystem="pypi", kind="runtime", declared_in=pin_lock_artifact[name],
            direct=False, locked_version=pins[name], prov=["lockfile"],
        ))

    # 3. Import universe from the symbol table (top-level segments only).
    # `module_name` is `py_file.stem` -- the leaf filename only (e.g. "api"
    # for "odoo/api.py"), never the package path -- so it alone misses the
    # top-level package name itself. Derive that from the symbol-table KEYS
    # (repo-relative POSIX paths) too: first path segment when nested, else
    # the root file's own stem. Keep the module_name-derived stems as well
    # (harmless -- still excludes leaf-name imports the key pass can't see).
    local = {m.module_name.split(".")[0] for m in modules.values() if m.module_name}
    local |= {
        key.split("/", 1)[0] if "/" in key else Path(key).stem for key in modules
    }
    stdlib = _stdlib_names()
    imported: set = set()
    for m in modules.values():
        for imp in m.imports or []:
            top = (imp.module or imp.name or "").split(".")[0]
            if top and top not in stdlib and top not in local:
                imported.add(top)

    # 4. provides_imports: same-name rule, alias table, optional installed metadata.
    installed = _installed_top_levels(venv_dir) if resolve_installed else {}
    for d in deps:
        provides: List[str] = []
        same = d.name.replace("-", "_")
        for candidate in {d.name, same}:
            if candidate in imported:
                provides.append(candidate)
        alias = _KNOWN_IMPORT_ALIASES.get(d.name)
        if alias and alias.split(".")[0] in imported:
            provides.append(alias)
            d.prov = sorted(set(d.prov) | {"heuristic"})
        if d.name in installed:
            for top in installed[d.name]:
                if top in imported and top not in provides:
                    provides.append(top)
            d.prov = sorted(set(d.prov) | {"installed-metadata"})
        d.provides_imports = sorted(set(provides))

    # 5. Unresolved: imported, not stdlib/local, not provided by any dependency.
    # Top-level segment only: `imported` (step 3) is already top-level-only, but
    # a dotted alias (e.g. protobuf -> "google.protobuf") puts the FULL dotted
    # path into provides_imports, so comparing it against `imported` verbatim
    # never matches and "google" falsely resurfaces as unresolved even though
    # protobuf declares it.
    provided = {p.split(".")[0] for d in deps for p in d.provides_imports}
    unresolved = [
        PyImportBinding(module=m) for m in sorted(imported - provided)
    ]
    deps.sort(key=lambda d: (d.name, d.declared_in))
    return deps, unresolved
