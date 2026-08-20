# Entrypoint Detection (Units 1-3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flag framework entrypoints on callables and classes from declarative rules, delivering working Flask / FastAPI / Celery / Click / DRF-decorator detection end to end.

**Architecture:** A post-pass over the built L1 symbol table appends `PyEntrypoint` records to `PyCallable.entrypoints` and `PyClass.entrypoints`; `is_entrypoint` is derived from the list. Detection is driven by a shipped `rules.yml` that users can extend. The pass is wrapped so its failure degrades to "no entrypoints", never "no analysis".

**Tech Stack:** Python 3.9+, Pydantic v2, PyYAML, Jedi, pytest.

**Spec:** `docs/design/specs/2026-08-19-entrypoint-detection-design.md`

## Global Constraints

- Entrypoints are **L1** data. They must be byte-identical at `-a 1` through `-a 4`; the monotonicity gate applies.
- `is_entrypoint` is **derived**, never authored: `len(entrypoints) > 0`.
- `confidence` is a closed set: `"declared"` | `"certain"` | `"heuristic"`.
- Matching is against `PyDecorator.qualified_name`, never the written spelling.
- The post-pass never raises. Failures land in `PyEntrypointReport.errors`.
- A malformed **user** rules file is a hard error before analysis starts.
- New schema fields are optional with defaults, so existing payloads still load.
- Follow repo conventions: tests live in `test/`, run with `uv run pytest`.

**Out of scope for this plan:** declared readers (Unit 4), the Django routing engine (Unit 5), structural passes (Unit 6). Do not add `routing:` handling; the key is parsed and ignored.

---

### Task 1: Schema — `PyEntrypoint`, `PyEntrypointReport`, carriers

**Files:**
- Modify: `codeanalyzer/schema/py_schema.py`
- Test: `test/test_entrypoint_schema.py`

**Interfaces:**
- Consumes: `Span`, `builder` decorator (already in `py_schema.py`)
- Produces: `PyEntrypoint`, `PyEntrypointReport`, `PyCallable.entrypoints`, `PyCallable.is_entrypoint`, `PyClass.entrypoints`, `PyClass.is_entrypoint`, `PyApplication.entrypoint_report`

- [ ] **Step 1: Write the failing test**

```python
# test/test_entrypoint_schema.py
from codeanalyzer.schema.py_schema import (
    PyApplication, PyCallable, PyClass, PyEntrypoint, PyEntrypointReport,
)


def test_entrypoint_record_defaults():
    e = PyEntrypoint(framework="flask", confidence="certain", rule="flask.route", ruleset="shipped")
    assert e.evidence is None and e.route is None and e.via is None
    assert e.http_methods == []


def test_callable_and_class_carry_entrypoints():
    c = PyCallable(name="f", path="a.py", signature="a.f")
    k = PyClass(name="C", signature="a.C")
    assert c.entrypoints == [] and c.is_entrypoint is False
    assert k.entrypoints == [] and k.is_entrypoint is False


def test_application_carries_a_report():
    app = PyApplication(symbol_table={})
    assert isinstance(app.entrypoint_report, PyEntrypointReport)
    assert app.entrypoint_report.frameworks_detected == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_entrypoint_schema.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'PyEntrypoint'`

- [ ] **Step 3: Write minimal implementation**

Add to `codeanalyzer/schema/py_schema.py`, immediately before `class PyCallableParameter`:

```python
@builder
class PyEntrypoint(BaseModel):
    """One way a callable or class is invoked from outside the application (#27).

    A node may hold several: two ``@app.route`` decorators, or a function that
    is both a Celery task and a CLI command. ``confidence`` lets a consumer
    threshold on evidence quality rather than inheriting this analyzer's
    judgement.
    """

    framework: str
    confidence: str = "certain"   # "declared" | "certain" | "heuristic"
    rule: str = ""                # rules.yml `id:`, or an engine name
    ruleset: str = "shipped"      # "shipped" | "user:<path>"
    evidence: Optional[str] = None
    route: Optional[str] = None
    http_methods: List[str] = []
    via: Optional[str] = None     # can:// id of the routed node dispatching here


@builder
class PyEntrypointReport(BaseModel):
    """Coverage and failure record for the entrypoint pass (#27).

    The pass under-approximates by design, so silence is its failure mode.
    This is what makes a gap visible instead of indistinguishable from
    "this project has no entrypoints".
    """

    frameworks_detected: List[str] = []
    rulesets: List[str] = []
    unresolved: Dict[str, int] = {}
    errors: List[str] = []
```

Then add to `PyCallable` (after `decorators`) and to `PyClass` (after `decorators`):

```python
    entrypoints: List[PyEntrypoint] = []
    is_entrypoint: bool = False
```

And to `PyApplication` (after `external_symbols`):

```python
    entrypoint_report: PyEntrypointReport = PyEntrypointReport()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_entrypoint_schema.py -v --no-cov`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/schema/py_schema.py test/test_entrypoint_schema.py
