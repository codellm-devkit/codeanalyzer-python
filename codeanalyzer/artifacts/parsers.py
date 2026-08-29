"""Dependency-manifest readers. Pure text-in/records-out; no execution, no I/O."""

import ast
import configparser
import json
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 CI leg
    import tomli as tomllib

import yaml


@dataclass(frozen=True)
class RawDep:
    name: str  # PEP 503 normalized
    spec: str = ""
    kind: str = "runtime"  # runtime|dev|optional|build
    extras: Tuple[str, ...] = ()


def normalize_name(raw: str) -> str:
    return re.sub(r"[-_.]+", "-", raw).lower()


_REQ_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]+)\])?\s*(?P<spec>[^;#]*)"
)


def parse_requirement_line(line: str, kind: str = "runtime") -> Optional[RawDep]:
    """One PEP 508-ish requirement line -> RawDep (None for options/paths/URLs)."""
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "--")) or line.startswith((".", "/")):
        return None
    if " @ " in line:
        line = line.split(" @ ", 1)[0].strip()  # direct ref (PEP 508): keep the name, drop the URL
    elif "://" in line:
        return None
    m = _REQ_LINE.match(line)
    if not m:
        return None
    extras = tuple(e.strip() for e in (m.group("extras") or "").split(",") if e.strip())
    spec = m.group("spec").strip().rstrip(",")
    if spec.endswith("\\"):
        spec = spec[:-1].rstrip()  # pip-compile --generate-hashes line continuation
    return RawDep(normalize_name(m.group("name")), spec, kind, extras)


_REF_LINE = re.compile(r"^(?:-r|--requirement|-c|--constraint)\s+(\S+)")


def parse_requirement_refs(text: str) -> List[str]:
    """-r/--requirement/-c/--constraint targets from a requirements file, in order."""
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = _REF_LINE.match(line)
        if m:
            out.append(m.group(1))
    return out


def _kind_for_requirements(basename: str) -> str:
    return "dev" if re.search(r"\b(dev|test|lint|doc)\b", basename, re.I) else "runtime"


def _parse_requirements(basename: str, text: str) -> List[RawDep]:
    kind = _kind_for_requirements(basename)
    out = []
    for line in text.splitlines():
        dep = parse_requirement_line(line, kind)
        if dep:
            out.append(dep)
    return out


def _spec_and_extras(spec) -> Tuple[str, Tuple[str, ...]]:
    """Poetry/Pipfile dep spec (bare string or {version, extras} inline table) -> (version, extras)."""
    if isinstance(spec, dict):
        return spec.get("version", "") or "", tuple(spec.get("extras", []) or [])
    return (spec if isinstance(spec, str) else ""), ()


def _parse_pyproject(text: str) -> List[RawDep]:
    data = tomllib.loads(text)
    out: List[RawDep] = []
    for req in (data.get("build-system") or {}).get("requires", []):
        d = parse_requirement_line(req, "build")
        if d:
            out.append(d)
    proj = data.get("project") or {}
    for req in proj.get("dependencies", []):
        d = parse_requirement_line(req)
        if d:
            out.append(d)
    for group in (proj.get("optional-dependencies") or {}).values():
        for req in group:
            d = parse_requirement_line(req, "optional")
            if d:
                out.append(d)
    poetry = ((data.get("tool") or {}).get("poetry")) or {}
    for name, spec in (poetry.get("dependencies") or {}).items():
        if normalize_name(name) == "python":
            continue
        v, ex = _spec_and_extras(spec)
        out.append(RawDep(normalize_name(name), v, "runtime", ex))
    for gname, group in (poetry.get("group") or {}).items():
        kind = "dev" if gname == "dev" else "optional"
        for name, spec in (group.get("dependencies") or {}).items():
            v, ex = _spec_and_extras(spec)
            out.append(RawDep(normalize_name(name), v, kind, ex))
    for name, spec in (poetry.get("dev-dependencies") or {}).items():  # legacy poetry
        v, ex = _spec_and_extras(spec)
        out.append(RawDep(normalize_name(name), v, "dev", ex))
    return out


