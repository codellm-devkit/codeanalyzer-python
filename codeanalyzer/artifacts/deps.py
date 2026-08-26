"""Dependency parsing for the artifact layer.

Turns a manifest artifact's text into ``PyDependency`` children: name +
version spec + scope + ecosystem + direct/transitive. Sits on the shared
low-level readers in ``codeanalyzer.manifests``. Lockfiles contribute
``resolved_version`` for an already-declared dependency (and, for lock-only
transitives, a ``direct=False`` node). Every parser is defensive: an
unreadable or malformed manifest yields no dependencies, never an exception —
the artifact node it hangs off is emitted regardless.
"""
from __future__ import annotations

import configparser
import re
from typing import Dict, Optional

from codeanalyzer import manifests
from codeanalyzer.schema.py_schema import PyDependency

try:  # 3.11+ stdlib; requires-python is >=3.9, so this is not guaranteed.
    import tomllib as _toml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.9/3.10 only
    _toml = None  # type: ignore


def _dep(
    name: str, version_spec: Optional[str], scope: str, direct: bool = True
) -> PyDependency:
    return PyDependency(
        name=name,
        version_spec=version_spec,
        ecosystem="pypi",
        scope=scope,
        direct=direct,
    )


def _from_toml_array(
    text: str, key: str, scope: str, out: Dict[str, PyDependency]
) -> None:
    span = manifests.deps_array_span(manifests.strip_comments(text), key)
    if span is None:
        return
    for m in manifests.PKG_STRING.finditer(span):
        name, ver = manifests.split_spec(m.group(1))
        if name:
            out.setdefault(name, _dep(name, ver, scope))


def parse_pyproject(text: str) -> Dict[str, PyDependency]:
    """PEP 621 ``[project]`` deps, optional-dependency extras, and build reqs.

    Uses ``tomllib`` when present for the optional-dependencies table (its
    per-group structure needs a real parser); the main ``dependencies`` array
    and ``build-system.requires`` fall back to the bracket-aware text reader,
    which works on every supported Python.
    """
    out: Dict[str, PyDependency] = {}
    _from_toml_array(text, "dependencies", "runtime", out)
    _from_toml_array(text, "requires", "build", out)  # [build-system].requires

    if _toml is not None:
        try:
            data = _toml.loads(text)
        except Exception:
            data = {}
        optional = (data.get("project") or {}).get("optional-dependencies") or {}
        for group, specs in optional.items():
            scope = _extra_scope(group)
            for spec in specs if isinstance(specs, list) else []:
                name, ver = manifests.split_spec(str(spec))
                if name:
                    out.setdefault(name, _dep(name, ver, scope))
    return out


def _extra_scope(group: str) -> str:
    g = group.lower()
    if g in ("dev", "develop", "development"):
        return "development"
    if g in ("test", "tests", "testing"):
        return "test"
    return "optional"


def parse_requirements(text: str, scope: str) -> Dict[str, PyDependency]:
    out: Dict[str, PyDependency] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue  # skip blanks, comments, and pip flags (-r, -e, --hash)
        name, ver = manifests.split_spec(line)
        if name:
            out.setdefault(name, _dep(name, ver, scope))
    return out


def parse_setup_cfg(text: str) -> Dict[str, PyDependency]:
    """``[options] install_requires`` (runtime) and
    ``[options.extras_require]`` groups (dev/test/optional)."""
    out: Dict[str, PyDependency] = {}
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return out

    def _add(block: str, scope: str) -> None:
        for spec in block.splitlines():
            name, ver = manifests.split_spec(spec.strip())
            if name:
                out.setdefault(name, _dep(name, ver, scope))

    if parser.has_option("options", "install_requires"):
        _add(parser.get("options", "install_requires"), "runtime")
    if parser.has_section("options.extras_require"):
        for group, block in parser.items("options.extras_require"):
            _add(block, _extra_scope(group))
    return out


