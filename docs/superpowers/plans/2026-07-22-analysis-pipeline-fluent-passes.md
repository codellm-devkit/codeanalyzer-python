# Analysis Pipeline (Fluent Passes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `Codeanalyzer.analyze()` from a ~130-line procedural method with scattered `if analysis_level >= N` gates into a fluent `AnalysisPipeline` of four subsystem-grouped passes over a shared `AnalysisContext`, with no change to emitted output.

**Architecture:** A new `codeanalyzer/pipeline/` package holds an `AnalysisContext` dataclass (the carrier threaded through the chain), four `_pass_*(ctx)` functions (symbol table → call graph → intraprocedural dataflow → interprocedural dataflow), and an `AnalysisPipeline` whose `.with_*()` methods self-gate on an intrinsic `min_level` via one shared runner. `Codeanalyzer` keeps the venv/cache lifecycle and delegates the build to the pipeline. Three helper methods move off `Codeanalyzer` into the package so passes have no hidden dependency on the engine instance.

**Tech Stack:** Python 3.9+, Pydantic v2 schema models, Typer CLI, Ray (optional parallelism), pytest, jedi/PyCG/tree-sitter, optional `python-scalpel`.

## Global Constraints

- **Conventional Commits** for every commit (`type(scope): summary`).
- **Never add AI/Claude authorship** anywhere — no `Co-Authored-By`, no "Generated with", no 🤖 trailer, in commits, PRs, code, or docs.
- **Behavior-preserving refactor:** emitted `analysis.json` and `graph.cypher` must be unchanged at every level. `schema_version` stays `2.0.0`.
- **Monotonicity invariant** `L1 ⊆ L2 ⊆ L3 ⊆ L4` must continue to hold (existing CI gate `test/test_v2_superset.py`).
- **Determinism** is already guaranteed by `PYTHONHASHSEED=0` (re-exec in `__main__._pin_hash_seed`) and the sorted call graph — do not break it.
- **No new dependencies.** `python-scalpel` remains an optional soft dependency (absent → type-based fallback, never a hard failure).
- **No circular imports:** the `pipeline` package may import from `schema/`, `semantic_analysis/`, `syntactic_analysis/`, `dataflow/`, `provenance`, `options`, `utils` — but **never** from `core`. `core` imports from `pipeline`.

## File Structure

- Create `codeanalyzer/pipeline/__init__.py` — re-exports `AnalysisContext`, `AnalysisPipeline`.
- Create `codeanalyzer/pipeline/context.py` — the `AnalysisContext` dataclass.
- Create `codeanalyzer/pipeline/symbol_table.py` — `build_symbol_table` + Ray helpers (`_ensure_ray`, `_process_file_with_ray`) + `_file_unchanged`, all moved verbatim from `core.py`.
- Create `codeanalyzer/pipeline/passes.py` — `home_external_symbols`, `pycg_call_graph_edges`, and the four `_pass_*` functions.
- Create `codeanalyzer/pipeline/pipeline.py` — the `AnalysisPipeline` fluent class.
- Modify `codeanalyzer/core.py` — slim `analyze()`; move the three helpers out; delete dead code.
- Modify `test/test_env_interpreter.py:151` — call the relocated `build_symbol_table`.
- Create `test/test_pipeline.py` — unit tests for the pipeline mechanics.
- Create `test/test_pipeline_equivalence.py` + `test/golden/pipeline_equivalence/*.json` — byte-for-byte characterization gate.

---

### Task 1: Characterization golden pin (against current code)

Locks current output BEFORE any refactor. Generated from the unmodified `analyze()`, committed, and asserted unchanged for the rest of the plan.

**Files:**
- Create: `test/test_pipeline_equivalence.py`
- Create: `test/golden/pipeline_equivalence/*.json` (generated)

**Interfaces:**
- Produces: nothing consumed by later tasks — it is a standalone regression gate that must stay green through Task 7.

- [ ] **Step 1: Write the equivalence test**

Create `test/test_pipeline_equivalence.py`:

```python
"""Byte-for-byte characterization gate for the analysis pipeline refactor.

Runs the CLI on copies of fixtures placed OUTSIDE any git tree (so
`repository_info` returns None and the output is deterministic), normalizes the
one volatile field (`analyzer.version`), and compares against committed goldens.
Regenerate with `REGEN=1 pytest test/test_pipeline_equivalence.py`.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = [
    "class_hierarchy",
    "decorators_and_hof",
    "async_patterns",
    "method_call_resolution",
]
GOLDEN_DIR = Path(__file__).parent / "golden" / "pipeline_equivalence"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "single_functionalities"


def _scalpel_available() -> bool:
    try:
        import scalpel  # noqa: F401
        return True
    except Exception:
        return False


def _normalize(payload: dict) -> dict:
    """Drop the only environment-volatile fields so the gate is stable across
    version bumps and non-git run locations."""
    payload.get("application", {}).pop("repository", None)
    analyzer = payload.get("analyzer")
    if isinstance(analyzer, dict):
        analyzer.pop("version", None)
    return payload


def _run(proj: Path, level: int) -> dict:
    out = subprocess.run(
        [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", str(level), "--no-venv"],
        capture_output=True, text=True, check=True,
    ).stdout
    return _normalize(json.loads(out))


@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_pipeline_output_matches_golden(tmp_path, fixture, level):
    if level == 4 and not _scalpel_available():
        pytest.skip("L4 golden requires python-scalpel (optional soft dependency)")
    proj = tmp_path / fixture
    shutil.copytree(FIXTURE_ROOT / fixture, proj)
    got = _run(proj, level)
    golden_path = GOLDEN_DIR / f"{fixture}.a{level}.json"
    if os.environ.get("REGEN"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(got, indent=2, sort_keys=True), encoding="utf-8")
        return
    assert golden_path.exists(), f"missing golden {golden_path}; regenerate with REGEN=1"
    want = _normalize(json.loads(golden_path.read_text(encoding="utf-8")))
    assert got == want, f"{fixture} @ -a {level} diverged from golden"
```