git commit -m "feat(schema): PyEntrypoint records on callables and classes (#27)"
```

---

### Task 2: Pipeline skeleton — a wrapped post-pass that finds nothing

**Files:**
- Create: `codeanalyzer/entrypoints/__init__.py`
- Create: `codeanalyzer/entrypoints/pipeline.py`
- Modify: `codeanalyzer/core.py` (after `reidentify_call_graph(app, sig_to_id)`)
- Test: `test/test_entrypoint_pipeline.py`

**Interfaces:**
- Consumes: `PyApplication`, `PyEntrypointReport` from Task 1
- Produces: `detect_entrypoints(app, project_dir, rule_paths=()) -> None` — mutates `app` in place, never raises

- [ ] **Step 1: Write the failing test**

```python
# test/test_entrypoint_pipeline.py
from pathlib import Path

from codeanalyzer.entrypoints.pipeline import detect_entrypoints
from codeanalyzer.schema.py_schema import PyApplication


def test_pass_is_a_noop_on_an_empty_application(tmp_path: Path):
    app = PyApplication(symbol_table={})
    detect_entrypoints(app, tmp_path)
    assert app.entrypoint_report.errors == []


def test_pass_never_raises_and_records_the_failure(tmp_path: Path, monkeypatch):
    """A finder crash must lose flags, not the analysis."""
    import codeanalyzer.entrypoints.pipeline as p

    def boom(*a, **k):
        raise RuntimeError("finder exploded")

    monkeypatch.setattr(p, "_run_stages", boom)
    app = PyApplication(symbol_table={})
    detect_entrypoints(app, tmp_path)          # must not raise
    assert any("finder exploded" in e for e in app.entrypoint_report.errors)


def test_derives_is_entrypoint_from_the_list(tmp_path: Path):
    from codeanalyzer.schema.py_schema import PyCallable, PyEntrypoint, PyModule

    fn = PyCallable(name="f", path="a.py", signature="a.f")
    fn.entrypoints.append(
        PyEntrypoint(framework="flask", confidence="certain", rule="flask.route", ruleset="shipped")
    )
    app = PyApplication(symbol_table={"a.py": PyModule(functions={"f": fn})})
    detect_entrypoints(app, tmp_path)
    assert fn.is_entrypoint is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_entrypoint_pipeline.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeanalyzer.entrypoints'`

- [ ] **Step 3: Write minimal implementation**

`codeanalyzer/entrypoints/__init__.py`:

```python
from codeanalyzer.entrypoints.pipeline import detect_entrypoints

__all__ = ["detect_entrypoints"]
```

`codeanalyzer/entrypoints/pipeline.py`:

```python
"""Entrypoint detection: a post-pass over the built L1 symbol table (#27).

Runs AFTER the symbol table exists so every view reference resolves as a
lookup against ids that already exist. Additive metadata: a failure here
loses flags, never the analysis.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyClass
from codeanalyzer.utils import logger


def detect_entrypoints(
    app: PyApplication, project_dir: Path, rule_paths: Iterable[Path] = ()
) -> None:
    """Populate ``entrypoints`` on every callable and class, in place."""
    try:
        _run_stages(app, project_dir, tuple(rule_paths))
    except Exception as exc:  # noqa: BLE001 - additive pass must never abort analysis
        logger.warning("entrypoint detection failed: %s", exc)
        app.entrypoint_report.errors.append(str(exc))
    _derive_flags(app)


def _run_stages(app: PyApplication, project_dir: Path, rule_paths: tuple) -> None:
    """Stages 0-4. Empty until Task 5; the skeleton exists so the contract does."""
    return None


def _derive_flags(app: PyApplication) -> None:
    for node in _walk(app):
        node.is_entrypoint = bool(node.entrypoints)


def _walk(app: PyApplication) -> Iterator[object]:
    def walk_callable(c: PyCallable) -> Iterator[object]:
        yield c
        for inner in (c.callables or {}).values():
            yield from walk_callable(inner)
        for cls in (c.types or {}).values():
            yield from walk_class(cls)

    def walk_class(k: PyClass) -> Iterator[object]:
        yield k
        for m in (k.callables or {}).values():
            yield from walk_callable(m)
        for inner in (k.types or {}).values():
            yield from walk_class(inner)

    for mod in app.symbol_table.values():
        for fn in (mod.functions or {}).values():
            yield from walk_callable(fn)
        for cls in (mod.types or {}).values():
            yield from walk_class(cls)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_entrypoint_pipeline.py -v --no-cov`
Expected: 3 passed

- [ ] **Step 5: Wire into the analyzer**

In `codeanalyzer/core.py`, immediately after the line `reidentify_call_graph(app, sig_to_id)`:

```python
        # Entrypoints: a post-pass over the built L1 tree (#27). Runs at every
        # level -- entrypoints are L1 data and must not vary with -a.
        from codeanalyzer.entrypoints import detect_entrypoints

        detect_entrypoints(app, self.project_dir, self.options.entrypoint_rules)
```

In `codeanalyzer/options/options.py`, widen the typing import (it currently reads
`from typing import Optional`):

```python
from typing import Optional, Tuple
```

then add to `AnalysisOptions`:

```python
    entrypoint_rules: Tuple[Path, ...] = ()
```

- [ ] **Step 6: Run the broader suite to confirm nothing regressed**

