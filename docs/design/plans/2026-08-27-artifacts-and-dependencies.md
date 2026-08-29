# Artifacts and Dependencies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit non-code artifacts, evidence-tagged dependency records, and unresolved imports in both schema v2 projections (closes #157).

**Architecture:** A new `codeanalyzer/artifacts/` package scans the project once (after the symbol table, at every level): `discovery.py` turns rule-matched files into `PyArtifact` nodes, `parsers.py` reads dependency manifests into raw records, `dependencies.py` builds `PyDependency`/`PyImportBinding` lists. `core.py` attaches all three to `PyApplication`; `neo4j/` projects them as language-neutral `:Artifact`/`:Package` nodes joined to the existing `:PyExternal` ghosts.

**Tech Stack:** pydantic models (v1/v2 compat helpers in `codeanalyzer/schema/__init__.py`), stdlib `tomllib` (`tomli` backport below 3.11), `yaml` (already a dependency via entrypoints), stdlib `ast`/`configparser`/`hashlib`.

**Spec:** `docs/design/specs/2026-08-27-artifacts-and-dependencies-design.md`

## Global Constraints

- Never add AI/Claude attribution anywhere (commits, code, docs). Conventional Commits.
- Artifact ids are language-neutral: `can://artifact/<app>/<repo-relative-path>`.
- `source` is verbatim and unbounded; artifacts are text-only (rule-matched formats).
- Dependency `prov` vocabulary exactly: `declared`, `lockfile`, `installed-metadata`, `heuristic`.
- Default run reads only repo files (byte-identical across machines); venv probing only behind `--resolve-installed`.
- Lock files never create records — they only backfill `locked_version`.
- All three new sections are L1 data: identical at every `-a`; never gated by level.
- `setup.py` is parsed by static AST only — never executed, never imported.
- Sorted iteration everywhere (walks, dict builds) — determinism by construction.
- Pydantic v1 must keep working: no `model_dump`/`model_copy` calls on models outside the compat helpers.
- New runtime dependency allowed: `tomli>=2.0; python_version < '3.11'` only.

---

### Task 1: Schema models and ids

**Files:**
- Modify: `codeanalyzer/schema/ids.py` (append)
- Modify: `codeanalyzer/schema/py_schema.py` (new models near `PyExternalSymbol`; three new fields on `PyApplication`)
- Test: `test/test_artifact_models.py` (create)

**Interfaces:**
- Produces: `artifact_id(app_name: str, rel_path: str) -> str`; `purl_pypi(name: str) -> str`; models `PyArtifact`, `PyDependency`, `PyImportBinding`; `PyApplication.artifacts: Dict[str, PyArtifact]`, `.dependencies: List[PyDependency]`, `.unresolved_imports: List[PyImportBinding]`.

- [ ] **Step 1: Write the failing test**

```python
# test/test_artifact_models.py
"""Task 1: artifact/dependency schema models and id constructors."""
from codeanalyzer.schema import model_validate_json, model_dump_json
from codeanalyzer.schema.ids import artifact_id, purl_pypi
from codeanalyzer.schema.py_schema import (
    PyApplication, PyArtifact, PyDependency, PyImportBinding,
)


def test_artifact_id_is_language_neutral():
    assert artifact_id("myapp", "deploy/docker-compose.yml") == \
        "can://artifact/myapp/deploy/docker-compose.yml"


def test_purl_pypi():
    assert purl_pypi("pyyaml") == "pkg:pypi/pyyaml"


def test_models_round_trip():
    art = PyArtifact(
        id=artifact_id("a", "pyproject.toml"), path="pyproject.toml",
        format="toml", roles=["dependency-manifest"], size_bytes=10,
        sha256="ab" * 32, source="[project]\n",
    )
    dep = PyDependency(
        name="requests", spec=">=2.31", kind="runtime",
        declared_in=art.id, provides_imports=["requests"], prov=["declared"],
    )
    imp = PyImportBinding(module="yaml", bound_to="pyyaml", prov=["heuristic"])
    app = PyApplication.builder().symbol_table({}).call_graph([]).build()
    app.artifacts = {art.path: art}
    app.dependencies = [dep]
    app.unresolved_imports = [imp]
    back = model_validate_json(PyApplication, model_dump_json(app))
    assert back.artifacts["pyproject.toml"].kind == "artifact"
    assert back.artifacts["pyproject.toml"].extraction == "none"
    assert back.dependencies[0].locked_version is None
    assert back.unresolved_imports[0].bound_to == "pyyaml"


def test_defaults_empty_on_old_payload():
    app = PyApplication.builder().symbol_table({}).call_graph([]).build()
    back = model_validate_json(PyApplication, model_dump_json(app))
    assert back.artifacts == {} and back.dependencies == [] and back.unresolved_imports == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_artifact_models.py -v`
Expected: FAIL with `ImportError` (`artifact_id` / `PyArtifact` not defined).

- [ ] **Step 3: Implement**

Append to `codeanalyzer/schema/ids.py`:

```python
def artifact_id(app_name: str, rel_path: str) -> str:
    """Language-neutral artifact id: ``can://artifact/<app>/<rel-path>``.

    The first segment is a namespace (a language for code nodes, the literal
    ``artifact`` for files), so sibling analyzers over the same repo emit the
    same id for the same file."""
    return f"can://artifact/{app_name}/{rel_path}"


def purl_pypi(name: str) -> str:
    """Package URL for a (PEP 503 normalized) PyPI distribution name."""
    return f"pkg:pypi/{name}"
```

