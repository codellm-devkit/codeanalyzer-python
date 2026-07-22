# Vendored typed_ast-free Scalpel as Default L4 Oracle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor a minimal, `typed_ast`-free slice of `python-scalpel 1.0b0` into `codeanalyzer/dataflow/scalpel/` and make `ScalpelAliasOracle` the shipping-default L4 oracle on every supported Python, with no external `python-scalpel`/`typed_ast` dependency.

**Architecture:** Copy the 9-module `SSA`/`cfg`/`core` closure the oracle actually loads (verified free of `typeinfer`/`typed_ast`) into a `codeanalyzer.dataflow.scalpel` package, patched only to make `graphviz` a non-required import. Repoint `scalpel_oracle.py` at the vendored path and drop its "optional dependency absent" fallback branch, so the type-based oracle demotes to a pure runtime safety net.

**Tech Stack:** Python 3.9–3.14, `astor` (new runtime dep), `networkx` (existing), pytest, Poetry (dev env, currently 3.14.5), uv (CI + fetching the vendored source).

## Global Constraints

- Conventional Commits (`type(scope): summary`).
- NEVER add AI/Claude authorship anywhere (no `Co-Authored-By`, "Generated with", 🤖) — commits, PRs, code, docs. Absolute.
- **Apache-2.0 attribution is mandatory** for the vendored code: copy upstream `LICENSE` into the vendored dir, add a provenance `README.md`, and add a `NOTICE` entry.
- **`requires-python` stays `>=3.9`** — do NOT cap it.
- **Behavior:** the monotonicity invariant `L3 ⊆ L4` must still hold (the `prov:["ssa"]` edge set is unchanged; scalpel only sharpens the additive `prov:["points-to"]` overlay). No schema change (`schema_version` stays `2.0.0`).
- The vendored copy is **verbatim** from `python-scalpel 1.0b0` except the single documented `graphviz` patch.
- Vendor **exactly** these 9 files — no other scalpel modules (`typeinfer`, `call_graph`, `pycg`, `import_graph`, `scope_graph`, `dataflow`, `rewriter.py`, and the unused `SSA/{alg,def_use,ssa}.py` + extra `core/*.py`):
  `__init__.py`, `SSA/__init__.py`, `SSA/const.py`, `cfg/__init__.py`, `cfg/builder.py`, `cfg/model.py`, `core/__init__.py`, `core/func_call_visitor.py`, `core/vars_visitor.py`.
- Run tests with `poetry run python -m pytest` (managed env, py3.14). Do NOT use `uv run` for the dev env (it creates a stray in-project `.venv` that shadows Poetry). There must be no in-project `.venv`.

## File Structure

- Create `codeanalyzer/dataflow/scalpel/` — the 9 vendored files + `LICENSE` + `README.md`.
- Modify `codeanalyzer/dataflow/scalpel/cfg/model.py` — the one `graphviz`-lazy patch.
- Modify `codeanalyzer/dataflow/scalpel_oracle.py` — repoint imports, drop the dead ImportError branch.
- Modify `pyproject.toml` — add `astor`, remove the `[scalpel]` extra.
- Modify `NOTICE` — Scalpel Apache-2.0 attribution.
- Modify `CLAUDE.md`, `.claude/SCHEMA_DECISIONS.md`, `CHANGELOG.md` — record the reversal + behavior change.
- Create `test/test_vendored_scalpel.py` — import-hygiene, typed_ast-free, fidelity, determinism tests.

---

### Task 1: Vendor the scalpel slice + dependency + attribution

**Files:**
- Create: `codeanalyzer/dataflow/scalpel/**` (9 files + `LICENSE` + `README.md`)
- Modify: `codeanalyzer/dataflow/scalpel/cfg/model.py` (graphviz patch)
- Modify: `pyproject.toml` (add `astor`, remove `[scalpel]` extra)
- Modify: `NOTICE`
- Test: `test/test_vendored_scalpel.py`

**Interfaces:**
- Produces: importable `codeanalyzer.dataflow.scalpel.SSA.const.SSA` and `codeanalyzer.dataflow.scalpel.cfg.CFGBuilder`, functional with no external `scalpel`/`typed_ast`/`graphviz`.