Run: `uv run pytest test/test_cli.py -q --no-cov`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add codeanalyzer/entrypoints/ codeanalyzer/core.py codeanalyzer/options/options.py test/test_entrypoint_pipeline.py
git commit -m "feat(entrypoints): wrapped post-pass skeleton wired into the analyzer (#27)"
```

---

### Task 3: `rules.yml` — shipped file and loader

**Files:**
- Create: `codeanalyzer/entrypoints/rules.yml`
- Create: `codeanalyzer/entrypoints/rules.py`
- Modify: `pyproject.toml` (declare `pyyaml`; add package data)
- Test: `test/test_entrypoint_rules.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `load_rules(user_paths: Iterable[Path] = ()) -> RuleSet`; `RuleSet` with `.frameworks: Dict[str, Framework]`, `.rulesets: List[str]`; `Framework` with `.detect: List[str]`, `.decorators: List[DecoratorRule]`, `.bases: List[BaseRule]`; `DecoratorRule` with `.id`, `.match`, `.confidence`, `.route`, `.methods`; `BaseRule` with `.id`, `.match`, `.confidence`, `.transitive`, `.dispatch`; `RulesError` exception

**Note:** PyYAML is currently only a transitive dependency (via `ray`). It must be declared explicitly — relying on a transitive dep is exactly the failure #124 removed.

- [ ] **Step 1: Write the failing test**

```python
# test/test_entrypoint_rules.py
import pytest

from codeanalyzer.entrypoints.rules import RulesError, load_rules


def test_shipped_rules_load_and_include_flask():
    rs = load_rules()
    assert "flask" in rs.frameworks
    flask = rs.frameworks["flask"]
    assert "flask" in flask.detect
    assert any(r.id == "flask.route" for r in flask.decorators)


def test_every_shipped_rule_has_a_stable_id_and_valid_confidence():
    rs = load_rules()
    for fw in rs.frameworks.values():
        for rule in list(fw.decorators) + list(fw.bases):
            assert rule.id, "every rule needs a stable id so users can disable it"
            assert rule.confidence in {"declared", "certain", "heuristic"}


def test_malformed_user_file_raises_before_analysis(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("frameworks: [this is a list not a mapping]\n")
    with pytest.raises(RulesError):
        load_rules([bad])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_entrypoint_rules.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeanalyzer.entrypoints.rules'`

- [ ] **Step 3: Write the shipped rules file**

`codeanalyzer/entrypoints/rules.yml`:

```yaml
version: 1

frameworks:
  flask:
    detect: [flask]
    decorators:
      - id: flask.route
        match: "flask.Flask.route"
        route: {from: positional, index: 0}
        methods: {from: keyword, name: methods, default: [GET]}
      - id: flask.bp-verb
        match: "flask.Blueprint.{get,post,put,delete,patch}"
        route: {from: positional, index: 0}
        methods: {from: match_suffix}
    bases:
      - id: flask.methodview
        match: "flask.views.MethodView"
        transitive: true
        dispatch: [get, post, put, delete, patch]

  fastapi:
    detect: [fastapi]
    decorators:
      - id: fastapi.verb
        match: "fastapi.FastAPI.{get,post,put,delete,patch,head,options}"
        route: {from: positional, index: 0}
        methods: {from: match_suffix}
      - id: fastapi.router-verb
        match: "fastapi.APIRouter.{get,post,put,delete,patch}"
        route: {from: positional, index: 0}
        methods: {from: match_suffix}
      - id: fastapi.websocket
        match: "fastapi.FastAPI.websocket"
        route: {from: positional, index: 0}

  celery:
    detect: [celery]
    decorators:
      - id: celery.shared-task
        match: "celery.shared_task"
      - id: celery.task
        match: "celery.Celery.task"

  click:
    detect: [click, typer]
    decorators:
      - id: click.command
        match: "click.{command,group}"
      - id: typer.command
        match: "typer.Typer.command"

  drf:
    detect: [rest_framework]
    decorators:
      - id: drf.api-view
        match: "rest_framework.decorators.api_view"
      - id: drf.action
        match: "rest_framework.decorators.action"
    bases:
      - id: drf.apiview
        match: "rest_framework.views.APIView"
        transitive: true
        dispatch: [get, post, put, patch, delete, head, options]
      - id: drf.viewset
        match: "rest_framework.viewsets.*"
        transitive: true
        dispatch: [list, retrieve, create, update, partial_update, destroy]
```

- [ ] **Step 4: Write minimal implementation**

`codeanalyzer/entrypoints/rules.py`:

```python
"""Loading and merging of entrypoint rules (#27).

The shipped ``rules.yml`` covers known frameworks; users extend it with
``--entrypoint-rules``. User rules merge additively and may ``disable:`` a
shipped rule by id. A malformed user file is a hard error before analysis
starts -- silently skipping it would let someone ship rules they believe
are live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

_SHIPPED = Path(__file__).with_name("rules.yml")
_CONFIDENCE = {"declared", "certain", "heuristic"}


class RulesError(Exception):
    """Raised for a malformed rules file. Never swallowed."""


@dataclass
class DecoratorRule:
    id: str
    match: str
    confidence: str = "certain"
    route: Optional[Dict[str, Any]] = None
    methods: Optional[Dict[str, Any]] = None


@dataclass
class BaseRule:
    id: str
    match: str
    confidence: str = "certain"
    transitive: bool = False
    dispatch: List[str] = field(default_factory=list)


@dataclass
class Framework:
    name: str
    detect: List[str] = field(default_factory=list)
    decorators: List[DecoratorRule] = field(default_factory=list)
    bases: List[BaseRule] = field(default_factory=list)


@dataclass
class RuleSet:
    frameworks: Dict[str, Framework] = field(default_factory=dict)
    rulesets: List[str] = field(default_factory=list)


def load_rules(user_paths: Iterable[Path] = ()) -> RuleSet:
    out = RuleSet()
    _merge(out, _read(_SHIPPED), "shipped")
    for p in user_paths:
        _merge(out, _read(Path(p)), f"user:{p}")
    return out


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise RulesError(f"rules file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RulesError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError(f"{path}: top level must be a mapping")
    return data


def _merge(out: RuleSet, data: Dict[str, Any], origin: str) -> None:
    out.rulesets.append(origin)
    disabled = set(data.get("disable") or [])
    frameworks = data.get("frameworks") or {}
    if not isinstance(frameworks, dict):
        raise RulesError(f"{origin}: `frameworks` must be a mapping")

    for name, body in frameworks.items():
        if not isinstance(body, dict):
            raise RulesError(f"{origin}: framework `{name}` must be a mapping")
        fw = out.frameworks.setdefault(name, Framework(name=name))
        fw.detect = sorted(set(fw.detect) | set(body.get("detect") or []))
        for raw in body.get("decorators") or []:
            fw.decorators.append(_decorator_rule(raw, origin))
        for raw in body.get("bases") or []:
            fw.bases.append(_base_rule(raw, origin))

    for fw in out.frameworks.values():
        fw.decorators = [r for r in fw.decorators if r.id not in disabled]
        fw.bases = [r for r in fw.bases if r.id not in disabled]


def _require(raw: Dict[str, Any], key: str, origin: str) -> Any:
    if key not in raw:
        raise RulesError(f"{origin}: rule {raw!r} is missing `{key}`")
    return raw[key]


def _confidence(raw: Dict[str, Any], origin: str) -> str:
    c = raw.get("confidence", "certain")
    if c not in _CONFIDENCE:
        raise RulesError(f"{origin}: confidence must be one of {sorted(_CONFIDENCE)}, got {c!r}")
    return c


def _decorator_rule(raw: Dict[str, Any], origin: str) -> DecoratorRule:
    return DecoratorRule(
        id=_require(raw, "id", origin),
        match=_require(raw, "match", origin),
        confidence=_confidence(raw, origin),
        route=raw.get("route"),
        methods=raw.get("methods"),
    )


def _base_rule(raw: Dict[str, Any], origin: str) -> BaseRule:
    return BaseRule(
        id=_require(raw, "id", origin),
        match=_require(raw, "match", origin),
        confidence=_confidence(raw, origin),
        transitive=bool(raw.get("transitive", False)),
        dispatch=list(raw.get("dispatch") or []),
    )
```

- [ ] **Step 5: Declare the dependency and ship the data file**

In `pyproject.toml`, add to `[project].dependencies`:

```toml
    # pyyaml: the entrypoint rules pack (#27) is YAML. Declared explicitly --
    # it was previously only reachable as a transitive dep of ray.
    "pyyaml>=6.0,<7.0",
```

And ensure the data file ships — add after the `[project.optional-dependencies]` block:

```toml
[tool.setuptools.package-data]
codeanalyzer = ["entrypoints/*.yml"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv sync && uv run pytest test/test_entrypoint_rules.py -v --no-cov`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add codeanalyzer/entrypoints/rules.py codeanalyzer/entrypoints/rules.yml pyproject.toml test/test_entrypoint_rules.py
git commit -m "feat(entrypoints): rules.yml loader with shipped framework pack (#27)"
```

---

### Task 4: User rules — merge, disable, CLI flag

**Files:**
- Modify: `codeanalyzer/__main__.py`
- Test: `test/test_entrypoint_rules.py` (extend)

**Interfaces:**
- Consumes: `load_rules` from Task 3, `AnalysisOptions.entrypoint_rules` from Task 2
- Produces: `--entrypoint-rules` CLI flag, repeatable, populating `AnalysisOptions.entrypoint_rules`

- [ ] **Step 1: Write the failing test**

```python
# append to test/test_entrypoint_rules.py
def test_user_rules_merge_additively_with_shipped(tmp_path):
    extra = tmp_path / "mine.yml"
    extra.write_text(
        "version: 1\n"
        "frameworks:\n"
        "  inhouse:\n"
        "    detect: [inhouse]\n"
        "    decorators:\n"
        "      - id: inhouse.handler\n"
        "        match: 'inhouse.app.handler'\n"
    )
    rs = load_rules([extra])
    assert "flask" in rs.frameworks          # shipped survives
    assert "inhouse" in rs.frameworks        # user added
    assert rs.rulesets == ["shipped", f"user:{extra}"]


def test_user_file_can_disable_a_shipped_rule(tmp_path):
    off = tmp_path / "off.yml"
    off.write_text("version: 1\ndisable: [flask.route]\n")
    rs = load_rules([off])
    assert not any(r.id == "flask.route" for r in rs.frameworks["flask"].decorators)
    assert any(r.id == "flask.bp-verb" for r in rs.frameworks["flask"].decorators)