In `codeanalyzer/schema/py_schema.py`, after `PyExternalSymbol` (follow its style; use `@builder` like neighbors):

```python
@builder
class PyArtifact(BaseModel):
    """A recognized non-code file (config, manifest, CI, container spec).

    Captured broadly (node + verbatim ``source``); *meaning* is extracted
    narrowly — only ``dependency-manifest`` roles feed ``dependencies`` today.
    ``id`` is language-neutral (``can://artifact/<app>/<path>``)."""

    id: str = ""
    kind: str = "artifact"
    path: str  # repo-relative POSIX path (also the map key)
    format: str  # toml|yaml|json|ini|requirements|dockerfile|text
    roles: List[str] = []
    size_bytes: int = 0
    sha256: str = ""
    source: str = ""  # verbatim, unbounded by decision (spec §3)
    extraction: str = "none"  # none|partial|full


@builder
class PyDependency(BaseModel):
    """One declared third-party dependency, evidence-tagged via ``prov``."""

    name: str  # PEP 503 normalized
    spec: str = ""
    kind: str = "runtime"  # runtime|dev|optional|build
    extras: List[str] = []
    declared_in: str = ""  # PyArtifact id
    locked_version: Optional[str] = None
    provides_imports: List[str] = []
    prov: List[str] = []  # declared|lockfile|installed-metadata|heuristic


@builder
class PyImportBinding(BaseModel):
    """A top-level import no declared dependency accounts for."""

    module: str
    bound_to: Optional[str] = None  # best-effort distribution name
    prov: List[str] = []
```

On `PyApplication`, after `external_symbols`:

```python
    # Non-code artifacts, declared dependencies, and undeclared imports
    # (spec 2026-08-27). L1 data: identical at every analysis level.
    artifacts: Dict[str, PyArtifact] = {}
    dependencies: List[PyDependency] = []
    unresolved_imports: List[PyImportBinding] = []
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest test/test_artifact_models.py test/test_v2_keystone.py -v`
Expected: PASS (keystone suite proves no regression to the envelope).

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/schema/ids.py codeanalyzer/schema/py_schema.py test/test_artifact_models.py
git commit -m "feat(schema): PyArtifact/PyDependency/PyImportBinding models and can://artifact ids"
```

---

### Task 2: Artifact discovery

**Files:**
- Create: `codeanalyzer/artifacts/__init__.py`, `codeanalyzer/artifacts/discovery.py`
- Test: `test/test_artifact_discovery.py` (create)

**Interfaces:**
- Consumes: `artifact_id`, `PyArtifact` (Task 1).
- Produces: `discover_artifacts(project_dir: Path, app_name: str) -> Dict[str, PyArtifact]` (sorted keys, repo-relative POSIX paths). `RULES: List[Tuple[str, str, List[str]]]` (glob pattern, format, roles).

- [ ] **Step 1: Write the failing test**

```python
# test/test_artifact_discovery.py
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
    _mk(tmp_path, ".github/workflows/ci.yml")
    _mk(tmp_path, "src/app.py", "x = 1\n")          # code: never an artifact
    _mk(tmp_path, "notes.md", "hi\n")               # unmatched: no node
    arts = discover_artifacts(tmp_path, "myapp")
    assert sorted(arts) == [
        ".github/workflows/ci.yml", "Dockerfile", "deploy/docker-compose.yml",
        "pyproject.toml", "requirements-dev.txt",
    ]
    py = arts["pyproject.toml"]
    assert py.id == "can://artifact/myapp/pyproject.toml"
    assert py.format == "toml" and "dependency-manifest" in py.roles
    assert arts["Dockerfile"].roles == ["container-image"]
    assert arts["deploy/docker-compose.yml"].roles == ["service-topology"]
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
    (tmp_path / "settings.json").write_bytes(b"\xff\xfe\x00bad")
    arts = discover_artifacts(tmp_path, "a")
    assert arts == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/test_artifact_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: codeanalyzer.artifacts`.

- [ ] **Step 3: Implement**

`codeanalyzer/artifacts/__init__.py`:

```python
"""Non-code artifact capture and dependency extraction (spec 2026-08-27).

Capture is broad (every rule-matched config-shaped file becomes a
:class:`~codeanalyzer.schema.py_schema.PyArtifact`); extraction is narrow
(only dependency manifests are parsed for meaning in this unit)."""

from codeanalyzer.artifacts.discovery import discover_artifacts

__all__ = ["discover_artifacts"]
```

`codeanalyzer/artifacts/discovery.py`:

```python
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
    ("**/requirements*.txt", "requirements", ["dependency-manifest"]),
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
    ("**/Dockerfile", "dockerfile", ["container-image"]),
    ("*.dockerfile", "dockerfile", ["container-image"]),
    ("docker-compose*.yml", "yaml", ["service-topology"]),
    ("docker-compose*.yaml", "yaml", ["service-topology"]),
    ("**/docker-compose*.yml", "yaml", ["service-topology"]),
    ("**/docker-compose*.yaml", "yaml", ["service-topology"]),
    ("compose.yml", "yaml", ["service-topology"]),
    ("compose.yaml", "yaml", ["service-topology"]),
    ("k8s/**/*.yml", "yaml", ["service-topology"]),
    ("k8s/**/*.yaml", "yaml", ["service-topology"]),
    ("**/Chart.yaml", "yaml", ["service-topology"]),
    ("**/values.yaml", "yaml", ["service-topology"]),
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
```

- [ ] **Step 4: Run tests** — `uv run pytest test/test_artifact_discovery.py -v` — PASS.

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/artifacts test/test_artifact_discovery.py
git commit -m "feat(artifacts): rule-table discovery walk producing PyArtifact nodes"
```

