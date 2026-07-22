# Vendored, typed_ast-free Scalpel as the default L4 oracle

- **Date:** 2026-07-22
- **Status:** Design approved; ready for an implementation plan
- **Scope:** Make the Scalpel-backed points-to oracle (`ScalpelAliasOracle`) the
  shipping default for L4 dataflow on **every** supported Python, by vendoring a
  minimal, `typed_ast`-free slice of `python-scalpel` into the package.

## Motivation

`python-scalpel` is the primary L4 may-alias oracle, but it ships today as an
**optional** extra with a type-based fallback — because `python-scalpel 1.0b0`
hard-depends on **`typed_ast`**, an abandoned package whose last release
(`1.5.5`) has no wheel for Python 3.12+ and fails to compile from source on a
modern compiler. So on the interpreters most users (and this repo's own dev env)
now run — 3.12, 3.13, 3.14 — scalpel cannot be installed at all, and L4 silently
degrades to the coarser type-based oracle.

We want scalpel to be the **default** L4 oracle everywhere. It cannot be a hard
PyPI dependency (the `typed_ast` wall), and a Python-version cap to reach it
(≤3.11) would drop the 3.12–3.14 support the analyzer just added. The way
through is to **vendor** the small slice of scalpel we use, minus the one module
that drags `typed_ast`.

## Feasibility (verified, not assumed)

- **`typed_ast` is spurious for our use.** It is imported in exactly one scalpel
  module — `scalpel/typeinfer/analysers.py` — which codeanalyzer never touches.
  The oracle uses only `scalpel.SSA.const` and `scalpel.cfg`.
- **The slice runs without `typed_ast`.** On Python 3.12 with `typed_ast` *not*
  installed, `cfg.CFGBuilder` + `SSA.const.SSA` import and `compute_SSA` returns
  the exact SSA/const facts the oracle consumes (verified: a copy chain yields
  `('b',0), ('c',0), ('<ret>',0)`).
- **Exact closure = 9 modules**, provably free of `typeinfer`/`typed_ast`
  (checked via `sys.modules` after importing the two entry points):
  `scalpel/__init__.py`, `SSA/{__init__,const}.py`,
  `cfg/{__init__,builder,model}.py`, `core/{__init__,func_call_visitor,vars_visitor}.py`.
- **Third-party imports touched:** `astor` (used at runtime — `SSA/const.py:191`
  `astor.to_source`), `graphviz` (imported at module load in `cfg/model.py` but
  only used by the unused `build_visual()`), `networkx` (already a dependency).
- **License = Apache-2.0** (confirmed on `SMAT-Lab/Scalpel` and in the wheel's
  `LICENSE`) — vendorable with attribution, same family as the already-bundled
  PyCG.
- **Upstream is abandoned** (`1.0b0`, frozen) — the usual "vendored copy drifts
  from upstream" cost does not apply.

## Goals

- `ScalpelAliasOracle` is the **default** L4 oracle on Python 3.9–3.14+, with no
  external `python-scalpel`/`typed_ast` dependency and no `pip install` breakage.
- The type-based oracle (`TypeBasedAliasOracle`) is retained purely as the
  runtime safety net (per-callable build failure, per-query unresolved path).
- Behavior for users who already had the `[scalpel]` extra on ≤3.11 is unchanged
  (same code). Behavior on 3.12+ *improves* (real points-to instead of the
  type-based over-approximation).

## Non-goals

- No change to the L4 *schema* (`prov` values `ssa`/`points-to` unchanged; the
  DDG shape is the same).
- No `requires-python` change — stays `>=3.9`.
- No vendoring of scalpel's `typeinfer`, `call_graph`, `pycg`, `import_graph`,
  `scope_graph`, or `dataflow` packages — only the 9-module `SSA`/`cfg`/`core`
  closure the oracle actually loads.
- No change to `defuse.py`'s two-rule DDG contract or the `k_limit` machinery.

## Design

### The vendored package: `codeanalyzer/dataflow/scalpel/`

A normal package (no `_vendor/` layer), mirroring upstream's layout so scalpel's
relative imports (`from ..core.vars_visitor import get_vars`) resolve within
`codeanalyzer.dataflow.scalpel`:

```
codeanalyzer/dataflow/scalpel/
  __init__.py
  SSA/__init__.py, const.py
  cfg/__init__.py, builder.py, model.py
  core/__init__.py, func_call_visitor.py, vars_visitor.py
  LICENSE          # upstream Apache-2.0, copied verbatim
  README.md        # provenance + the one patch (see below)
```

The `scalpel/` wrapper name is kept (rather than flattening `SSA`/`cfg`/`core`
into `dataflow/`) because `codeanalyzer/dataflow/cfg.py` already exists and
scalpel ships its own `cfg/` package — the wrapper namespaces them apart.

The 9 files are copied **verbatim** from `python-scalpel 1.0b0`, with **exactly
one patch**:

- `cfg/model.py`: the module-load `import graphviz as gv` (line 12) is moved to a
  lazy import *inside* the unused `build_visual()` method. This removes `graphviz`
  as a runtime dependency (we never render). No other line changes; the
  SSA/CFG-computation code paths are byte-identical to upstream.

### Attribution (Apache-2.0)

- `codeanalyzer/dataflow/scalpel/LICENSE` — upstream's Apache-2.0 license, copied.
- `codeanalyzer/dataflow/scalpel/README.md` — records: source repo
  (`SMAT-Lab/Scalpel`), the pinned version (`1.0b0`), the exact 9-file list, the
  single `graphviz`-lazy patch, and that `typeinfer` (the sole `typed_ast` user)
  was deliberately excluded.
- The repo's top-level `NOTICE` gains a one-line entry crediting vendored Scalpel
  (Apache-2.0), alongside the existing attributions.

### Dependencies (`pyproject.toml`)

- **Add** `astor` to core `dependencies` (genuine runtime dep; pure-Python;
  installs on every platform/Python).
- **Remove** the `[project.optional-dependencies].scalpel` extra (scalpel is now
  built in).
- `networkx` unchanged (already core); `graphviz` and `typed_ast` are **not**
  dependencies.
- `requires-python` stays `>=3.9`.

### Oracle rewiring: `codeanalyzer/dataflow/scalpel_oracle.py`

- `ScalpelAliasOracle.from_function` imports from the vendored path:
  `from codeanalyzer.dataflow.scalpel.SSA.const import SSA` and
  `from codeanalyzer.dataflow.scalpel.cfg import CFGBuilder`.
- `make_alias_oracle`: the `ImportError` ("python-scalpel not installed") branch
  becomes dead — the vendored import cannot be absent — and is **removed**. The
  per-callable **build-failure** `except` (returns the type-based fallback) and
  the per-query unresolved-path fallback inside `may_alias` both **stay**, so the
  total, never-raises contract is preserved. `ScalpelAliasOracle` is now the
  default the selector returns.
- Docstrings/comments updated to drop the "optional / not installed" framing.

### Behavior change

On Python 3.12+/3.14 — and for anyone who never installed the old `[scalpel]`
extra — L4's `prov:["points-to"]` DDG edges were previously derived from the
*type-based* over-approximation; they now become **scalpel-precise** (a tighter
subset). The `prov:["ssa"]` edges (the L3 subset) are unchanged, so the
**monotonicity invariant `L3 ⊆ L4` still holds** (the ssa set is untouched;
points-to is an additive overlay). Users who already had the `[scalpel]` extra on
≤3.11 see no change. This L4 precision improvement is recorded in `CHANGELOG.md`.

### Docs

`CLAUDE.md` and `.claude/SCHEMA_DECISIONS.md` are updated to record: scalpel is
now **vendored** (`codeanalyzer/dataflow/scalpel/`, `typed_ast`-free) and the
**shipping default** L4 oracle on all supported Python — superseding the earlier
"the type-based oracle is the sanctioned fallback, not the shipping default"
statement (it is now the runtime safety net, not the default). The Stage-0
`SCHEMA_DECISIONS` entry gains a follow-up noting the `typed_ast` wall on 3.12+
that forced vendoring.

## Testing plan

1. **Import hygiene.** `import codeanalyzer.dataflow.scalpel` and building the
   oracle succeed with no external `scalpel`/`typed_ast`/`graphviz` installed;
   assert `typeinfer` is never imported (scan `sys.modules` after building the
   oracle).
2. **`typed_ast`-free property gate.** Assert `typed_ast` is not importable in
   the environment, yet `make_alias_oracle` builds a `ScalpelAliasOracle` and
   `may_alias` returns verdicts on a copy-chain fixture — the whole point,
   encoded as a regression gate.
3. **Vendored-fidelity check.** On Python ≤3.11 (where pip `python-scalpel`
   installs), compare the vendored `compute_SSA`/CFG output against the
   pip-installed scalpel on a set of fixtures — proves the copy is faithful.
   `skipif` the pip package can't install (3.12+); the copy is verbatim, so this
   is a belt-and-suspenders check.
4. **L4 regression (the live acceptance test).** `test_v2_l4.py`,
   `test_v2_l4_summary.py`, `test_dataflow_sdg.py`, `test_dataflow_defuse.py` now
   exercise the **scalpel** path by default — the first time scalpel-L4 runs on
   the 3.14 dev env. They must stay green and now genuinely test scalpel-derived
   points-to rather than the fallback.
5. **Determinism.** Run L4 twice on a fixture and assert identical output (the
   scalpel SSA feeds `may_alias`; the `#99` `PYTHONHASHSEED=0` pin + the oracle's
   access-path normalization must keep it stable).

## Risks and rollback

- **Incomplete closure** — a missed transitive import would surface as an
  `ImportError` at oracle build, which the per-callable fallback swallows into a
  silent type-based degrade. Mitigation: the import-hygiene test (1) builds the
  oracle and asserts it is a `ScalpelAliasOracle`, so a broken closure fails
  loudly rather than degrading silently.
- **Vendored-copy divergence from upstream behavior** — mitigated by the verbatim
  copy (only the `graphviz` import patched) and the fidelity check (3).
- **L4 output change on 3.12+** is intended, documented in the CHANGELOG, and
  bounded by the preserved monotonicity invariant.
- **Rollback** is a single revert: delete `codeanalyzer/dataflow/scalpel/`,
  restore the `[scalpel]` extra + the `make_alias_oracle` ImportError branch, and
  drop `astor`. No schema or id changes to unwind.

## Out of scope

- Replacing `astor.to_source` with stdlib `ast.unparse` to drop the `astor` dep
  (a behavior-risking change to vendored SSA logic — deferred; `astor` is tiny
  and universal).
- A scalpel-installed CI lane / capturing the L4 (`a4`) equivalence goldens from
  the separate analysis-pipeline branch — that work belongs to that branch once
  it merges; here scalpel simply becomes available for it.
- Vendoring or using scalpel's type-inference (`typeinfer`) for the type-guided
  alias branch — the type-based oracle already covers that fallback.