- [ ] **Step 2: Generate the goldens from current code**

Run: `REGEN=1 python -m pytest test/test_pipeline_equivalence.py -q`
Expected: PASS (regen branch returns before asserting); `test/golden/pipeline_equivalence/` now holds `*.a1.json … *.a4.json` (a4 files only if scalpel is installed).

- [ ] **Step 3: Verify the gate passes against current code without REGEN**

Run: `python -m pytest test/test_pipeline_equivalence.py -q`
Expected: PASS (a4 cases PASS or SKIP depending on scalpel).

- [ ] **Step 4: Commit**

```bash
git add test/test_pipeline_equivalence.py test/golden/pipeline_equivalence
git commit -m "test(pipeline): pin current analysis output as characterization goldens"
```

---

### Task 2: `AnalysisContext` dataclass

**Files:**
- Create: `codeanalyzer/pipeline/__init__.py`
- Create: `codeanalyzer/pipeline/context.py`
- Test: `test/test_pipeline.py`

**Interfaces:**
- Produces: `AnalysisContext(options, project_dir, virtualenv, analysis_level, app_name, cached_symbol_table={})` with mutable produced fields `symbol_table`, `app`, `sig_to_id`, `infos`, `ir` (all default `None`, except `cached_symbol_table` defaults to `{}`).

- [ ] **Step 1: Write the failing test**

Create `test/test_pipeline.py`:

```python
from pathlib import Path

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.config import OutputFormat
from codeanalyzer.pipeline import AnalysisContext


def _opts(tmp_path):
    return AnalysisOptions(
        input=tmp_path, output=None, format=OutputFormat.JSON,
        analysis_level=1, skip_tests=True, no_venv=True,
    )


def test_context_defaults(tmp_path):
    ctx = AnalysisContext(
        options=_opts(tmp_path), project_dir=Path(tmp_path),
        virtualenv=None, analysis_level=1, app_name="proj",
    )
    assert ctx.cached_symbol_table == {}
    assert ctx.symbol_table is None
    assert ctx.app is None
    assert ctx.sig_to_id is None
    assert ctx.infos is None
    assert ctx.ir is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_pipeline.py::test_context_defaults -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeanalyzer.pipeline'`.

- [ ] **Step 3: Write the context and package exports**

Create `codeanalyzer/pipeline/context.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import PyApplication, PyModule


@dataclass
class AnalysisContext:
    """Mutable carrier threaded through the AnalysisPipeline passes.

    Inputs are set at construction; produced artifacts start as ``None`` and are
    filled in chain order: ``symbol_table`` -> ``app``/``sig_to_id`` ->
    ``infos`` -> ``ir``. ``infos`` (L3 PDGs) is deliberately reused by the L4
    pass, so its ordering in the chain matters.
    """

    # inputs
    options: AnalysisOptions
    project_dir: Path
    virtualenv: Optional[Path]
    analysis_level: int
    app_name: str
    cached_symbol_table: Dict[str, PyModule] = field(default_factory=dict)

    # produced by passes (loosely typed to avoid importing dataflow types here)
    symbol_table: Optional[Dict[str, PyModule]] = None
    app: Optional[PyApplication] = None
    sig_to_id: Optional[Dict[str, str]] = None
    infos: Optional[Dict[str, Any]] = None   # Dict[str, FunctionInfo]
    ir: Optional[Any] = None                 # ProgramGraphsIR
```

Create `codeanalyzer/pipeline/__init__.py`:

```python
from codeanalyzer.pipeline.context import AnalysisContext
from codeanalyzer.pipeline.pipeline import AnalysisPipeline

__all__ = ["AnalysisContext", "AnalysisPipeline"]
```