---

### Task 3: Manifest parsers

**Files:**
- Create: `codeanalyzer/artifacts/parsers.py`
- Modify: `pyproject.toml` (add `"tomli>=2.0; python_version < '3.11'"` to `dependencies`)
- Test: `test/test_manifest_parsers.py` (create)

**Interfaces:**
- Produces: `RawDep` dataclass `(name, spec, kind, extras)` (name PEP 503 normalized); `normalize_name(raw: str) -> str`; `parse_manifest(fmt_path: str, text: str) -> Tuple[List[RawDep], bool]` returning `(deps, partial)` and dispatching on basename: requirements/pyproject/setup.py/setup.cfg/Pipfile/environment.yml; `parse_lock_pins(basename: str, text: str) -> Dict[str, str]` for poetry.lock, uv.lock, Pipfile.lock; `parse_requirement_line(line: str) -> RawDep | None` (exported for reuse).

- [ ] **Step 1: Write the failing test**

```python
# test/test_manifest_parsers.py
"""Task 3: every spec §6 manifest format parses into RawDep records."""
import textwrap
from codeanalyzer.artifacts.parsers import (
    RawDep, normalize_name, parse_lock_pins, parse_manifest,
)


def test_normalize_name():
    assert normalize_name("PyYAML") == "pyyaml"
    assert normalize_name("ruamel.yaml") == "ruamel-yaml"
    assert normalize_name("typing_extensions") == "typing-extensions"


def test_requirements_txt():
    text = textwrap.dedent("""\
        # comment
        requests>=2.31,<3
        pyyaml
        celery[redis]==5.3.*
        -e ./local-pkg
        --index-url https://example.invalid
    """)
    deps, partial = parse_manifest("requirements.txt", text)
    assert not partial
    assert [(d.name, d.spec) for d in deps] == [
        ("requests", ">=2.31,<3"), ("pyyaml", ""), ("celery", "==5.3.*"),
    ]
    assert deps[2].extras == ["redis"]
    assert all(d.kind == "runtime" for d in deps)


def test_requirements_dev_kind():
    deps, _ = parse_manifest("requirements-dev.txt", "pytest\n")
    assert deps[0].kind == "dev"


def test_pyproject_pep621_poetry_and_build():
    text = textwrap.dedent("""\
        [build-system]
        requires = ["setuptools>=68"]
        [project]
        dependencies = ["requests>=2.31"]
        [project.optional-dependencies]
        docs = ["sphinx"]
        [tool.poetry.dependencies]
        python = "^3.10"
        rich = "^13.0"
        [tool.poetry.group.dev.dependencies]
        mypy = "*"
    """)
    deps, partial = parse_manifest("pyproject.toml", text)
    assert not partial
    by = {(d.name, d.kind) for d in deps}
    assert ("setuptools", "build") in by
    assert ("requests", "runtime") in by
    assert ("sphinx", "optional") in by
    assert ("rich", "runtime") in by and ("mypy", "dev") in by
    assert ("python", "runtime") not in {(d.name, d.kind) for d in deps}  # interpreter, not a dep


def test_setup_py_static_literals():
    text = 'from setuptools import setup\nsetup(install_requires=["flask>=2"], extras_require={"test": ["pytest"]})\n'
    deps, partial = parse_manifest("setup.py", text)
    assert not partial
    assert {(d.name, d.kind) for d in deps} == {("flask", "runtime"), ("pytest", "optional")}


def test_setup_py_dynamic_is_partial():
    text = "from setuptools import setup\nreqs = compute()\nsetup(install_requires=reqs)\n"
    deps, partial = parse_manifest("setup.py", text)
    assert partial and deps == []


def test_setup_cfg():
    text = "[options]\ninstall_requires =\n    numpy>=1.24\n    pandas\n"
    deps, _ = parse_manifest("setup.cfg", text)
    assert [(d.name, d.spec) for d in deps] == [("numpy", ">=1.24"), ("pandas", "")]


def test_pipfile_and_environment_yml():
    pip = '[packages]\nrequests = ">=2.31"\n[dev-packages]\nblack = "*"\n'
    deps, _ = parse_manifest("Pipfile", pip)
    assert {(d.name, d.kind, d.spec) for d in deps} == {
        ("requests", "runtime", ">=2.31"), ("black", "dev", ""),
    }
    env = "dependencies:\n  - numpy=1.26\n  - pip\n  - pip:\n      - fastapi>=0.100\n"
    deps, _ = parse_manifest("environment.yml", env)
    assert {(d.name, d.spec) for d in deps} == {("numpy", "=1.26"), ("fastapi", ">=0.100")}


def test_lock_pins():
    poetry = '[[package]]\nname = "requests"\nversion = "2.31.0"\n[[package]]\nname = "PyYAML"\nversion = "6.0.1"\n'
    assert parse_lock_pins("poetry.lock", poetry) == {"requests": "2.31.0", "pyyaml": "6.0.1"}
    uv = '[[package]]\nname = "requests"\nversion = "2.32.0"\n'
    assert parse_lock_pins("uv.lock", uv) == {"requests": "2.32.0"}
    pipf = '{"default": {"requests": {"version": "==2.31.0"}}, "develop": {}}'
    assert parse_lock_pins("Pipfile.lock", pipf) == {"requests": "2.31.0"}
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest test/test_manifest_parsers.py -v` — FAIL (`No module named codeanalyzer.artifacts.parsers`).