def test_bad_confidence_value_is_rejected(tmp_path):
    bad = tmp_path / "c.yml"
    bad.write_text(
        "version: 1\nframeworks:\n  x:\n    decorators:\n"
        "      - id: x.y\n        match: 'x.y'\n        confidence: probably\n"
    )
    with pytest.raises(RulesError):
        load_rules([bad])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_entrypoint_rules.py -v --no-cov`
Expected: the three new tests FAIL (`rulesets` empty or disable unsupported)

- [ ] **Step 3: Implementation**

The `_merge` written in Task 3 already satisfies these. If any test fails, fix `_merge` — do not change the tests.

- [ ] **Step 4: Add the CLI flag**

In `codeanalyzer/__main__.py`, add a parameter alongside the existing options:

```python
    entrypoint_rules: Annotated[
        Optional[List[Path]],
        typer.Option(
            "--entrypoint-rules",
            help="Extra entrypoint rules file (YAML). Repeatable; merges with "
            "the shipped rules. A malformed file is an error.",
        ),
    ] = None,
```

and thread it into the `AnalysisOptions(...)` construction:

```python
        entrypoint_rules=tuple(entrypoint_rules or ()),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest test/test_entrypoint_rules.py -v --no-cov && uv run canpy --help | grep entrypoint-rules`
Expected: all pass; the flag appears in help output

- [ ] **Step 6: Commit**

```bash
git add codeanalyzer/__main__.py test/test_entrypoint_rules.py
git commit -m "feat(cli): --entrypoint-rules for user rule packs (#27)"
```

---

### Task 5: Stage 0 — framework detection gate

**Files:**
- Create: `codeanalyzer/entrypoints/detect.py`
- Modify: `codeanalyzer/entrypoints/pipeline.py`
- Test: `test/test_entrypoint_detect.py`

**Interfaces:**
- Consumes: `RuleSet` from Task 3, `PyApplication` from Task 1
- Produces: `detected_frameworks(app, project_dir, ruleset) -> Set[str]`

- [ ] **Step 1: Write the failing test**

```python
# test/test_entrypoint_detect.py
from pathlib import Path

from codeanalyzer.entrypoints.detect import detected_frameworks
from codeanalyzer.entrypoints.rules import load_rules
from codeanalyzer.schema.py_schema import PyApplication, PyImport, PyModule


def _app(*modules: str) -> PyApplication:
    return PyApplication(
        symbol_table={
            "a.py": PyModule(
                imports=[PyImport(module=m, name=m.split(".")[-1]) for m in modules]
            )
        }
    )


def test_framework_detected_from_an_import(tmp_path: Path):
    got = detected_frameworks(_app("flask"), tmp_path, load_rules())
    assert "flask" in got


def test_absent_framework_is_not_detected(tmp_path: Path):
    got = detected_frameworks(_app("os"), tmp_path, load_rules())
    assert "celery" not in got


def test_manifest_entry_alone_is_sufficient(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["celery>=5"]\n'
    )
    got = detected_frameworks(_app("os"), tmp_path, load_rules())
    assert "celery" in got
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_entrypoint_detect.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeanalyzer.entrypoints.detect'`

- [ ] **Step 3: Write minimal implementation**

`codeanalyzer/entrypoints/detect.py`:

```python
"""Stage 0: which frameworks is this project actually using? (#27)

Gates every later stage, so a project without Celery never pays for Celery
rules and cannot false-positive on a locally-defined ``shared_task``. A
package counts as present if first-party source imports it OR the dependency
manifest names it -- either is sufficient, since an import may be dynamic.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Set

from codeanalyzer.entrypoints.rules import RuleSet
from codeanalyzer.schema.py_schema import PyApplication

_REQ = re.compile(r"^\s*['\"]?([A-Za-z0-9_.\-]+)")


def detected_frameworks(app: PyApplication, project_dir: Path, rules: RuleSet) -> Set[str]:
    present = _imported_packages(app) | _manifest_packages(project_dir)
    return {
        name
        for name, fw in rules.frameworks.items()
        if any(pkg in present for pkg in (fw.detect or [name]))
    }


def _imported_packages(app: PyApplication) -> Set[str]:
    out: Set[str] = set()
    for mod in app.symbol_table.values():
        for imp in mod.imports or []:
            # `from flask import Flask` puts the package in `module`, not `name`.
            # Prefer `module`; fall back to `name` for a bare `import flask`.
            spelling = (getattr(imp, "module", "") or getattr(imp, "name", "") or "")
            spelling = spelling.lstrip(".")
            if spelling:
                out.add(spelling.split(".", 1)[0])
    return out


def _manifest_packages(project_dir: Path) -> Set[str]:
    out: Set[str] = set()
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            m = _REQ.match(line)
            if m and "=" not in m.group(1):
                out.add(m.group(1).split("[", 1)[0].lower())
    requirements = project_dir / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text().splitlines():
            m = _REQ.match(line)
            if m:
                out.add(m.group(1).split("[", 1)[0].lower())
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_entrypoint_detect.py -v --no-cov`
Expected: 3 passed

- [ ] **Step 5: Wire Stage 0 into the pipeline**

Replace `_run_stages` in `codeanalyzer/entrypoints/pipeline.py`:

```python
def _run_stages(app: PyApplication, project_dir: Path, rule_paths: tuple) -> None:
    from codeanalyzer.entrypoints.detect import detected_frameworks
    from codeanalyzer.entrypoints.rules import load_rules

    rules = load_rules(rule_paths)
    app.entrypoint_report.rulesets = list(rules.rulesets)
    frameworks = detected_frameworks(app, project_dir, rules)
    app.entrypoint_report.frameworks_detected = sorted(frameworks)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest test/test_entrypoint_pipeline.py test/test_entrypoint_detect.py -v --no-cov`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add codeanalyzer/entrypoints/detect.py codeanalyzer/entrypoints/pipeline.py test/test_entrypoint_detect.py