> Note: `__init__.py` imports `AnalysisPipeline` (created in Task 6). Until then this import fails, so for Task 2 temporarily export only `AnalysisContext`:
>
> ```python
> from codeanalyzer.pipeline.context import AnalysisContext
>
> __all__ = ["AnalysisContext"]
> ```
>
> Task 6 restores the full `__init__.py` shown above.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test/test_pipeline.py::test_context_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/pipeline/__init__.py codeanalyzer/pipeline/context.py test/test_pipeline.py
git commit -m "feat(pipeline): add AnalysisContext carrier for the pass chain"
```

---

### Task 3: Move the symbol-table builder out of `Codeanalyzer`

Extract the 140-line `_build_symbol_table` (and its Ray helpers and `_file_unchanged`) into `pipeline/symbol_table.py` as free functions. `Codeanalyzer._build_symbol_table` becomes a one-line delegator so the existing `analyze()` and `test_env_interpreter.py` stay green.

**Files:**
- Create: `codeanalyzer/pipeline/symbol_table.py`
- Modify: `codeanalyzer/core.py` (remove `_ensure_ray`, `_process_file_with_ray`, `_file_unchanged`; make `_build_symbol_table` delegate; drop `import ray`)
- Test: `test/test_pipeline.py`

**Interfaces:**
- Produces: `build_symbol_table(project_dir: Path, virtualenv: Optional[Path], options: AnalysisOptions, cached_symbol_table: Dict[str, PyModule]) -> Dict[str, PyModule]`

- [ ] **Step 1: Write the failing test**

Append to `test/test_pipeline.py`:

```python
from codeanalyzer.pipeline.symbol_table import build_symbol_table