- [ ] **Step 3: Implement `codeanalyzer/artifacts/parsers.py`**

```python
"""Dependency-manifest readers. Pure text-in/records-out; no execution, no I/O."""

import ast
import configparser
import json
import re
import sys
from dataclasses import dataclass, field
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
    if not line or line.startswith(("-", "--")) or "://" in line or line.startswith((".", "/")):
        return None
    m = _REQ_LINE.match(line)
    if not m:
        return None
    extras = tuple(e.strip() for e in (m.group("extras") or "").split(",") if e.strip())
    return RawDep(normalize_name(m.group("name")), m.group("spec").strip().rstrip(","), kind, extras)


def _kind_for_requirements(basename: str) -> str:
    return "dev" if re.search(r"(dev|test|lint|doc)", basename, re.I) else "runtime"


def _parse_requirements(basename: str, text: str) -> List[RawDep]:
    kind = _kind_for_requirements(basename)
    out = []
    for line in text.splitlines():
        dep = parse_requirement_line(line, kind)
        if dep:
            out.append(dep)
    return out


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
        out.append(RawDep(normalize_name(name), spec if isinstance(spec, str) else "", "runtime"))
    for gname, group in (poetry.get("group") or {}).items():
        kind = "dev" if gname == "dev" else "optional"
        for name, spec in (group.get("dependencies") or {}).items():
            out.append(RawDep(normalize_name(name), spec if isinstance(spec, str) else "", kind))
    for name, spec in (poetry.get("dev-dependencies") or {}).items():  # legacy poetry
        out.append(RawDep(normalize_name(name), spec if isinstance(spec, str) else "", "dev"))
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
            s = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
            out.append(RawDep(normalize_name(name), "" if s == "*" else s, kind))
    return out


def _parse_environment_yml(text: str) -> List[RawDep]:
    data = yaml.safe_load(text) or {}
    out: List[RawDep] = []
    for item in data.get("dependencies") or []:
        if isinstance(item, str):
            name, _, spec = item.partition("=")
            if normalize_name(name) in ("pip", "python"):
                continue
            out.append(RawDep(normalize_name(name), f"={spec}" if spec else ""))
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
```

Add to `pyproject.toml` `[project] dependencies`: `"tomli>=2.0; python_version < '3.11'",`

- [ ] **Step 4: Run tests** — `uv run pytest test/test_manifest_parsers.py -v` — PASS.

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/artifacts/parsers.py test/test_manifest_parsers.py pyproject.toml uv.lock
git commit -m "feat(artifacts): manifest parsers for all spec formats (static, no execution)"
```

(Run `uv lock` before committing so `uv.lock` picks up the tomli marker dep.)

---

### Task 4: Dependency view — records, lock backfill, import binding

**Files:**
- Create: `codeanalyzer/artifacts/dependencies.py`
- Test: `test/test_dependency_view.py` (create)

**Interfaces:**
- Consumes: Tasks 1–3 (`PyArtifact`, `RawDep`, `parse_manifest`, `parse_lock_pins`, `normalize_name`, `purl_pypi`).
- Produces: `build_dependency_view(artifacts: Dict[str, PyArtifact], modules: Dict[str, "PyModule"], venv_dir: Optional[Path], resolve_installed: bool) -> Tuple[List[PyDependency], List[PyImportBinding]]`. Mutates `artifacts` only to set `extraction` (`"full"`/`"partial"` on manifests). Dependencies sorted by `(name, declared_in)`; unresolved sorted by `module`.

- [ ] **Step 1: Write the failing test**

```python
# test/test_dependency_view.py
"""Task 4: declared records, lock backfill, provides_imports, unresolved imports."""
from pathlib import Path
from codeanalyzer.artifacts.discovery import discover_artifacts
from codeanalyzer.artifacts.dependencies import build_dependency_view
from codeanalyzer.schema.py_schema import PyImport, PyModule


def _module(name, imports):
    return PyModule.builder().file_path(f"/tmp/{name}.py").module_name(name).imports(
        [PyImport(module=m, name="*") for m in imports]
    ).build()


def _setup(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests>=2.31", "PyYAML"]\n'
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.32.3"\n'
    )
    arts = discover_artifacts(tmp_path, "app")
    mods = {
        "app.py": _module("app", ["requests", "yaml", "colorama", "os", "app.util"]),
        "app/util.py": _module("app.util", []),
    }
    return arts, mods


def test_records_lock_and_binding(tmp_path):
    arts, mods = _setup(tmp_path)
    deps, unresolved = build_dependency_view(arts, mods, None, False)
    by = {d.name: d for d in deps}
    assert by["requests"].prov == ["declared", "lockfile"]
    assert by["requests"].locked_version == "2.32.3"
    assert by["requests"].declared_in == "can://artifact/app/pyproject.toml"
    assert by["pyyaml"].locked_version is None and by["pyyaml"].prov == ["declared"]
    # provides_imports: requests trivially; pyyaml has no same-name import -> []
    assert by["requests"].provides_imports == ["requests"]
    assert by["pyyaml"].provides_imports == []
    assert arts["pyproject.toml"].extraction == "full"