git commit -m "feat(entrypoints): stage 0 framework detection gate (#27)"
```

---

### Task 6: Stage 3a — decorator matching

**Files:**
- Create: `codeanalyzer/entrypoints/matching.py`
- Modify: `codeanalyzer/entrypoints/pipeline.py`
- Test: `test/test_entrypoint_decorators.py`

**Interfaces:**
- Consumes: `DecoratorRule` from Task 3, `PyDecorator` (already exists from #128)
- Produces: `match_pattern(pattern: str, qualified_name: str) -> bool`; `entrypoints_from_decorators(node, framework, rules, ruleset_of) -> List[PyEntrypoint]`

- [ ] **Step 1: Write the failing test**

```python
# test/test_entrypoint_decorators.py
from codeanalyzer.entrypoints.matching import entrypoints_from_decorators, match_pattern
from codeanalyzer.entrypoints.rules import DecoratorRule
from codeanalyzer.schema.py_schema import PyCallable, PyDecorator


def test_brace_alternation_and_wildcard():
    assert match_pattern("flask.Blueprint.{get,post}", "flask.Blueprint.get")
    assert not match_pattern("flask.Blueprint.{get,post}", "flask.Blueprint.delete")
    assert match_pattern("rest_framework.viewsets.*", "rest_framework.viewsets.ModelViewSet")
    assert not match_pattern("flask.Flask.route", "flask.Flask.routes")


def test_route_and_methods_are_extracted():
    fn = PyCallable(name="h", path="a.py", signature="a.h")
    fn.decorators.append(
        PyDecorator(
            name="app.route",
            qualified_name="flask.Flask.route",
            positional_arguments=["'/products'"],
            keyword_arguments={"methods": "['POST']"},
        )
    )
    rule = DecoratorRule(
        id="flask.route",
        match="flask.Flask.route",
        route={"from": "positional", "index": 0},
        methods={"from": "keyword", "name": "methods", "default": ["GET"]},
    )
    (ep,) = entrypoints_from_decorators(fn, "flask", [rule], "shipped")
    assert ep.route == "/products"
    assert ep.http_methods == ["POST"]
    assert ep.rule == "flask.route" and ep.ruleset == "shipped"


def test_verb_comes_from_the_matched_suffix():
    fn = PyCallable(name="h", path="a.py", signature="a.h")
    fn.decorators.append(
        PyDecorator(name="router.post", qualified_name="fastapi.APIRouter.post",
                    positional_arguments=["'/x'"])
    )
    rule = DecoratorRule(
        id="fastapi.router-verb",
        match="fastapi.APIRouter.{get,post}",
        route={"from": "positional", "index": 0},
        methods={"from": "match_suffix"},
    )
    (ep,) = entrypoints_from_decorators(fn, "fastapi", [rule], "shipped")
    assert ep.http_methods == ["POST"]


def test_unresolved_decorator_never_matches():
    """qualified_name is None when Jedi could not resolve; must not guess."""
    fn = PyCallable(name="h", path="a.py", signature="a.h")
    fn.decorators.append(PyDecorator(name="app.route", qualified_name=None))
    rule = DecoratorRule(id="flask.route", match="flask.Flask.route")
    assert entrypoints_from_decorators(fn, "flask", [rule], "shipped") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_entrypoint_decorators.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeanalyzer.entrypoints.matching'`

- [ ] **Step 3: Write minimal implementation**

`codeanalyzer/entrypoints/matching.py`:

```python
"""Stage 3: match rules against decorators and base classes (#27).

Matching is on ``PyDecorator.qualified_name`` -- never the written spelling --
so ``@route`` under ``from flask import route`` hits the same rule as
``@app.route``. An unresolved decorator (``qualified_name is None``) never
matches: under-approximate rather than guess.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Dict, Iterable, List, Optional

from codeanalyzer.entrypoints.rules import DecoratorRule
from codeanalyzer.schema.py_schema import PyEntrypoint

# Dispatch names that are HTTP verbs. DRF's ViewSet dispatch names
# (list, retrieve, create, ...) are NOT verbs and must not be emitted as such.
_HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}


def match_pattern(pattern: str, qualified_name: Optional[str]) -> bool:
    """``{a,b}`` alternation and trailing ``*``; everything else is literal."""
    if not qualified_name:
        return False
    return re.fullmatch(_compile(pattern), qualified_name) is not None


def _compile(pattern: str) -> str:
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "{":
            j = pattern.index("}", i)
            alts = pattern[i + 1 : j].split(",")
            out.append("(?:" + "|".join(re.escape(a.strip()) for a in alts) + ")")
            i = j + 1
        elif ch == "*":
            out.append(r"[^\s]*")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def _literal(text: Optional[str]) -> Any:
    """Best-effort: decorator arguments are unparsed source fragments."""
    if text is None:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None


def _route_of(dec, spec: Optional[Dict[str, Any]]) -> Optional[str]:
    if not spec or spec.get("from") != "positional":
        return None
    args = dec.positional_arguments or []
    idx = int(spec.get("index", 0))
    if idx >= len(args):
        return None
    value = _literal(args[idx])
    return value if isinstance(value, str) else None


def _methods_of(dec, rule: DecoratorRule, spec: Optional[Dict[str, Any]]) -> List[str]:
    if not spec:
        return []
    source = spec.get("from")
    if source == "match_suffix":
        verb = (dec.qualified_name or "").rsplit(".", 1)[-1]
        return [verb.upper()]
    if source == "keyword":
        raw = (dec.keyword_arguments or {}).get(spec.get("name", ""))
        value = _literal(raw)
        if isinstance(value, (list, tuple)):
            return [str(v).upper() for v in value]
        return [str(v).upper() for v in (spec.get("default") or [])]
    return []


def entrypoints_from_decorators(
    node, framework: str, rules: Iterable[DecoratorRule], ruleset: str
) -> List[PyEntrypoint]:
    out: List[PyEntrypoint] = []
    for dec in getattr(node, "decorators", []) or []:
        for rule in rules:
            if not match_pattern(rule.match, dec.qualified_name):
                continue
            out.append(
                PyEntrypoint(
                    framework=framework,
                    confidence=rule.confidence,
                    rule=rule.id,
                    ruleset=ruleset,
                    evidence=dec.qualified_name,
                    route=_route_of(dec, rule.route),
                    http_methods=_methods_of(dec, rule, rule.methods),
                )
            )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_entrypoint_decorators.py -v --no-cov`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/entrypoints/matching.py test/test_entrypoint_decorators.py