_PIPFILE_PKG = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*=\s*(.+)$")


def parse_pipfile(text: str) -> Dict[str, PyDependency]:
    """``[packages]`` (runtime) and ``[dev-packages]`` (development). Pipfile is
    TOML; parse it structurally when tomllib is available, else a small
    section-scanning fallback that handles the common ``name = "spec"`` form."""
    out: Dict[str, PyDependency] = {}
    sections = {"packages": "runtime", "dev-packages": "development"}
    if _toml is not None:
        try:
            data = _toml.loads(text)
        except Exception:
            data = {}
        for section, scope in sections.items():
            for name, spec in (data.get(section) or {}).items():
                ver = _pipfile_version(spec)
                out.setdefault(name, _dep(name, ver, scope))
        return out
    # Fallback: line scan bounded by section headers.
    current: Optional[str] = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = sections.get(s[1:-1])
            continue
        if current is None:
            continue
        m = _PIPFILE_PKG.match(line)
        if m:
            name = m.group(1)
            raw = m.group(2).strip().strip("'\"")
            ver = raw if raw not in ("*", "") else None
            out.setdefault(name, _dep(name, ver, current))
    return out


def _pipfile_version(spec) -> Optional[str]:
    if isinstance(spec, str):
        return None if spec in ("*", "") else spec
    if isinstance(spec, dict):
        v = spec.get("version")
        return None if v in ("*", "", None) else v
    return None


def apply_lockfile_versions(
    deps: Dict[str, PyDependency], locked: Dict[str, str]
) -> None:
    """Pin ``resolved_version`` on already-declared deps, and add lock-only
    packages as ``direct=False`` transitive nodes. Name match is
    case-insensitive with ``-``/``_`` normalized (pypi identity rules)."""
    index = {_norm(name): name for name in deps}
    for locked_name, ver in locked.items():
        key = _norm(locked_name)
        if key in index:
            deps[index[key]].resolved_version = ver
        else:
            deps[locked_name] = PyDependency(
                name=locked_name,
                resolved_version=ver,
                ecosystem="pypi",
                scope="unknown",
                direct=False,
            )


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


# --- lockfile readers: name -> resolved version. Best-effort, never raise. ---


def read_lock(filename: str, text: str) -> Dict[str, str]:
    if filename in ("poetry.lock", "uv.lock"):
        return _read_toml_lock(text)
    if filename == "Pipfile.lock":
        return _read_pipfile_lock(text)
    return {}


def _read_toml_lock(text: str) -> Dict[str, str]:
    """poetry.lock and uv.lock both use ``[[package]]`` arrays of tables with
    ``name`` / ``version``."""
    out: Dict[str, str] = {}
    if _toml is not None:
        try:
            data = _toml.loads(text)
        except Exception:
            return out
        for pkg in data.get("package", []) or []:
            name, ver = pkg.get("name"), pkg.get("version")
            if name and ver:
                out[str(name)] = str(ver)
        return out
    # Fallback without tomllib: scan [[package]] blocks textually.
    name = None
    for line in text.splitlines():
        s = line.strip()
        if s == "[[package]]":
            name = None
        elif s.startswith("name"):
            name = _lock_scalar(s)
        elif s.startswith("version") and name:
            ver = _lock_scalar(s)
            if ver:
                out[name] = ver
    return out


def _lock_scalar(line: str) -> Optional[str]:
    _, _, rhs = line.partition("=")
    val = rhs.strip().strip("'\"")
    return val or None


def _read_pipfile_lock(text: str) -> Dict[str, str]:
    """Pipfile.lock is JSON: ``{default: {name: {version: "==x"}}, develop: …}``."""
    import json

    out: Dict[str, str] = {}
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return out
    for section in ("default", "develop"):
        for name, meta in (data.get(section) or {}).items():
            ver = meta.get("version") if isinstance(meta, dict) else None
            if ver:
                out[name] = str(ver).lstrip("=")
    return out