def test_unresolved_imports(tmp_path):
    arts, mods = _setup(tmp_path)
    _, unresolved = build_dependency_view(arts, mods, None, False)
    u = {b.module: b for b in unresolved}
    # yaml: known-alias heuristic binds it to declared pyyaml -> NOT unresolved
    # colorama: imported, never declared -> unresolved, unbound
    # os: stdlib; app.util: local module -> neither appears
    assert set(u) == {"colorama"}
    assert u["colorama"].bound_to is None and u["colorama"].prov == []


def test_known_alias_binding(tmp_path):
    arts, mods = _setup(tmp_path)
    deps, unresolved = build_dependency_view(arts, mods, None, False)
    yaml_dep = next(d for d in deps if d.name == "pyyaml")
    assert "yaml" in yaml_dep.provides_imports and "heuristic" in yaml_dep.prov


def test_installed_metadata_binding(tmp_path):
    arts, mods = _setup(tmp_path)
    venv = tmp_path / ".venv"
    di = venv / "lib" / "python3.12" / "site-packages" / "PyYAML-6.0.1.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: PyYAML\nVersion: 6.0.1\n")
    (di / "top_level.txt").write_text("yaml\n_yaml\n")
    deps, _ = build_dependency_view(arts, mods, venv, True)
    yaml_dep = next(d for d in deps if d.name == "pyyaml")
    assert "yaml" in yaml_dep.provides_imports
    assert "installed-metadata" in yaml_dep.prov
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest test/test_dependency_view.py -v` — FAIL (module missing).

- [ ] **Step 3: Implement `codeanalyzer/artifacts/dependencies.py`**

```python
"""PyDependency / PyImportBinding construction from discovered artifacts.

Deterministic by default: reads only repo files. ``resolve_installed`` adds
filesystem reads of ``<venv>/**/site-packages/*.dist-info`` (never runs an
interpreter), tagged ``prov: installed-metadata``."""

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codeanalyzer.artifacts.parsers import (
    RawDep, normalize_name, parse_lock_pins, parse_manifest,
)
from codeanalyzer.schema.py_schema import (
    PyArtifact, PyDependency, PyImport, PyImportBinding, PyModule,
)

_LOCK_BASENAMES = ("poetry.lock", "uv.lock", "Pipfile.lock")

# Small, non-exhaustive alias table for the worst offenders; everything else
# rides the same-name rule or --resolve-installed. prov: heuristic.
_KNOWN_IMPORT_ALIASES: Dict[str, str] = {
    "pyyaml": "yaml", "beautifulsoup4": "bs4", "pillow": "PIL",
    "scikit-learn": "sklearn", "opencv-python": "cv2", "python-dateutil": "dateutil",
    "msgpack-python": "msgpack", "protobuf": "google.protobuf",
    "setuptools": "setuptools", "attrs": "attr", "pymongo": "pymongo",
}


def _stdlib_names() -> set:
    return set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def _installed_top_levels(venv_dir: Optional[Path]) -> Dict[str, List[str]]:
    """{normalized dist name: [top-level import names]} from *.dist-info files."""
    out: Dict[str, List[str]] = {}
    if venv_dir is None or not venv_dir.exists():
        return out
    for di in sorted(venv_dir.glob("lib/python*/site-packages/*.dist-info")):
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


def build_dependency_view(
    artifacts: Dict[str, PyArtifact],
    modules: Dict[str, PyModule],
    venv_dir: Optional[Path],
    resolve_installed: bool,
) -> Tuple[List[PyDependency], List[PyImportBinding]]:
    # 1. Declared records from every dependency-manifest artifact (non-lock).
    deps: List[PyDependency] = []
    for path in sorted(artifacts):
        art = artifacts[path]
        if "dependency-manifest" not in art.roles:
            continue
        if path.rsplit("/", 1)[-1] in _LOCK_BASENAMES:
            continue
        raw, partial = parse_manifest(path, art.source)
        art.extraction = "partial" if partial else "full"
        for r in raw:
            deps.append(PyDependency(
                name=r.name, spec=r.spec, kind=r.kind, extras=sorted(r.extras),
                declared_in=art.id, prov=["declared"],
            ))

    # 2. Lock backfill (locked_version + prov "lockfile"); locks never create records.
    pins: Dict[str, str] = {}
    for path in sorted(artifacts):
        if path.rsplit("/", 1)[-1] in _LOCK_BASENAMES:
            pins.update(parse_lock_pins(path, artifacts[path].source))
            artifacts[path].extraction = "full"
    for d in deps:
        if d.name in pins:
            d.locked_version = pins[d.name]
            d.prov = sorted(set(d.prov) | {"lockfile"})

    # 3. Import universe from the symbol table (top-level segments only).
    local = {m.module_name.split(".")[0] for m in modules.values() if m.module_name}
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
    provided = {p for d in deps for p in d.provides_imports}
    unresolved = [
        PyImportBinding(module=m) for m in sorted(imported - provided)
    ]
    deps.sort(key=lambda d: (d.name, d.declared_in))
    return deps, unresolved
```

Also export from `codeanalyzer/artifacts/__init__.py`: add `from codeanalyzer.artifacts.dependencies import build_dependency_view` and extend `__all__`.

- [ ] **Step 4: Run tests** — `uv run pytest test/test_dependency_view.py test/test_manifest_parsers.py -v` — PASS.

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/artifacts test/test_dependency_view.py
git commit -m "feat(artifacts): dependency records with lock backfill and import binding"
```