git commit -m "feat(entrypoints): decorator rule matching with route extraction (#27)"
```

---

### Task 7: Stage 3b — inheritance matching and the dispatch split

**Files:**
- Modify: `codeanalyzer/entrypoints/matching.py`
- Modify: `codeanalyzer/entrypoints/pipeline.py`
- Test: `test/test_entrypoint_bases.py`

**Interfaces:**
- Consumes: `BaseRule` from Task 3, `match_pattern` from Task 6
- Produces: `entrypoints_from_bases(cls, framework, rules, ruleset, resolve) -> Tuple[List[PyEntrypoint], Dict[str, List[PyEntrypoint]]]` — class records, and per-method-name records for the dispatch split

- [ ] **Step 1: Write the failing test**

```python
# test/test_entrypoint_bases.py
from codeanalyzer.entrypoints.matching import entrypoints_from_bases
from codeanalyzer.entrypoints.rules import BaseRule
from codeanalyzer.schema.py_schema import PyCallable, PyClass

RULE = BaseRule(
    id="drf.apiview",
    match="rest_framework.views.APIView",
    transitive=True,
    dispatch=["get", "post", "put"],
)


def _cls(*methods: str, bases=("rest_framework.views.APIView",)) -> PyClass:
    return PyClass(
        name="V",
        signature="a.V",
        base_classes=list(bases),
        callables={m: PyCallable(name=m, path="a.py", signature=f"a.V.{m}") for m in methods},
    )


def test_class_is_flagged_and_only_defined_methods_dispatch():
    cls = _cls("get")                       # defines get, not post
    class_eps, method_eps = entrypoints_from_bases(cls, "drf", [RULE], "shipped", lambda b: b)
    assert len(class_eps) == 1
    assert list(method_eps) == ["get"], "no phantom post entrypoint"


def test_methods_point_back_at_the_routed_class_via():
    cls = _cls("get")
    cls.id = "can://python/app/a.py/V"
    _, method_eps = entrypoints_from_bases(cls, "drf", [RULE], "shipped", lambda b: b)
    assert method_eps["get"][0].via == "can://python/app/a.py/V"


def test_transitive_base_resolves_one_hop():
    cls = _cls("get", bases=("app.BaseView",))
    resolve = {"app.BaseView": "rest_framework.views.APIView"}.get
    class_eps, _ = entrypoints_from_bases(cls, "drf", [RULE], "shipped", resolve)
    assert len(class_eps) == 1