def _parse_setup_py(text: str) -> Tuple[List[RawDep], bool]:
    """Static AST only. Literal lists lift; anything computed -> partial=True."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], True
    out: List[RawDep] = []
    partial = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "setup"):
            continue
        for kw in node.keywords:
            if kw.arg == "install_requires":
                lifted = _lift_str_list(kw.value)
                if lifted is None:
                    partial = True
                else:
                    out += [d for d in (parse_requirement_line(s) for s in lifted) if d]
            elif kw.arg == "extras_require":
                if not isinstance(kw.value, ast.Dict):
                    partial = True
                    continue
                for v in kw.value.values:
                    lifted = _lift_str_list(v)
                    if lifted is None:
                        partial = True
                    else:
                        out += [d for d in (parse_requirement_line(s, "optional") for s in lifted) if d]
    return out, partial


def _lift_str_list(node: ast.AST) -> Optional[List[str]]:
    if isinstance(node, (ast.List, ast.Tuple)) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
    ):
        return [e.value for e in node.elts]
    return None


def _parse_setup_cfg(text: str) -> List[RawDep]:
    cp = configparser.ConfigParser()
    cp.read_string(text)
    out: List[RawDep] = []
    if cp.has_option("options", "install_requires"):
        for line in cp.get("options", "install_requires").splitlines():
            d = parse_requirement_line(line)
            if d:
                out.append(d)
    if cp.has_section("options.extras_require"):
        for _, val in cp.items("options.extras_require"):
            for line in val.splitlines():
                d = parse_requirement_line(line, "optional")
                if d:
                    out.append(d)
    return out


def _parse_pipfile(text: str) -> List[RawDep]:
    data = tomllib.loads(text)
    out: List[RawDep] = []
    for section, kind in (("packages", "runtime"), ("dev-packages", "dev")):
        for name, spec in (data.get(section) or {}).items():
            v, ex = _spec_and_extras(spec)
            out.append(RawDep(normalize_name(name), "" if v == "*" else v, kind, ex))
    return out


def _parse_environment_yml(text: str) -> List[RawDep]:
    data = yaml.safe_load(text) or {}
    out: List[RawDep] = []
    for item in data.get("dependencies") or []:
        if isinstance(item, str):
            d = parse_requirement_line(item)
            if d and d.name not in ("pip", "python"):
                out.append(d)
        elif isinstance(item, dict):
            for req in item.get("pip") or []:
                d = parse_requirement_line(req)
                if d:
                    out.append(d)
    return out


def parse_manifest(path: str, text: str) -> Tuple[List[RawDep], bool]:
    """Dispatch on basename -> (records, partial). Unknown basenames -> ([], False)."""
    base = path.rsplit("/", 1)[-1]
    try:
        if base.startswith("requirements") and base.endswith(".txt"):
            return _parse_requirements(base, text), False
        if base == "pyproject.toml":
            return _parse_pyproject(text), False
        if base == "setup.py":
            return _parse_setup_py(text)
        if base == "setup.cfg":
            return _parse_setup_cfg(text), False
        if base == "Pipfile":
            return _parse_pipfile(text), False
        if base in ("environment.yml", "environment.yaml"):
            return _parse_environment_yml(text), False
    except Exception:
        return [], True  # unparseable manifest: keep the artifact, flag extraction
    return [], False


def parse_lock_pins(path: str, text: str) -> Dict[str, str]:
    """Lock file -> {normalized name: pinned version}. Never creates records."""
    base = path.rsplit("/", 1)[-1]
    try:
        if base in ("poetry.lock", "uv.lock"):
            data = tomllib.loads(text)
            return {
                normalize_name(p["name"]): str(p["version"])
                for p in data.get("package") or [] if "name" in p and "version" in p
            }
        if base == "Pipfile.lock":
            data = json.loads(text)
            out = {}
            for section in ("default", "develop"):
                for name, meta in (data.get(section) or {}).items():
                    v = (meta or {}).get("version", "")
                    out[normalize_name(name)] = v.lstrip("=")
            return out
    except Exception:
        return {}
    return {}