- [ ] **Step 1: Write the failing import-hygiene test**

Create `test/test_vendored_scalpel.py`:

```python
"""The vendored, typed_ast-free scalpel slice: it must import and compute SSA
with no external scalpel / typed_ast / graphviz, and never pull in typeinfer."""
import sys
import importlib


def test_vendored_scalpel_imports_and_computes_ssa_typed_ast_free():
    # No external scalpel shadowing the vendored copy.
    assert "scalpel" not in sys.modules or not sys.modules["scalpel"].__file__.endswith(
        "site-packages/scalpel/__init__.py"
    ), "external python-scalpel is installed; the test must exercise the vendored copy"

    from codeanalyzer.dataflow.scalpel.cfg import CFGBuilder
    from codeanalyzer.dataflow.scalpel.SSA.const import SSA

    src = "def f(a):\n    b = a\n    c = b\n    return c\n"
    module_cfg = CFGBuilder().build_from_src("m", src)
    func_cfg = list(module_cfg.functioncfgs.values())[0]
    ssa_results, const_dict = SSA().compute_SSA(func_cfg)
    # The copy chain + return pseudo-name scalpel is known to produce.
    assert ("b", 0) in const_dict and ("c", 0) in const_dict and ("<ret>", 0) in const_dict

    # The whole point: no typed_ast, and typeinfer was never vendored/loaded.
    assert "typed_ast" not in sys.modules
    loaded = [m for m in sys.modules if m.startswith("codeanalyzer.dataflow.scalpel")]
    assert not any("typeinfer" in m for m in loaded), f"typeinfer leaked: {loaded}"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `poetry run python -m pytest test/test_vendored_scalpel.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'codeanalyzer.dataflow.scalpel'`.

- [ ] **Step 3: Vendor the 9 files + LICENSE (reproducible fetch)**

Fetch the exact upstream source and copy only the 9 files + the license:

```bash
cd /home/rkrsn/workspace/codellm-devkit/codeanalyzer-python
TMP=$(mktemp -d)
uv pip install --no-deps --target "$TMP" 'python-scalpel==1.0b0'
DST=codeanalyzer/dataflow/scalpel
mkdir -p "$DST/SSA" "$DST/cfg" "$DST/core"
cp "$TMP/scalpel/__init__.py"                    "$DST/__init__.py"
cp "$TMP/scalpel/SSA/__init__.py"                "$DST/SSA/__init__.py"
cp "$TMP/scalpel/SSA/const.py"                   "$DST/SSA/const.py"
cp "$TMP/scalpel/cfg/__init__.py"                "$DST/cfg/__init__.py"
cp "$TMP/scalpel/cfg/builder.py"                 "$DST/cfg/builder.py"
cp "$TMP/scalpel/cfg/model.py"                   "$DST/cfg/model.py"
cp "$TMP/scalpel/core/__init__.py"               "$DST/core/__init__.py"
cp "$TMP/scalpel/core/func_call_visitor.py"      "$DST/core/func_call_visitor.py"
cp "$TMP/scalpel/core/vars_visitor.py"           "$DST/core/vars_visitor.py"
cp "$TMP"/python_scalpel-1.0b0.dist-info/LICENSE "$DST/LICENSE"
rm -rf "$TMP"
```

Verify exactly 9 `.py` files landed:

```bash
find codeanalyzer/dataflow/scalpel -name '*.py' | sort
# expect the 9 listed in Global Constraints, nothing else
```

- [ ] **Step 4: Apply the single graphviz patch**

In `codeanalyzer/dataflow/scalpel/cfg/model.py`, the top-level `import graphviz as gv` (line 12) makes the module require `graphviz` at load. `graphviz` is used only by the visualization methods (`_build_visual`/`build_visual`), which codeanalyzer never calls. Replace the bare import:

```python
import graphviz as gv
```

with a guarded import so the module loads without `graphviz`:

```python
try:  # PATCH (codeanalyzer): graphviz is only used by build_visual(), which
    import graphviz as gv  # codeanalyzer never calls. Keep the module importable
except ImportError:  # without the graphviz dependency.
    gv = None
