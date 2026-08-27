"""Config-key extraction for the artifact layer.

Turns a structured config artifact's text into ``PyConfigKey`` children: a
canonical dotted key, its value, and any recognized placeholder references.
Flat (``.env``, ``.properties``, ``.ini``) and nested (``.yaml``, ``.json``,
``.toml``) formats collapse into one dotted key space, so a later config-use
edge can resolve to a definition by shared (``namespace``, ``key``).

This is a pure overlay: every parser is defensive and returns ``{}`` on failure
rather than raising, so the artifact node it hangs off is emitted regardless of
whether its structure was understood.
"""
from __future__ import annotations

import configparser
import json
import re
from typing import Any, Dict, List

import yaml

from codeanalyzer.schema.py_schema import PyConfigKey

try:  # tomllib is 3.11+ stdlib; tomli is its identical-API backport on 3.9/3.10.
    import tomllib as _toml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as _toml  # type: ignore
    except ModuleNotFoundError:
        _toml = None  # type: ignore

# `${VAR}`, `$VAR`, and `%(VAR)s` style placeholders -> recorded as env: refs.
_PLACEHOLDER = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)|%\(([A-Za-z_][A-Za-z0-9_]*)\)s"
)


def _refs(value: Any) -> List[str]:
    if not isinstance(value, str):
        return []
    out: List[str] = []
    for m in _PLACEHOLDER.finditer(value):
        name = m.group(1) or m.group(2) or m.group(3)
        if name:
            ref = f"env:{name}"
            if ref not in out:
                out.append(ref)
    return out


def _key(
    artifact_id: str, dotted: str, value: Any, namespace: str | None
) -> PyConfigKey:
    from codeanalyzer.schema.ids import config_key_id

    return PyConfigKey(
        id=config_key_id(artifact_id, dotted),
        key=dotted,
        namespace=namespace,
        value=value if _scalar(value) else None,
        references=_refs(value),
    )


def _scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _flatten(prefix: str, node: Any, out: Dict[str, Any]) -> None:
    """Depth-first flatten of nested mappings into dotted keys. Leaves (scalars
    and lists) terminate; a list is kept whole as the leaf value."""
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            _flatten(child, v, out)
    else:
        out[prefix] = node


def parse(
    artifact_id: str, filename: str, suffix: str, text: str
) -> Dict[str, PyConfigKey]:
    """Dispatch on file kind. ``filename`` handles dotfiles (``.env``) that carry
    their identity in the name; ``suffix`` handles extensioned formats."""
    try:
        if (
            filename == ".env"
            or filename.startswith(".env.")
            or filename == ".flaskenv"
        ):
            return _parse_env(artifact_id, text)
        if suffix in (".yml", ".yaml"):
            return _parse_mapping(artifact_id, _load_yaml(text), namespace=None)
        if suffix == ".json":
            return _parse_mapping(artifact_id, json.loads(text), namespace=None)
        if suffix == ".toml":
            return _parse_toml(artifact_id, text)
        if suffix in (".properties",):
            return _parse_properties(artifact_id, text)
        if suffix in (".ini", ".cfg"):
            return _parse_ini(artifact_id, text)
    except Exception:
        return {}
    return {}


def _load_yaml(text: str) -> Any:
    # A multi-document stream: merge documents left-to-right into one mapping.
    docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    merged: Dict[str, Any] = {}
    for d in docs:
        merged.update(d)
    return merged


def _parse_mapping(
    artifact_id: str, data: Any, namespace: str | None
) -> Dict[str, PyConfigKey]:
    if not isinstance(data, dict):
        return {}
    flat: Dict[str, Any] = {}
    _flatten("", data, flat)
    return {k: _key(artifact_id, k, v, namespace) for k, v in flat.items() if k}


def _parse_toml(artifact_id: str, text: str) -> Dict[str, PyConfigKey]:
    if _toml is None:
        return {}  # no tomllib/tomli: artifact still inventoried, keys skipped
    return _parse_mapping(artifact_id, _toml.loads(text), namespace=None)


def _parse_env(artifact_id: str, text: str) -> Dict[str, PyConfigKey]:
    out: Dict[str, PyConfigKey] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export ") :]
        if "=" not in s:
            continue
        key, _, raw = s.partition("=")
        key = key.strip()
        if not key:
            continue
        val = raw.strip().strip("'\"")
        out[key] = _key(artifact_id, key, val, namespace="env")
    return out


def _parse_properties(artifact_id: str, text: str) -> Dict[str, PyConfigKey]:
    out: Dict[str, PyConfigKey] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        sep = min((i for i in (s.find("="), s.find(":")) if i != -1), default=-1)
        if sep == -1:
            continue
        key = s[:sep].strip()
        val = s[sep + 1 :].strip()
        if key:
            out[key] = _key(artifact_id, key, val, namespace=None)
    return out


def _parse_ini(artifact_id: str, text: str) -> Dict[str, PyConfigKey]:
    parser = configparser.ConfigParser()
    parser.read_string(text)
    out: Dict[str, PyConfigKey] = {}
    for section in parser.sections():
        for opt, val in parser.items(section, raw=True):
            dotted = f"{section}.{opt}"
            out[dotted] = _key(artifact_id, dotted, val, namespace=None)
    return out