def test_unrelated_class_is_not_flagged():
    cls = _cls("get", bases=("object",))
    class_eps, method_eps = entrypoints_from_bases(cls, "drf", [RULE], "shipped", lambda b: b)
    assert class_eps == [] and method_eps == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_entrypoint_bases.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'entrypoints_from_bases'`

- [ ] **Step 3: Write minimal implementation**

Append to `codeanalyzer/entrypoints/matching.py`:

```python
def entrypoints_from_bases(
    cls, framework: str, rules, ruleset: str, resolve
):
    """Records for a routed class and for the methods the framework dispatches.

    ``resolve`` maps a written base-class name to its resolved qualified name
    (identity when already qualified). Dispatch names are intersected with the
    methods the class actually defines, so a ``ListView`` with only ``get``
    gains no phantom ``post`` entrypoint.
    """
    class_eps: List[PyEntrypoint] = []
    method_eps: Dict[str, List[PyEntrypoint]] = {}

    for rule in rules:
        if not any(
            match_pattern(rule.match, resolve(b) or b) for b in (cls.base_classes or [])
        ):
            continue
        class_eps.append(
            PyEntrypoint(
                framework=framework,
                confidence=rule.confidence,
                rule=rule.id,
                ruleset=ruleset,
                evidence=cls.signature,
            )
        )
        defined = set((cls.callables or {}).keys())
        for name in rule.dispatch:
            if name not in defined:
                continue
            method_eps.setdefault(name, []).append(
                PyEntrypoint(
                    framework=framework,
                    confidence=rule.confidence,
                    rule=f"{rule.id}.dispatch",
                    ruleset=ruleset,
                    evidence=cls.signature,
                    http_methods=[name.upper()] if name in _HTTP_VERBS else [],
                    via=cls.id or None,
                )
            )
    return class_eps, method_eps
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_entrypoint_bases.py -v --no-cov`
Expected: 4 passed

- [ ] **Step 5: Wire Stage 3 into the pipeline**

Extend `_run_stages` in `codeanalyzer/entrypoints/pipeline.py`, after Stage 0:

```python
    from codeanalyzer.entrypoints.matching import (
        entrypoints_from_bases,
        entrypoints_from_decorators,
    )

    ruleset_name = rules.rulesets[-1] if len(rules.rulesets) > 1 else "shipped"
    for name in sorted(frameworks):
        fw = rules.frameworks[name]
        for node in _walk(app):
            node.entrypoints.extend(
                entrypoints_from_decorators(node, name, fw.decorators, ruleset_name)
            )
            if isinstance(node, PyClass) and fw.bases:
                class_eps, method_eps = entrypoints_from_bases(
                    node, name, fw.bases, ruleset_name, lambda b: b
                )
                node.entrypoints.extend(class_eps)
                for method_name, eps in method_eps.items():
                    target = (node.callables or {}).get(method_name)
                    if target is not None:
                        target.entrypoints.extend(eps)
```

- [ ] **Step 6: Run the whole entrypoint suite**

Run: `uv run pytest test/test_entrypoint_*.py -v --no-cov`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add codeanalyzer/entrypoints/matching.py codeanalyzer/entrypoints/pipeline.py test/test_entrypoint_bases.py
git commit -m "feat(entrypoints): inheritance rules and the class/method dispatch split (#27)"
```

---

### Task 8: End-to-end fixture and Neo4j projection

**Files:**
- Create: `test/fixtures/single_functionalities/entrypoints_flask/app.py`
- Create: `test/test_entrypoints_e2e.py`
- Modify: `codeanalyzer/neo4j/schema.py`, `codeanalyzer/neo4j/project.py`
- Modify: `schema.neo4j.json` (regenerated)

**Interfaces:**
- Consumes: everything above
- Produces: `is_entrypoint` and `entrypoint_frameworks` properties on `:PyCallable` and `:PyClass`

- [ ] **Step 1: Write the fixture**

```python
# test/fixtures/single_functionalities/entrypoints_flask/app.py
from flask import Flask

app = Flask(__name__)


@app.route("/products", methods=["POST"])
def create_product():
    return helper()


def helper():
    """Called only internally - must NOT be flagged."""
    return {}
```

- [ ] **Step 2: Write the failing test**

```python
# test/test_entrypoints_e2e.py
import json
import subprocess
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "entrypoints_flask"


def test_flask_route_flagged_and_helper_not(tmp_path):
    subprocess.run(
        ["uv", "run", "canpy", "-i", str(FIXTURE), "-a", "1", "-o", str(tmp_path)],
        check=True,
    )
    data = json.loads((tmp_path / "analysis.json").read_text())
    fns = data["application"]["symbol_table"]["app.py"]["functions"]

    create = fns["create_product"]
    assert create["is_entrypoint"] is True
    (ep,) = create["entrypoints"]
    assert ep["framework"] == "flask" and ep["rule"] == "flask.route"
    assert ep["route"] == "/products" and ep["http_methods"] == ["POST"]

    assert fns["helper"]["is_entrypoint"] is False
    assert fns["helper"]["entrypoints"] == []
    assert "flask" in data["application"]["entrypoint_report"]["frameworks_detected"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest test/test_entrypoints_e2e.py -v --no-cov`
Expected: FAIL — `is_entrypoint` is False (Flask is not installed in the fixture, so Stage 0 does not gate in)

- [ ] **Step 4: Make Stage 0 pass for the fixture**

Add a manifest so detection has a source. Create `test/fixtures/single_functionalities/entrypoints_flask/requirements.txt`:

```
flask
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest test/test_entrypoints_e2e.py -v --no-cov`
Expected: PASS

- [ ] **Step 6: Project into Neo4j**

In `codeanalyzer/neo4j/schema.py`, add to both the `PyCallable` and `PyClass` property maps:

```python
            "is_entrypoint": "boolean",
            "entrypoint_frameworks": "string[]",
```

In `codeanalyzer/neo4j/project.py`, add to both `_callable_props` and `_class_props`:

```python
            "is_entrypoint": bool(node.entrypoints),
            "entrypoint_frameworks": sorted({e.framework for e in (node.entrypoints or [])}),
```

(using the local parameter name in each function — `c` in `_callable_props`, `cl` in `_class_props`).

- [ ] **Step 7: Regenerate the schema snapshot and run the suite**

```bash
uv run canpy --emit schema > schema.neo4j.json
uv run pytest test/ -q --no-cov -k "not neo4j_bolt"
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add test/fixtures/single_functionalities/entrypoints_flask test/test_entrypoints_e2e.py \
        codeanalyzer/neo4j/schema.py codeanalyzer/neo4j/project.py schema.neo4j.json
git commit -m "feat(entrypoints): end-to-end flask detection and Neo4j projection (#27)"
```