def test_build_symbol_table_free_function(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    opts = AnalysisOptions(
        input=proj, output=None, format=OutputFormat.JSON,
        analysis_level=1, skip_tests=True, no_venv=True,
    )
    table = build_symbol_table(proj, None, opts, cached_symbol_table={})
    assert "m.py" in table
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_pipeline.py::test_build_symbol_table_free_function -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeanalyzer.pipeline.symbol_table'`.

- [ ] **Step 3: Create `pipeline/symbol_table.py` by moving code from `core.py`**

Move `_ensure_ray` (core.py:40-53), `_process_file_with_ray` (core.py:56-77), the body of `_build_symbol_table` (core.py:802-938), and `_file_unchanged` (core.py:758-785) into a new `codeanalyzer/pipeline/symbol_table.py`. Apply this substitution table to the moved `_build_symbol_table` body:

| In `core._build_symbol_table` | In `build_symbol_table` |
| --- | --- |
| `self.file_name` | `options.file_name` |
| `self.project_dir` | `project_dir` |
| `self.virtualenv` | `virtualenv` |
| `self.rebuild_analysis` | `options.rebuild_analysis` |
| `self.skip_tests` | `options.skip_tests` |
| `self.using_ray` | `options.using_ray` |
| `self._file_unchanged(a, b)` | `_file_unchanged(a, b)` |

`_ensure_ray` and `_process_file_with_ray` move verbatim (no `self`). `_file_unchanged` moves with its signature changing from `(self, file_path, cached_module)` to `(file_path, cached_module)`.

The new module header and function signature:

```python
import time
from pathlib import Path
from typing import Dict, Optional, Union

import ray

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import PyModule
from codeanalyzer.syntactic_analysis.exceptions import SymbolTableBuilderRayError
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder
from codeanalyzer.utils import ProgressBar, logger


def _ensure_ray() -> None:
    ...  # moved verbatim from core.py:40-53


@ray.remote
def _process_file_with_ray(py_file, project_dir, virtualenv) -> Dict[str, PyModule]:
    ...  # moved verbatim from core.py:56-77


def _file_unchanged(file_path: Path, cached_module: PyModule) -> bool:
    ...  # moved from core.py:758-785, `self.` prefix removed


def build_symbol_table(
    project_dir: Path,
    virtualenv: Optional[Path],
    options: AnalysisOptions,
    cached_symbol_table: Optional[Dict[str, PyModule]] = None,
) -> Dict[str, PyModule]:
    """Build the symbol table for the project (moved from Codeanalyzer)."""
    if cached_symbol_table is None:
        cached_symbol_table = {}
    ...  # moved body from core.py:815-938 with the substitution table applied
```

- [ ] **Step 4: Replace `core._build_symbol_table` with a delegator and delete the moved helpers**

In `codeanalyzer/core.py`: delete the module-level `_ensure_ray` and `_process_file_with_ray`; delete the `_file_unchanged` method; delete `import ray` (now unused in core); replace the whole `_build_symbol_table` method body with:

```python
def _build_symbol_table(self, cached_symbol_table=None):
    from codeanalyzer.pipeline.symbol_table import build_symbol_table
    return build_symbol_table(
        self.project_dir, self.virtualenv, self.options, cached_symbol_table or {}
    )
```

- [ ] **Step 5: Run the moved-code and regression tests**

Run: `python -m pytest test/test_pipeline.py::test_build_symbol_table_free_function test/test_symbol_table_builder.py test/test_env_interpreter.py test/test_pipeline_equivalence.py -q`
Expected: PASS (equivalence still green — behavior is unchanged; `test_env_interpreter` still calls `analyzer._build_symbol_table`, which now delegates).

- [ ] **Step 6: Commit**

```bash
git add codeanalyzer/pipeline/symbol_table.py codeanalyzer/core.py test/test_pipeline.py
git commit -m "refactor(pipeline): extract build_symbol_table as a free function"
```

---

### Task 4: Move the call-graph helpers out of `Codeanalyzer`

Extract `_get_pycg_call_graph` and `_home_external_symbols` into `pipeline/passes.py` as free functions; the `Codeanalyzer` methods become delegators.

**Files:**
- Create: `codeanalyzer/pipeline/passes.py`
- Modify: `codeanalyzer/core.py` (make `_get_pycg_call_graph` and `_home_external_symbols` delegate)
- Test: `test/test_pipeline.py`

**Interfaces:**
- Produces:
  - `home_external_symbols(app: PyApplication, app_id: str, sig_to_id: Dict[str, str]) -> Dict[str, PyExternalSymbol]`
  - `pycg_call_graph_edges(project_dir: Path, symbol_table, jedi_edges, options: AnalysisOptions) -> List[PyCallEdge]`

- [ ] **Step 1: Write the failing test**

Append to `test/test_pipeline.py`:

```python
from codeanalyzer.pipeline.passes import home_external_symbols
from codeanalyzer.schema import PyApplication


def test_home_external_symbols_homes_undeclared_endpoints():
    app = PyApplication.builder().symbol_table({}).call_graph([]).build()
    app.id = "can://python/proj"
    # a call edge whose endpoints are not declared callables
    from codeanalyzer.schema.py_schema import PyCallEdge
    app.call_graph = [PyCallEdge(src="a.b", dst="os.getcwd", prov=["jedi"], weight=1)]
    sig_to_id = {}
    externals = home_external_symbols(app, app.id, sig_to_id)
    assert "can://python/proj/@external/os/getcwd" in externals
    assert sig_to_id["os.getcwd"] == "can://python/proj/@external/os/getcwd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_pipeline.py::test_home_external_symbols_homes_undeclared_endpoints -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeanalyzer.pipeline.passes'`.

- [ ] **Step 3: Create `pipeline/passes.py` with the two helpers**

Create `codeanalyzer/pipeline/passes.py`:

```python
from pathlib import Path
from typing import Dict, List

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import PyApplication, PyExternalSymbol
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.semantic_analysis.pycg import PyCG, PyCGExceptions
from codeanalyzer.utils import logger


def home_external_symbols(app, app_id, sig_to_id) -> Dict[str, PyExternalSymbol]:
    """Home every call-graph endpoint that is not a declared callable onto a
    ``can://…/@external/<module>/<name>`` id. Moved verbatim from
    ``Codeanalyzer._home_external_symbols`` (static method, no ``self``)."""
    externals: Dict[str, PyExternalSymbol] = {}
    for edge in app.call_graph:
        for sig in (edge.src, edge.dst):
            if sig in sig_to_id:
                continue
            module, name = sig.rsplit(".", 1) if "." in sig else (None, sig)
            ext_id = f"{app_id}/@external/{module}/{name}" if module else \
                f"{app_id}/@external/{name}"
            sig_to_id[sig] = ext_id
            externals[ext_id] = PyExternalSymbol(id=ext_id, name=name, module=module)
    return externals


def pycg_call_graph_edges(project_dir, symbol_table, jedi_edges, options) -> List[PyCallEdge]:
    """Build PyCG-resolved call edges, degrading to Jedi-only on failure. Moved
    from ``Codeanalyzer._get_pycg_call_graph`` (``self.X`` -> ``options.X`` /
    ``project_dir``)."""
    try:
        pycg = PyCG(
            project_dir,
            skip_tests=options.skip_tests,
            shard=options.pycg_shard,
            shard_ceiling=options.pycg_shard_ceiling,
            shard_timeout=options.pycg_shard_timeout,
            shard_strategy=options.pycg_shard_strategy,
            max_iter=options.pycg_max_iter,
            using_ray=options.using_ray,
        )
        return pycg.build_call_graph_edges(symbol_table, jedi_edges=jedi_edges)
    except PyCGExceptions.PyCGImportError as exc:
        logger.warning(f"PyCG not installed — level 2 edges will be Jedi-only: {exc}")
        return []
    except PyCGExceptions.PyCGAnalysisError as exc:
        logger.warning(f"PyCG analysis failed — level 2 edges will be Jedi-only: {exc}")
        logger.debug("PyCG full traceback:", exc_info=True)
        return []
```

- [ ] **Step 4: Make the `core.py` methods delegate**

In `codeanalyzer/core.py`, replace the bodies of `_get_pycg_call_graph` and `_home_external_symbols`:

```python
def _get_pycg_call_graph(self, symbol_table, jedi_edges):
    from codeanalyzer.pipeline.passes import pycg_call_graph_edges
    return pycg_call_graph_edges(self.project_dir, symbol_table, jedi_edges, self.options)

@staticmethod
def _home_external_symbols(app, app_id, sig_to_id):
    from codeanalyzer.pipeline.passes import home_external_symbols
    return home_external_symbols(app, app_id, sig_to_id)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest test/test_pipeline.py test/test_pipeline_equivalence.py test/test_pycg_sharding.py -q`
Expected: PASS (equivalence still green).

- [ ] **Step 6: Commit**

```bash
git add codeanalyzer/pipeline/passes.py codeanalyzer/core.py test/test_pipeline.py
git commit -m "refactor(pipeline): extract pycg + external-symbol helpers as free functions"
```

---

### Task 5: The four pass functions

Add the four `_pass_*(ctx)` functions to `pipeline/passes.py`. Each mutates the context, asserts its preconditions, and mirrors exactly the work today's `analyze()` does for that subsystem.

**Files:**
- Modify: `codeanalyzer/pipeline/passes.py`
- Test: `test/test_pipeline.py`

**Interfaces:**
- Consumes: `AnalysisContext`; `build_symbol_table` (Task 3); `home_external_symbols`, `pycg_call_graph_edges` (Task 4).
- Produces: `_pass_symbol_table(ctx)`, `_pass_call_graph(ctx)`, `_pass_intraproc_dataflow(ctx)`, `_pass_interproc_dataflow(ctx)` — each returns `None` and mutates `ctx`.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_pipeline.py`:

```python
from pathlib import Path

from codeanalyzer.pipeline import AnalysisContext
from codeanalyzer.pipeline.passes import (
    _pass_symbol_table, _pass_call_graph,
    _pass_intraproc_dataflow, _pass_interproc_dataflow,
)


def _ctx(tmp_path, level, src="def g():\n    return 1\ndef f():\n    return g()\n"):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text(src, encoding="utf-8")
    opts = AnalysisOptions(
        input=proj, output=None, format=OutputFormat.JSON,
        analysis_level=level, skip_tests=True, no_venv=True,
    )
    return AnalysisContext(
        options=opts, project_dir=proj, virtualenv=None,
        analysis_level=level, app_name="proj",
    )


def test_pass_symbol_table_populates_symbol_table(tmp_path):
    ctx = _ctx(tmp_path, 1)
    _pass_symbol_table(ctx)
    assert ctx.symbol_table is not None and "m.py" in ctx.symbol_table


def test_pass_call_graph_populates_app_and_ids(tmp_path):
    ctx = _ctx(tmp_path, 1)
    _pass_symbol_table(ctx)
    _pass_call_graph(ctx)
    assert ctx.app is not None and ctx.sig_to_id is not None
    assert ctx.app.id.startswith("can://python/proj")


def test_pass_call_graph_requires_symbol_table(tmp_path):
    ctx = _ctx(tmp_path, 1)
    with pytest.raises(AssertionError):
        _pass_call_graph(ctx)


def test_pass_interproc_requires_infos(tmp_path):
    ctx = _ctx(tmp_path, 4)
    _pass_symbol_table(ctx)
    _pass_call_graph(ctx)
    # intraproc pass deliberately skipped -> infos is still None
    with pytest.raises(AssertionError):
        _pass_interproc_dataflow(ctx)


def test_pass_intraproc_populates_infos(tmp_path):
    ctx = _ctx(tmp_path, 3, src="def g(x):\n    return x\ndef f(a):\n    b = a\n    g(b)\n    return b\n")
    _pass_symbol_table(ctx)
    _pass_call_graph(ctx)
    _pass_intraproc_dataflow(ctx)
    assert ctx.infos is not None
```

Add `import pytest` at the top of `test/test_pipeline.py` if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_pipeline.py -k "pass_" -v`
Expected: FAIL with `ImportError: cannot import name '_pass_symbol_table'`.

- [ ] **Step 3: Add the four pass functions**

Append to `codeanalyzer/pipeline/passes.py` (add these imports at the top of the file alongside the existing ones):

```python
from codeanalyzer.pipeline.context import AnalysisContext
from codeanalyzer.pipeline.symbol_table import build_symbol_table
from codeanalyzer.provenance import repository_info
from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.schema.call_graph_ids import reidentify_call_graph
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.l2_callees import backfill_callees
from codeanalyzer.semantic_analysis.call_graph import (
    filter_external_edges, jedi_call_graph_edges, merge_edges,
    resolve_unresolved_constructors,
)
from codeanalyzer.syntactic_analysis.import_resolver import resolve_imports
```

Then the pass functions:

```python
def _pass_symbol_table(ctx: AnalysisContext) -> None:
    symbol_table = build_symbol_table(
        ctx.project_dir, ctx.virtualenv, ctx.options, ctx.cached_symbol_table
    )
    resolve_unresolved_constructors(symbol_table)
    ctx.symbol_table = symbol_table


def _pass_call_graph(ctx: AnalysisContext) -> None:
    assert ctx.symbol_table is not None, "call_graph pass requires the symbol-table pass"
    st = ctx.symbol_table

    call_graph = list(jedi_call_graph_edges(st))
    if ctx.analysis_level >= 2:
        pycg_edges = pycg_call_graph_edges(
            ctx.project_dir, st, call_graph, ctx.options
        )
        call_graph = merge_edges(call_graph, pycg_edges)
    call_graph = filter_external_edges(call_graph, st)
    call_graph.sort(key=lambda e: (e.src, e.dst))

    app = PyApplication.builder().symbol_table(st).call_graph(call_graph).build()
    resolve_imports(app, ctx.project_dir)
    app.repository = repository_info(ctx.project_dir)

    sig_to_id = assign_ids(app, ctx.app_name)
    app.external_symbols = home_external_symbols(app, app.id, sig_to_id)
    populate_l1_body(app)
    if ctx.analysis_level >= 2:
        backfill_callees(app, sig_to_id)
    reidentify_call_graph(app, sig_to_id)

    ctx.app = app
    ctx.sig_to_id = sig_to_id


def _pass_intraproc_dataflow(ctx: AnalysisContext) -> None:
    assert ctx.app is not None and ctx.sig_to_id is not None, \
        "intraproc dataflow pass requires the call-graph pass"
    from codeanalyzer.dataflow.builder import build_function_pdgs, emit_l3_body
    from codeanalyzer.dataflow.syntactic import SyntacticOracle

    infos, _func_asts = build_function_pdgs(
        ctx.app,
        k=ctx.options.graph_field_depth,
        oracle_factory=lambda c, fast: SyntacticOracle(),
    )
    emit_l3_body(ctx.app, infos, ctx.sig_to_id, set(ctx.options.graphs.split(",")))
    ctx.infos = infos


def _pass_interproc_dataflow(ctx: AnalysisContext) -> None:
    assert ctx.app is not None and ctx.sig_to_id is not None, \
        "interproc dataflow pass requires the call-graph pass"
    assert ctx.infos is not None, \
        "interproc dataflow pass requires the intraproc pass (it reuses its PDGs)"
    from codeanalyzer.dataflow.builder import (
        _base_types, build_program_graphs, emit_ddg_pointsto_delta, emit_l4,
    )
    from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle

    ir = build_program_graphs(
        ctx.app,
        k=ctx.options.graph_field_depth,
        oracle_factory=lambda c, fast: make_alias_oracle(c, fast, _base_types(c)),
    )
    emit_l4(ctx.app, ir, ctx.sig_to_id)
    emit_ddg_pointsto_delta(ctx.app, ctx.infos, ir, ctx.sig_to_id)
    ctx.ir = ir
```

> Note: `jedi_call_graph_edges` returns the L1 edge list; today's `analyze()` passes those same Jedi edges into PyCG as the coupling graph (`jedi_edges=call_graph` here, before PyCG merges into `call_graph`). Passing the pre-merge `call_graph` list preserves that exactly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_pipeline.py -k "pass_" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/pipeline/passes.py test/test_pipeline.py
git commit -m "feat(pipeline): add the four analysis pass functions"
```

---

### Task 6: The `AnalysisPipeline` fluent class

**Files:**
- Create: `codeanalyzer/pipeline/pipeline.py`
- Modify: `codeanalyzer/pipeline/__init__.py` (restore the full export)
- Test: `test/test_pipeline.py`

**Interfaces:**
- Consumes: the four `_pass_*` functions (Task 5), `AnalysisContext` (Task 2), `analyzer_info` (`provenance`), `Analysis` (`schema`).
- Produces: `AnalysisPipeline(ctx)` with `.with_symbol_table()`, `.with_call_graph()`, `.with_intraproc_dataflow()`, `.with_interproc_dataflow()` (each returns `self`) and `.build() -> Analysis`.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_pipeline.py`:

```python
from codeanalyzer.pipeline import AnalysisPipeline
from codeanalyzer.schema import Analysis


def test_pipeline_gating_skips_dataflow_below_level(tmp_path):
    ctx = _ctx(tmp_path, 2)
    analysis = (
        AnalysisPipeline(ctx)
        .with_symbol_table()
        .with_call_graph()
        .with_intraproc_dataflow()   # min_level 3 -> skipped at level 2
        .with_interproc_dataflow()   # min_level 4 -> skipped at level 2
        .build()
    )
    assert isinstance(analysis, Analysis)
    assert ctx.infos is None and ctx.ir is None            # gates fired
    assert analysis.max_level == 2
    assert analysis.k_limit is None                         # L3+ only


def test_pipeline_with_methods_return_self(tmp_path):
    ctx = _ctx(tmp_path, 1)
    pipe = AnalysisPipeline(ctx)
    assert pipe.with_symbol_table() is pipe


def test_pipeline_level4_runs_intraproc_before_interproc(tmp_path):
    ctx = _ctx(tmp_path, 4, src="def g(x):\n    return x\ndef f(a):\n    b = g(a)\n    return b\n")
    (AnalysisPipeline(ctx)
        .with_symbol_table().with_call_graph()
        .with_intraproc_dataflow().with_interproc_dataflow().build())
    assert ctx.infos is not None    # L3 ran before L4 (reuse precondition held)
    assert ctx.ir is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_pipeline.py -k pipeline -v`
Expected: FAIL with `ImportError: cannot import name 'AnalysisPipeline'`.

- [ ] **Step 3: Write the pipeline and restore the package export**

Create `codeanalyzer/pipeline/pipeline.py`:

```python
import time

from codeanalyzer.pipeline.context import AnalysisContext
from codeanalyzer.pipeline.passes import (
    _pass_call_graph, _pass_interproc_dataflow,
    _pass_intraproc_dataflow, _pass_symbol_table,
)
from codeanalyzer.provenance import analyzer_info
from codeanalyzer.schema import Analysis
from codeanalyzer.utils import logger


class AnalysisPipeline:
    """Fluent chain of analysis passes over a shared AnalysisContext.

    Each ``.with_*`` runs one pass through the shared ``_run`` gate: a pass
    below its intrinsic ``min_level`` is a logged no-op. ``.build()`` assembles
    the ``Analysis`` envelope from the produced context.
    """

    def __init__(self, ctx: AnalysisContext):
        self.ctx = ctx

    def with_symbol_table(self):
        return self._run("symbol_table", 1, _pass_symbol_table)

    def with_call_graph(self):
        return self._run("call_graph", 1, _pass_call_graph)

    def with_intraproc_dataflow(self):
        return self._run("intraproc_dataflow", 3, _pass_intraproc_dataflow)

    def with_interproc_dataflow(self):
        return self._run("interproc_dataflow", 4, _pass_interproc_dataflow)

    def _run(self, name, min_level, fn):
        if self.ctx.analysis_level < min_level:
            logger.info("⏭️  %s: skipped (level %d < %d)", name,
                        self.ctx.analysis_level, min_level)
            return self
        t0 = time.perf_counter()
        fn(self.ctx)
        logger.info("✅ %s: %.1fs", name, time.perf_counter() - t0)
        return self

    def build(self) -> Analysis:
        return Analysis(
            max_level=self.ctx.analysis_level,
            k_limit=self.ctx.options.graph_field_depth
            if self.ctx.analysis_level >= 3 else None,
            analyzer=analyzer_info(self.ctx.analysis_level),
            application=self.ctx.app,
        )
```

Restore `codeanalyzer/pipeline/__init__.py` to the full export:

```python
from codeanalyzer.pipeline.context import AnalysisContext
from codeanalyzer.pipeline.pipeline import AnalysisPipeline

__all__ = ["AnalysisContext", "AnalysisPipeline"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_pipeline.py -q`
Expected: PASS (all pipeline unit tests green).

- [ ] **Step 5: Commit**

```bash
git add codeanalyzer/pipeline/pipeline.py codeanalyzer/pipeline/__init__.py test/test_pipeline.py
git commit -m "feat(pipeline): add the fluent AnalysisPipeline with self-gating passes"
```

---

### Task 7: Flip `Codeanalyzer.analyze()` to the pipeline and remove dead code

The behavior-preserving swap. `analyze()` becomes a thin wrapper; the three delegating methods are deleted; the one direct test consumer is updated. The equivalence goldens and the full suite are the gate.

**Files:**
- Modify: `codeanalyzer/core.py` (rewrite `analyze()`; delete `_build_symbol_table`, `_get_pycg_call_graph`, `_home_external_symbols`; adjust imports)
- Modify: `test/test_env_interpreter.py:151`
- Modify: `codeanalyzer/dataflow/scalpel_oracle.py` and `codeanalyzer/semantic_analysis/pycg/pycg_analysis.py` (prose comment references only)

**Interfaces:**
- Consumes: `AnalysisContext`, `AnalysisPipeline` from `codeanalyzer.pipeline`.
- Produces: unchanged `Codeanalyzer.analyze() -> Analysis`.

- [ ] **Step 1: Rewrite `analyze()`**

In `codeanalyzer/core.py`, add near the top-level imports:

```python
from codeanalyzer.pipeline import AnalysisContext, AnalysisPipeline
```

Replace the entire `analyze()` method (core.py:560-691) with:

```python
def analyze(self) -> Analysis:
    """Analyze the project and return the v2 ``Analysis`` envelope.

    Loads any cache seed, runs the fluent AnalysisPipeline, and persists the
    result. The per-level work lives in the pipeline passes.
    """
    cache_file = self.cache_dir / "analysis_cache.json"

    cached = None
    if not self.rebuild_analysis and cache_file.exists():
        try:
            cached = self._load_pyapplication_from_cache(cache_file)
            if cached is not None:
                logger.info("Loaded cached analysis")
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}. Rebuilding analysis.")
            cached = None

    if not self._cache_analyzer_matches(cached, analyzer_info(self.analysis_level).version):
        if cached is not None:
            logger.info("Analysis cache written by a different analyzer version; rebuilding.")
        cached = None

    ctx = AnalysisContext(
        options=self.options,
        project_dir=self.project_dir,
        virtualenv=self.virtualenv,
        analysis_level=self.analysis_level,
        app_name=self.options.app_name or self.project_dir.name,
        cached_symbol_table=cached.application.symbol_table if cached else {},
    )

    analysis = (
        AnalysisPipeline(ctx)
        .with_symbol_table()
        .with_call_graph()
        .with_intraproc_dataflow()
        .with_interproc_dataflow()
        .build()
    )

    self._save_analysis_cache(analysis, cache_file)
    return analysis
```

- [ ] **Step 2: Delete the now-unused delegating methods and imports**

In `codeanalyzer/core.py`: delete the `_build_symbol_table`, `_get_pycg_call_graph`, and `_home_external_symbols` methods (the delegators from Tasks 3–4). Remove any imports left unused by the rewrite — check with `python -c "import ast,sys; ..."` or simply run `ruff`/`pyflakes` if available. Candidates now unused in `core.py`: `PyExternalSymbol`, `PyCallEdge`, `filter_external_edges`, `jedi_call_graph_edges`, `merge_edges`, `resolve_unresolved_constructors`, `PyCG`, `PyCGExceptions`, `resolve_imports`, `SymbolTableBuilder`, `ProgressBar`, `assign_ids`, `populate_l1_body`, `backfill_callees`, `reidentify_call_graph`, `repository_info`. Keep `analyzer_info` (used in the cache-version check), `Analysis`, `PyApplication`, `PyModule`, `model_dump_json`, `model_validate_json` (still used by cache load/save).

> Do NOT delete `_load_pyapplication_from_cache`, `_save_analysis_cache`, `_cache_analyzer_matches`, `_compute_checksum`, `__enter__`, `__exit__`, or the interpreter/venv helpers — they stay on `Codeanalyzer`.

- [ ] **Step 3: Update the one direct test consumer**

In `test/test_env_interpreter.py`, replace line 151:

```python
        table = analyzer._build_symbol_table(cached_symbol_table={})
```

with:

```python
        from codeanalyzer.pipeline.symbol_table import build_symbol_table
        table = build_symbol_table(
            analyzer.project_dir, analyzer.virtualenv, analyzer.options,
            cached_symbol_table={},
        )
```

- [ ] **Step 4: Fix the two stale prose comment references**

In `codeanalyzer/dataflow/scalpel_oracle.py:256`, change the reference `core._get_pycg_call_graph` to `pipeline.passes.pycg_call_graph_edges`. In `codeanalyzer/semantic_analysis/pycg/pycg_analysis.py:497`, change `core.py's _build_symbol_table` to `pipeline.symbol_table.build_symbol_table`. These are comments only — no behavior change.

- [ ] **Step 5: Run the full suite + equivalence gate**

Run: `python -m pytest test/ -q`
Expected: PASS. Specifically confirm green: `test_pipeline_equivalence.py` (byte-identical output), `test_v2_superset.py` (monotonicity), `test_v2_l1_body/l2/l3/l4`, `test_dataflow_*`, `test_v2_cache`, `test_v2_two_projection_agreement`, `test_neo4j_schema`, `test_cli`, `test_env_interpreter`.

- [ ] **Step 6: Verify end-to-end by running the CLI at each level**

Run:
```bash
for L in 1 2 3 4; do
  python -m codeanalyzer -i test/fixtures/single_functionalities/class_hierarchy -a $L --no-venv -vv -o /tmp/canpy_verify_a$L 2>&1 | grep -E "symbol_table|call_graph|intraproc|interproc|skipped"
done
```
Expected: level 1 logs `✅ symbol_table` and `✅ call_graph`, then `⏭️ intraproc_dataflow: skipped (level 1 < 3)` and `⏭️ interproc_dataflow: skipped (level 1 < 4)`; level 3 runs intraproc and skips interproc; level 4 runs all four. Each `/tmp/canpy_verify_a$L/analysis.json` is written.

- [ ] **Step 7: Commit**

```bash
git add codeanalyzer/core.py test/test_env_interpreter.py \
        codeanalyzer/dataflow/scalpel_oracle.py \
        codeanalyzer/semantic_analysis/pycg/pycg_analysis.py
git commit -m "refactor(core): drive analyze() through the fluent AnalysisPipeline"
```

---

## Self-Review

**1. Spec coverage:**
- Fluent `.with_*()` chain over a shared context → Tasks 2, 5, 6, 7. ✓
- Four subsystem-grouped passes → Task 5. ✓
- Self-gating on `min_level`, no `skip` arg → Task 6 (`_run`). ✓
- Internal L2 gates kept inside `_pass_call_graph` → Task 5 (`if ctx.analysis_level >= 2:`). ✓
- Caching/venv stay at the edge in `Codeanalyzer` → Task 7 (`analyze()` keeps cache load/save; `__enter__`/`__exit__` untouched). ✓
- Three helpers move off `Codeanalyzer` (callers updated, no shim) → Tasks 3, 4, 7. ✓
- Byte-identical output / monotonicity gate → Task 1 (goldens) + Task 7 Step 5. ✓
- New unit tests (`_run` gating, passes in isolation, preconditions, self-gating reproduces old gates, L3→L4 reuse ordering) → Tasks 2, 5, 6. ✓
- Helper-move regression (`test_env_interpreter.py`, prose comments) → Task 7 Steps 3–4. ✓

**2. Placeholder scan:** The `...  # moved verbatim` markers in Task 3 Step 3 are explicit relocation instructions with exact source line ranges and a substitution table, not unfinished work — the engineer copies named, line-referenced blocks. No `TBD`/`TODO`/"handle edge cases".

**3. Type consistency:** `build_symbol_table(project_dir, virtualenv, options, cached_symbol_table)` — defined Task 3, called Task 5 (`_pass_symbol_table`) and Task 7 (`test_env_interpreter`), consistent. `pycg_call_graph_edges(project_dir, symbol_table, jedi_edges, options)` — defined Task 4, called Task 5, consistent. `home_external_symbols(app, app_id, sig_to_id)` — defined Task 4, called Task 5, consistent. `_pass_*` names match between Tasks 5 and 6. `AnalysisContext` field names (`symbol_table`, `app`, `sig_to_id`, `infos`, `ir`) consistent across Tasks 2, 5, 6, 7. `min_level` values (1, 1, 3, 4) match the spec table.