---

### Task 5: Core wiring + CLI flag

**Files:**
- Modify: `codeanalyzer/core.py` (in `analyze`, directly after the `detect_entrypoints(app, self.project_dir, self.options.entrypoint_rules)` call)
- Modify: `codeanalyzer/options/options.py` (`AnalysisOptions`: add `resolve_installed: bool = False`)
- Modify: `codeanalyzer/__main__.py` (add the flag; keep option count/order consistent with neighbors)
- Test: `test/test_artifact_pipeline.py` (create)

**Interfaces:**
- Consumes: `discover_artifacts`, `build_dependency_view` (Tasks 2/4).
- Produces: populated `app.artifacts` / `app.dependencies` / `app.unresolved_imports` at every level; CLI `--resolve-installed` (default off).

- [ ] **Step 1: Write the failing test**

```python
# test/test_artifact_pipeline.py
"""Task 5: sections populated at every level, identically (L1-data posture)."""
import json
from pathlib import Path
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump


def _run(tmp_path, project, level):
    out = tmp_path / f"out{level}"
    opts = AnalysisOptions(
        input=project, output=out, analysis_level=level,
        no_venv=True, cache_dir=tmp_path / f"cache{level}",
    )
    artifacts = Codeanalyzer(opts).analyze()
    return artifacts.application


def _fixture(tmp_path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\ndependencies = ["requests"]\n')
    (proj / "Dockerfile").write_text("FROM python:3.12\n")
    (proj / "app.py").write_text("import requests\nimport colorama\n")
    return proj


def test_sections_identical_across_levels(tmp_path):
    proj = _fixture(tmp_path)
    a1 = _run(tmp_path, proj, 1)
    a2 = _run(tmp_path, proj, 2)
    d1 = json.loads(model_dump_json_app(a1))
    d2 = json.loads(model_dump_json_app(a2))
    for field in ("artifacts", "dependencies", "unresolved_imports"):
        assert d1[field] == d2[field]
    assert sorted(d1["artifacts"]) == ["Dockerfile", "pyproject.toml"]
    assert [d["name"] for d in d1["dependencies"]] == ["requests"]
    assert [u["module"] for u in d1["unresolved_imports"]] == ["colorama"]


def model_dump_json_app(app):
    from codeanalyzer.schema import model_dump_json
    return model_dump_json(app)


def test_resolve_installed_flag_default_off():
    from codeanalyzer.options import AnalysisOptions
    assert AnalysisOptions.__fields__ if hasattr(AnalysisOptions, "__fields__") else True
    assert AnalysisOptions(input=Path(".")).resolve_installed is False
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest test/test_artifact_pipeline.py -v` — FAIL (`resolve_installed` unknown / sections empty).

- [ ] **Step 3: Implement**

`options.py`: add `resolve_installed: bool = False` beside `no_venv`.

`core.py`, after the `detect_entrypoints(...)` call (comment style matches neighbors):

```python
        # Artifacts + dependencies: L1 data, every level, never varies with -a
        # (spec 2026-08-27). Deterministic by default; venv probing is opt-in.
        from codeanalyzer.artifacts import build_dependency_view, discover_artifacts

        app.artifacts = discover_artifacts(self.project_dir, self.app_name)
        app.dependencies, app.unresolved_imports = build_dependency_view(
            app.artifacts,
            app.symbol_table,
            self.virtualenv if self.options.resolve_installed else None,
            self.options.resolve_installed,
        )
```

(`self.app_name` and `self.virtualenv` already exist on `Codeanalyzer`; check their exact attribute names in `__init__` and use those.)

`__main__.py`: add beside `--no-venv`:

```python
    resolve_installed: bool = typer.Option(
        False, "--resolve-installed",
        help="Additionally bind imports via the project venv's installed metadata "
             "(*.dist-info); output becomes machine-dependent (prov: installed-metadata).",
    ),
```

and pass `resolve_installed=resolve_installed` into `AnalysisOptions(...)`.

- [ ] **Step 4: Run tests** — `uv run pytest test/test_artifact_pipeline.py test/test_cli.py -v` — PASS (CLI test asserts existing flags unaffected). Then `uv run python scripts/update_readme.py` to refresh the README `--help` block.

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/core.py codeanalyzer/options/options.py codeanalyzer/__main__.py README.md test/test_artifact_pipeline.py
git commit -m "feat(cli): emit artifacts/dependencies at every level; add --resolve-installed"
```

---

### Task 6: Neo4j projection

**Files:**
- Modify: `codeanalyzer/neo4j/schema.py` (catalog: 2 node labels, 5 rel types, 2 constraints)
- Modify: `codeanalyzer/neo4j/project.py` (new `_project_artifacts(...)` called from `project(...)`)
- Modify: `schema.neo4j.json` (regenerate)
- Test: `test/test_neo4j_artifacts.py` (create)

**Interfaces:**
- Consumes: populated app sections (Task 5); `purl_pypi` (Task 1); existing `RowBuilder`, external-ghost id scheme.
- Produces: catalog entries — `NodeLabel("Artifact", "Artifact", "id", {...})`, `NodeLabel("Package", "Package", "id", {"id": "string", "ecosystem": "string", "name": "string"})`; rels `HAS_ARTIFACT` (PyApplication→Artifact), `DECLARES_DEPENDENCY` (Artifact→Package, props `{spec, kind, extras: "string[]", prov: "string[]"}`), `LOCKS` (Artifact→Package, `{version}`), `PY_PROVIDES` (Package→PyExternal), `PY_UNRESOLVED_IMPORT` (PyApplication→PyExternal, `{prov: "string[]"}`).

- [ ] **Step 1: Write the failing test**

```python
# test/test_neo4j_artifacts.py
"""Task 6: artifact/dependency rows in the Neo4j projection."""
from pathlib import Path
from codeanalyzer.neo4j.schema import NODE_LABELS, REL_TYPES