```

This is the ONLY change to any vendored file.

- [ ] **Step 5: Add attribution — `README.md` + `NOTICE` entry**

Create `codeanalyzer/dataflow/scalpel/README.md`:

```markdown
# Vendored Scalpel (typed_ast-free slice)

Vendored from **[SMAT-Lab/Scalpel](https://github.com/SMAT-Lab/Scalpel)**,
package `python-scalpel==1.0b0`, licensed **Apache-2.0** (see `LICENSE`).

## Why vendored

`python-scalpel` hard-depends on `typed_ast`, whose last release (1.5.5) has no
wheel for Python 3.12+ and fails to build from source on modern compilers — so
`pip install python-scalpel` fails on 3.12/3.13/3.14. `typed_ast` is imported by
exactly one scalpel module, `typeinfer/analysers.py`, which codeanalyzer does not
use. Vendoring the small slice the L4 may-alias oracle needs removes the
`typed_ast` dependency and makes scalpel the default oracle on every supported
Python.

## What is vendored

Exactly the 9-module closure that `scalpel.SSA.const` + `scalpel.cfg` load
(verified via `sys.modules`; provably free of `typeinfer`/`typed_ast`):

    __init__.py
    SSA/__init__.py, SSA/const.py
    cfg/__init__.py, cfg/builder.py, cfg/model.py
    core/__init__.py, core/func_call_visitor.py, core/vars_visitor.py

Copied verbatim except **one patch**: `cfg/model.py`'s top-level
`import graphviz as gv` is guarded (`try/except ImportError: gv = None`) so the
module imports without the `graphviz` package — the graphviz-using
`build_visual()` methods are unused here.

Runtime deps of this slice: `astor`, `networkx` (both core dependencies of
codeanalyzer). `typed_ast` and `graphviz` are NOT required.

To refresh: re-run the vendoring in `docs/superpowers/plans/2026-07-22-vendored-scalpel-default-oracle.md` Task 1.
```

Append to the top-level `NOTICE` (after the existing content):

```
--- Scalpel License Notice ---

This project vendors a slice of Scalpel (python-scalpel), a product of SMAT-Lab,
under codeanalyzer/dataflow/scalpel/. Scalpel is licensed under the Apache
License 2.0. The full license text is included at
codeanalyzer/dataflow/scalpel/LICENSE. Source: https://github.com/SMAT-Lab/Scalpel
```

- [ ] **Step 6: Add `astor` dep, remove the `[scalpel]` extra**

In `pyproject.toml`, add to the core `dependencies` array (after the `uv` entry), with a comment:

```toml
    # astor: runtime dependency of the vendored Scalpel SSA slice
    # (scalpel/SSA/const.py uses astor.to_source). Pure-Python, installs
    # everywhere; scalpel pins ~=0.8.1.
    "astor>=0.8.1,<0.9.0",
```

Remove the entire `[project.optional-dependencies].scalpel` group (the
`scalpel = ["python-scalpel>=1.0b0"]` block and its comment). Leave the `neo4j`
extra intact.

- [ ] **Step 7: Install the new dep into the dev env**

Run: `poetry install --with test`
Then: `poetry run python -c "import astor; print('astor', astor.__version__)"`
Expected: prints the astor version (installs on 3.14 — pure Python).

- [ ] **Step 8: Run the hygiene test — now green**

Run: `poetry run python -m pytest test/test_vendored_scalpel.py -q`
Expected: PASS (the vendored slice imports + computes SSA on 3.14, typed_ast-free).

- [ ] **Step 9: Commit**

```bash
git add codeanalyzer/dataflow/scalpel pyproject.toml NOTICE test/test_vendored_scalpel.py
git commit -m "feat(dataflow): vendor typed_ast-free scalpel SSA/cfg slice"
```

---

### Task 2: Repoint the oracle at the vendored slice and make it the default

**Files:**
- Modify: `codeanalyzer/dataflow/scalpel_oracle.py`
- Test: `test/test_vendored_scalpel.py` (append)

**Interfaces:**
- Consumes: the vendored `codeanalyzer.dataflow.scalpel.SSA.const.SSA` / `...cfg.CFGBuilder` (Task 1).
- Produces: `make_alias_oracle(pycallable, func_ast, base_types)` returns a `ScalpelAliasOracle` by default (never the type-based oracle for *absence*, only for a per-callable build failure).

- [ ] **Step 1: Write the failing "scalpel is the default" test**

Append to `test/test_vendored_scalpel.py`:

```python
import ast


def test_make_alias_oracle_defaults_to_scalpel_without_typed_ast():
    import sys
    assert "typed_ast" not in sys.modules
    from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle, ScalpelAliasOracle

    src = "def f(a):\n    b = a\n    c = b\n    return c\n"
    func_ast = ast.parse(src).body[0]
    oracle = make_alias_oracle(pycallable=None, func_ast=func_ast, base_types={})
    # The whole point: scalpel is the default, not the type-based fallback.
    assert isinstance(oracle, ScalpelAliasOracle), type(oracle).__name__
    # It answers queries (copies alias; unrelated locals do not).
    assert oracle.may_alias("b", "c") is True


def test_make_alias_oracle_is_deterministic():
    import ast as _ast
    from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle
    src = "def f(a):\n    b = a\n    c = b\n    return c\n"
    fa = _ast.parse(src).body[0]
    o1 = make_alias_oracle(None, _ast.parse(src).body[0], {})
    o2 = make_alias_oracle(None, _ast.parse(src).body[0], {})
    pairs = [("a", "b"), ("b", "c"), ("a", "c"), ("b", "b")]
    assert [o1.may_alias(x, y) for x, y in pairs] == [o2.may_alias(x, y) for x, y in pairs]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `poetry run python -m pytest test/test_vendored_scalpel.py -k make_alias_oracle -q`
Expected: FAIL — `make_alias_oracle` still imports `from scalpel.SSA.const import SSA` (external, absent on 3.14) and returns the type-based fallback, so `isinstance(..., ScalpelAliasOracle)` is False.

- [ ] **Step 3: Repoint the imports in `from_function`**

In `codeanalyzer/dataflow/scalpel_oracle.py`, in `ScalpelAliasOracle.from_function`, change:

```python
        from scalpel.SSA.const import SSA
        from scalpel.cfg import CFGBuilder
```

to:

```python
        from codeanalyzer.dataflow.scalpel.SSA.const import SSA
        from codeanalyzer.dataflow.scalpel.cfg import CFGBuilder
```

Update that method's docstring line "Imports Scalpel lazily (``ImportError`` if the optional dependency is absent)…" to "Imports the vendored Scalpel slice (``codeanalyzer.dataflow.scalpel``) and reuses the *same source*…". Also update the module docstring reference at the top of the file (`from scalpel.SSA.const import SSA`) to the vendored path.

- [ ] **Step 4: Drop the dead ImportError branch in `make_alias_oracle`**

Replace the current `make_alias_oracle` body:

```python
def make_alias_oracle(pycallable, func_ast, base_types) -> object:
    """Total selector for the L4 may-alias oracle.

    Returns a :class:`ScalpelAliasOracle` built on the vendored, typed_ast-free
    Scalpel slice (``codeanalyzer.dataflow.scalpel``) — the default L4 oracle.
    Falls back to :class:`TypeBasedAliasOracle` only when the per-callable
    Scalpel build fails on this AST. Never raises.
    """
    fallback = TypeBasedAliasOracle(base_types)
    try:
        return ScalpelAliasOracle.from_function(
            func_ast, base_types=base_types, fallback=fallback
        )
    except Exception:
        _note_fallback("scalpel alias build failed")
        logger.debug("scalpel alias oracle build error", exc_info=True)
        return fallback
```

(The `except ImportError: _note_fallback("python-scalpel not installed"); return fallback` branch is removed — the vendored import cannot be absent. Keep the per-query `self._fallback.may_alias(...)` inside `may_alias` unchanged.)

- [ ] **Step 5: Run the oracle tests — now green**

Run: `poetry run python -m pytest test/test_vendored_scalpel.py -q`
Expected: PASS (scalpel is now the default oracle; determinism holds).

- [ ] **Step 6: L4 regression — the live acceptance test (scalpel-L4 on 3.14)**

Run: `poetry run python -m pytest test/test_v2_l4.py test/test_v2_l4_summary.py test/test_dataflow_sdg.py test/test_dataflow_defuse.py -q`
Expected: PASS. These now exercise the vendored **scalpel** path (previously the type-based fallback on 3.14). If a case fails, investigate: a genuine scalpel-vs-type-based points-to difference is expected and the test's expectation may need updating to the (correct, tighter) scalpel result — but confirm it's a precision tightening, not a regression, before changing any assertion. If unsure, report rather than edit the assertion.

- [ ] **Step 7: Commit**

```bash
git add codeanalyzer/dataflow/scalpel_oracle.py test/test_vendored_scalpel.py
git commit -m "feat(dataflow): make vendored scalpel the default L4 alias oracle"
```

---

### Task 3: Fidelity check + documentation

**Files:**
- Test: `test/test_vendored_scalpel.py` (append the fidelity check)
- Modify: `CLAUDE.md`, `.claude/SCHEMA_DECISIONS.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: the vendored slice (Task 1) and the rewired oracle (Task 2). Produces no new code interface.

- [ ] **Step 1: Add the vendored-vs-upstream fidelity test (skipif no pip scalpel)**

Append to `test/test_vendored_scalpel.py`:

```python
import pytest


def _pip_scalpel_available():
    try:
        import importlib.util
        # the EXTERNAL package, not our vendored copy
        return importlib.util.find_spec("scalpel") is not None and \
            not importlib.util.find_spec("scalpel").origin.endswith(
                "codeanalyzer/dataflow/scalpel/__init__.py")
    except Exception:
        return False


@pytest.mark.skipif(not _pip_scalpel_available(),
                    reason="upstream python-scalpel not installed (3.12+ can't build typed_ast)")
def test_vendored_ssa_matches_upstream():
    """On a Python where pip python-scalpel installs (<=3.11), the vendored copy
    must produce identical SSA const_dict keys as upstream on the same source."""
    import scalpel.SSA.const as up_ssa
    import scalpel.cfg as up_cfg
    from codeanalyzer.dataflow.scalpel.SSA.const import SSA as VSSA
    from codeanalyzer.dataflow.scalpel.cfg import CFGBuilder as VCFG

    src = "def f(a):\n    b = a\n    if a:\n        b = 2\n    return b\n"
    up_c = up_cfg.CFGBuilder().build_from_src("m", src)
    v_c = VCFG().build_from_src("m", src)
    up_fn = list(up_c.functioncfgs.values())[0]
    v_fn = list(v_c.functioncfgs.values())[0]
    _, up_const = up_ssa.SSA().compute_SSA(up_fn)
    _, v_const = VSSA().compute_SSA(v_fn)
    assert sorted(map(str, up_const)) == sorted(map(str, v_const))
```

- [ ] **Step 2: Run the fidelity test (skips on 3.14; that's expected)**

Run: `poetry run python -m pytest test/test_vendored_scalpel.py -q`
Expected: PASS with the fidelity case SKIPPED on the 3.14 dev env (pip scalpel not installable).

- [ ] **Step 3: Update `CLAUDE.md`**

In `CLAUDE.md`, find the "**L4 points-to oracle = Scalpel.**" bullet and replace its "optional dependency … sanctioned fallback, not the shipping default" framing. Change the sentence:

> `python-scalpel` is an **optional** dependency: if it is absent or a build/query fails, the analyzer falls back to the total `TypeBasedAliasOracle` (`alias.py`) and degrades, never raising. The type-based oracle is the sanctioned fallback, not the shipping default.

to:

> Scalpel is **vendored** (`codeanalyzer/dataflow/scalpel/`, a `typed_ast`-free 9-module slice of `python-scalpel 1.0b0`, Apache-2.0) so it is the **shipping default** L4 oracle on every supported Python — there is no external `python-scalpel`/`typed_ast` dependency. `TypeBasedAliasOracle` (`alias.py`) is retained only as the runtime safety net: on a per-callable Scalpel build failure or a per-query unresolved access path, `may_alias` degrades to it, never raising.

- [ ] **Step 4: Update `.claude/SCHEMA_DECISIONS.md`**

Append a follow-up note to the "## Stage 0 — Scalpel oracle spike" section:

```markdown
**Follow-up (2026-07-22): Scalpel vendored, now the default.** The Stage-0
"dependency hygiene" concern proved fatal for a hard dependency: `python-scalpel`
drags `typed_ast`, which has no wheel for Python 3.12+ and does not build from
source there, so `pip install python-scalpel` fails on 3.12/3.13/3.14. Since
`typed_ast` is imported only by `scalpel/typeinfer` (unused here), the 9-module
`SSA`/`cfg`/`core` slice the oracle loads was **vendored** into
`codeanalyzer/dataflow/scalpel/` (Apache-2.0, verbatim but for a `graphviz`-lazy
patch). `ScalpelAliasOracle` is now the shipping default on all supported Python;
`TypeBasedAliasOracle` is the runtime safety net only. See
`docs/superpowers/specs/2026-07-22-vendored-scalpel-default-oracle-design.md`.
```

- [ ] **Step 5: Update `CHANGELOG.md`**

Under the `## [Unreleased]` heading, add:

```markdown
### Changed
- The Scalpel-backed L4 points-to oracle is now **vendored** (`typed_ast`-free)
  and the default on all supported Python (3.9–3.14); `python-scalpel` is no
  longer an optional dependency and the `[scalpel]` extra is removed. On Python
  3.12+ (and anywhere the `[scalpel]` extra was not installed), L4
  `prov:["points-to"]` data-dependence edges are now Scalpel-precise rather than
  the coarser type-based over-approximation; the `prov:["ssa"]` set and the
  `L3 ⊆ L4` monotonicity invariant are unchanged. Adds `astor` as a runtime
  dependency.
```

- [ ] **Step 6: Run the full behavior-preservation gate**

Run: `poetry run python -m pytest test/test_vendored_scalpel.py test/test_v2_l4.py test/test_v2_l4_summary.py test/test_dataflow_sdg.py test/test_dataflow_defuse.py test/test_v2_superset.py -q`
Expected: PASS (incl. the `test_l3_subset_of_l4` monotonicity gate in `test_v2_superset.py`). Note: `test_v2_superset.py` runs the CLI with `--no-venv`; it exercises the vendored scalpel on the L4 fixture.

- [ ] **Step 7: Commit**

```bash
git add test/test_vendored_scalpel.py CLAUDE.md .claude/SCHEMA_DECISIONS.md CHANGELOG.md
git commit -m "docs(dataflow): record vendored scalpel as the default L4 oracle"
```

---

## Self-Review

**1. Spec coverage:**
- Vendored `codeanalyzer/dataflow/scalpel/` 9-file slice, verbatim + graphviz patch → Task 1 Steps 3–4. ✓
- Attribution (LICENSE, README, NOTICE) → Task 1 Steps 3, 5. ✓
- `astor` added, `[scalpel]` extra removed, `requires-python` unchanged → Task 1 Step 6. ✓
- Oracle repointed + default (ImportError branch dropped, fallback kept) → Task 2 Steps 3–4. ✓
- Behavior change (L4 points-to precision), monotonicity preserved → Task 2 Step 6, Task 3 Steps 5–6. ✓
- Docs: CLAUDE.md, SCHEMA_DECISIONS, CHANGELOG → Task 3 Steps 3–5. ✓
- Testing: import hygiene, typed_ast-free gate, default-scalpel, determinism, fidelity, L4 regression, monotonicity → Tasks 1–3. ✓

**2. Placeholder scan:** No TBD/TODO. The vendoring uses exact `cp` commands and an explicit file list; the graphviz patch shows the exact before/after; every doc edit gives the exact old→new text.

**3. Type consistency:** `make_alias_oracle(pycallable, func_ast, base_types)` signature matches the existing call site in `core`/the pipeline. Import paths (`codeanalyzer.dataflow.scalpel.SSA.const`, `...cfg`) are consistent across Task 1 (creation), Task 2 (oracle import), and the tests. `ScalpelAliasOracle`/`TypeBasedAliasOracle` names match `scalpel_oracle.py`/`alias.py`.