def test_catalog_has_neutral_vocabulary():
    labels = {n.label: n for n in NODE_LABELS}
    assert labels["Artifact"].key == "id" and labels["Package"].key == "id"
    rels = {r.type for r in REL_TYPES}
    assert {"HAS_ARTIFACT", "DECLARES_DEPENDENCY", "LOCKS",
            "PY_PROVIDES", "PY_UNRESOLVED_IMPORT"} <= rels


def test_rows_projected(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\ndependencies = ["requests"]\n')
    (proj / "app.py").write_text("import requests\nimport colorama\nrequests.get('u')\n")
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions
    app = Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=2, no_venv=True, cache_dir=tmp_path / "c",
    )).analyze().application
    from codeanalyzer.neo4j.project import project
    from codeanalyzer.schema.assign_ids import build_sig_to_id
    rows = project(app, "p", build_sig_to_id(app), full_depth=True)
    nodes = {(r.label, r.key_value) for r in rows.nodes}
    assert ("Artifact", "can://artifact/p/pyproject.toml") in nodes
    assert ("Package", "pkg:pypi/requests") in nodes
    rel_types = {r.type for r in rows.rels}
    assert {"HAS_ARTIFACT", "DECLARES_DEPENDENCY", "PY_PROVIDES"} <= rel_types
```

(Adapt the two helper imports — `project(...)` signature and the sig-to-id builder name — to what `codeanalyzer/neo4j/project.py` and `codeanalyzer/schema/assign_ids.py` actually export; the existing `test/test_neo4j_*.py` files show the working invocation to copy. Row objects: use the same accessors those tests use.)

- [ ] **Step 2: Run to verify it fails** — `uv run pytest test/test_neo4j_artifacts.py -v` — FAIL (labels missing).

- [ ] **Step 3: Implement**

`schema.py` — append to `NODE_LABELS`:

```python
    NodeLabel("Artifact", "Artifact", "id", {
        "id": "string", "path": "string", "format": "string",
        "roles": "string[]", "size_bytes": "integer", "sha256": "string",
        "source": "string", "extraction": "string",
    }),
    NodeLabel("Package", "Package", "id", {
        "id": "string", "ecosystem": "string", "name": "string",
    }),
```

Append to `REL_TYPES`:

```python
    RelType("HAS_ARTIFACT", ["PyApplication"], ["Artifact"]),
    RelType("DECLARES_DEPENDENCY", ["Artifact"], ["Package"], {
        "spec": "string", "kind": "string", "extras": "string[]", "prov": "string[]",
    }),
    RelType("LOCKS", ["Artifact"], ["Package"], {"version": "string"}),
    RelType("PY_PROVIDES", ["Package"], ["PyExternal"]),
    RelType("PY_UNRESOLVED_IMPORT", ["PyApplication"], ["PyExternal"], {"prov": "string[]"}),
```

`project.py` — new step called at the end of `project(...)`, next to `_project_program_graphs`:

```python
def _project_artifacts(b, app, app_name: str) -> None:
    """Neutral artifact/package subgraph (spec 2026-08-27). Package prov of an
    import binding lands on the edge; PY_PROVIDES targets the same @external
    ghost ids the call graph merges on, so dependencies join it."""
    from codeanalyzer.schema.ids import purl_pypi

    app_key = app_name  # PyApplication merges on name
    for path in sorted(app.artifacts or {}):
        art = app.artifacts[path]
        b.node("Artifact", art.id, {
            "path": art.path, "format": art.format, "roles": art.roles,
            "size_bytes": art.size_bytes, "sha256": art.sha256,
            "source": art.source, "extraction": art.extraction,
        })
        b.rel("PyApplication", app_key, "HAS_ARTIFACT", "Artifact", art.id)
    seen_pkgs = set()
    for d in app.dependencies or []:
        pkg = purl_pypi(d.name)
        if pkg not in seen_pkgs:
            b.node("Package", pkg, {"ecosystem": "pypi", "name": d.name})
            seen_pkgs.add(pkg)
        b.rel("Artifact", d.declared_in, "DECLARES_DEPENDENCY", "Package", pkg, {
            "spec": d.spec, "kind": d.kind, "extras": d.extras, "prov": d.prov,
        })
        if d.locked_version:
            # every lock artifact that pinned it: point from each lock file present
            for lpath in sorted(app.artifacts or {}):
                if lpath.rsplit("/", 1)[-1] in ("poetry.lock", "uv.lock", "Pipfile.lock"):
                    b.rel("Artifact", app.artifacts[lpath].id, "LOCKS", "Package", pkg,
                          {"version": d.locked_version})
        for top in d.provides_imports:
            ghost = f"can://python/{app_name}/@external/{top}"
            b.merge_external(ghost, module=top)
            b.rel("Package", pkg, "PY_PROVIDES", "PyExternal", ghost)
    for u in app.unresolved_imports or []:
        ghost = f"can://python/{app_name}/@external/{u.module}"
        b.merge_external(ghost, module=u.module)
        b.rel("PyApplication", app_key, "PY_UNRESOLVED_IMPORT", "PyExternal", ghost,
              {"prov": u.prov})
```

**Adapt the `b.node`/`b.rel`/ghost-merge calls to the actual `RowBuilder` API** in `codeanalyzer/neo4j/rows.py` (read it first; `_project_program_graphs` shows the working idiom, and external ghosts are merged in `project(...)` — reuse that helper rather than inventing `merge_external` if one already exists; match the exact `can://…/@external/…` id shape used there, which may include the member name segment).

Regenerate the contract: `uv run canpy --emit schema > schema.neo4j.json`.

- [ ] **Step 4: Run tests** — `uv run pytest test/test_neo4j_artifacts.py test/ -k "neo4j" -v` — PASS (schema-conformance test must accept the regenerated contract).

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/neo4j/schema.py codeanalyzer/neo4j/project.py schema.neo4j.json test/test_neo4j_artifacts.py
git commit -m "feat(neo4j): neutral Artifact/Package subgraph joined to external ghosts"
```

---

### Task 7: End-to-end fixture, determinism, docs

**Files:**
- Create: `test/fixtures/whole_applications/manifests_app/` (pyproject.toml, requirements-dev.txt, setup.py with a computed `install_requires`, uv.lock, environment.yml, Dockerfile, docker-compose.yml, `.github/workflows/ci.yml`, `pkg/__init__.py`, `pkg/main.py` importing one declared + one undeclared package)
- Test: `test/test_artifacts_end_to_end.py` (create)
- Modify: `CHANGELOG.md` (Unreleased → Added), `README.md` (Output shape section: three new sections, one paragraph; cookbook: drop the "Landing with #157" marker)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Create the fixture** — files exactly:

`pkg/main.py`:

```python
import requests
import colorama  # deliberately undeclared

def fetch(url):
    return requests.get(url)
```

`pyproject.toml`: `[project]\nname = "manifests-app"\ndependencies = ["requests>=2.31"]\n`
`requirements-dev.txt`: `pytest>=8\n`
`setup.py`: `from setuptools import setup\nextra = compute_extras()\nsetup(install_requires=extra)\n`
`uv.lock`: `[[package]]\nname = "requests"\nversion = "2.32.3"\n`
`environment.yml`: `dependencies:\n  - numpy=1.26\n`
`Dockerfile`: `FROM python:3.12-slim\n`
`docker-compose.yml`: `services:\n  web:\n    build: .\n`
`.github/workflows/ci.yml`: `on: push\njobs: {}\n`
`pkg/__init__.py`: empty.

- [ ] **Step 2: Write the failing test**

```python
# test/test_artifacts_end_to_end.py
"""Task 7: full pipeline over the manifests_app fixture + determinism."""
import json
from pathlib import Path
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json

FIXTURE = Path(__file__).parent / "fixtures" / "whole_applications" / "manifests_app"


def _app(tmp_path, tag):
    return Codeanalyzer(AnalysisOptions(
        input=FIXTURE, analysis_level=1, no_venv=True, cache_dir=tmp_path / tag,
    )).analyze().application


def test_full_surface(tmp_path):
    app = _app(tmp_path, "a")
    arts = app.artifacts
    assert {"pyproject.toml", "requirements-dev.txt", "setup.py", "uv.lock",
            "environment.yml", "Dockerfile", "docker-compose.yml",
            ".github/workflows/ci.yml"} <= set(arts)
    assert arts["setup.py"].extraction == "partial"      # computed install_requires
    assert arts["pyproject.toml"].extraction == "full"
    deps = {d.name: d for d in app.dependencies}
    assert deps["requests"].locked_version == "2.32.3"
    assert deps["requests"].prov == ["declared", "lockfile"]
    assert deps["pytest"].kind == "dev"
    assert deps["numpy"].spec == "=1.26"
    assert [u.module for u in app.unresolved_imports] == ["colorama"]


def test_determinism_two_runs(tmp_path):
    a = model_dump_json(_app(tmp_path, "r1"))
    b = model_dump_json(_app(tmp_path, "r2"))
    assert a == b
```

- [ ] **Step 3: Run to verify current state** — first run FAILs only if earlier tasks missed something; otherwise both PASS immediately (this task's value is the fixture + gate).

- [ ] **Step 4: Full suite + docs**

Run: `uv run pytest test/ -q` — all green.
`CHANGELOG.md` under a new `## [Unreleased]` → `### Added`:

```markdown
- Schema v2 now captures non-code artifacts (`application.artifacts`), declared
  dependencies with provenance (`application.dependencies`), and undeclared
  imports (`application.unresolved_imports`) at every analysis level (#157).
  Neo4j gains language-neutral `:Artifact`/`:Package` nodes (purl ids) joined
  to the existing `:PyExternal` ghosts. New flag: `--resolve-installed`.
```

README: in **Output shape**, add a short paragraph naming the three sections and the `can://artifact/` namespace; in the cookbook, delete the "Landing with #157 … not in the current release" sentence (queries now real).

- [ ] **Step 5: Commit**

```bash
git add test/fixtures/whole_applications/manifests_app test/test_artifacts_end_to_end.py CHANGELOG.md README.md
git commit -m "test(artifacts): manifests_app fixture, end-to-end + determinism gates; docs"
```
