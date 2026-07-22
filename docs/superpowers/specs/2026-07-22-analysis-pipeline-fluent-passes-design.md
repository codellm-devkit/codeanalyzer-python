# Reshaping the analysis levels as a fluent pass pipeline

- **Date:** 2026-07-22
- **Status:** Design approved; ready for an implementation plan
- **Scope:** Internal refactor of `Codeanalyzer.analyze()` orchestration. No
  schema change, no CLI change, no output change.

## Motivation

`Codeanalyzer.analyze()` (`codeanalyzer/core.py:560-691`) is a single ~130-line
procedural method that grows the canonical schema-v2 CPG tree one level at a
time. The level gating is threaded through the body as scattered
`if self.analysis_level >= 2 / 3 / 4:` checks, and the transient artifacts that
one step produces and a later step consumes (`sig_to_id`, the L3 PDGs `infos`,
the L4 program graphs `ir`) are ordinary locals with no named home. The method
is hard to read as "the four levels," the per-level work is not independently
testable, adding or reordering a step means editing the monolith, and
per-step observability is hand-rolled (only the Jedi and symbol-table timings
are instrumented today).

## Goals

- **Readability / maintainability** — the orchestration reads as a linear,
  self-documenting chain that mirrors the four analysis levels.
- **Testable passes in isolation** — each pass is a `context -> context`
  function that can be run and asserted against a hand-built context, not only
  through the full pipeline.
- **Extensibility** — inserting, reordering, or adding a pass is a local edit,
  not surgery on a monolith.
- **Per-pass observability** — uniform timing, logging, and gate reporting for
  every pass, replacing the ad-hoc instrumentation.

All of this while keeping output **byte-identical at every level** — this is a
behavior-preserving refactor.

## Non-goals

- No change to the emitted schema (`schema_version` stays `2.0.0`), the
  `Analysis` envelope, the `-a`/`--graphs`/`--graph-field-depth` CLI surface, or
  the Neo4j projection.
- No change to the caching format, the venv provisioning, or the
  degradation semantics (PyCG failure → Jedi-only; Scalpel absent → type-based
  fallback).
- No new analysis capability. Passes wrap exactly the work `analyze()` does
  today, in the same order.

## The pattern: a fluent analysis-pass pipeline

One architectural pattern: **analysis passes composed through a fluent
`.with_*()` chain over a shared context.** The chain replaces the procedural
body of `analyze()`; the scattered level gates are replaced by each pass
self-gating on the level it belongs to.

The chain groups by subsystem (mirroring the package layout
`syntactic_analysis/` → `semantic_analysis/` → `dataflow/`), giving **four
passes**:

```python
analysis = (
    AnalysisPipeline(ctx)
        .with_symbol_table()        # syntactic_analysis/  (L1 tree)
        .with_call_graph()          # semantic_analysis/ + identity (L1 + L2)
        .with_intraproc_dataflow()  # dataflow/  L3
        .with_interproc_dataflow()  # dataflow/  L4
        .build()                    # -> Analysis
)
```

## Architecture

### New module: `codeanalyzer/pipeline.py`

Holds two types; `Codeanalyzer` remains the engine that owns the process/
lifecycle concerns.

### `AnalysisContext`

A plain dataclass — the single carrier threaded through the chain. Inputs set at
construction; produced artifacts start empty and are filled in chain order.

```
# inputs
options              # AnalysisOptions
project_dir          # Path
virtualenv           # Optional[Path]
analysis_level       # int  (== options.analysis_level, hoisted for the gates)
app_name             # str
cached_symbol_table  # Dict[str, PyModule]  (from cache, for per-file reuse)

# produced by passes (empty/None until their pass runs)
symbol_table         # Dict[str, PyModule]   <- with_symbol_table
app                  # PyApplication         <- with_call_graph
sig_to_id            # dict                  <- with_call_graph (consumed by both dataflow passes)
infos                # L3 PDGs               <- with_intraproc_dataflow (REUSED by L4)
ir                   # L4 program graphs     <- with_interproc_dataflow
```

### `AnalysisPipeline`

Holds the context; each `.with_*()` mutates it and returns `self`; `.build()`
assembles and returns the `Analysis` envelope. Gating, timing, and logging are
folded into one shared runner so every pass is instrumented identically:

```python
class AnalysisPipeline:
    def __init__(self, ctx: AnalysisContext):
        self.ctx = ctx

    def with_symbol_table(self):       return self._run("symbol_table",       1, _pass_symbol_table)
    def with_call_graph(self):         return self._run("call_graph",         1, _pass_call_graph)
    def with_intraproc_dataflow(self): return self._run("intraproc_dataflow", 3, _pass_intraproc_dataflow)
    def with_interproc_dataflow(self): return self._run("interproc_dataflow", 4, _pass_interproc_dataflow)

    def _run(self, name, min_level, fn):
        if self.ctx.analysis_level < min_level:
            logger.info("⏭️  %s: skipped (level %d < %d)", name, self.ctx.analysis_level, min_level)
            return self
        t0 = time.perf_counter()
        fn(self.ctx)                       # pure context -> context mutation
        logger.info("✅ %s: %.1fs", name, time.perf_counter() - t0)
        return self

    def build(self) -> Analysis:
        return Analysis(
            max_level=self.ctx.analysis_level,
            k_limit=self.ctx.options.graph_field_depth if self.ctx.analysis_level >= 3 else None,
            analyzer=analyzer_info(self.ctx.analysis_level),
            application=self.ctx.app,
        )
```

Each pass is a module-level function `_pass_*(ctx)` — that is the isolated,
testable unit. Every pass asserts its **preconditions** (e.g.
`_pass_interproc_dataflow` asserts `ctx.app` and `ctx.infos` are populated) so a
misordered chain fails loudly instead of dereferencing `None`.

### The four passes

| Pass | `min_level` | Wraps (from today's `analyze()`) | Reads → writes |
| --- | --- | --- | --- |
| `_pass_symbol_table` | 1 | `_build_symbol_table`, `resolve_unresolved_constructors` | `cached_symbol_table` → `symbol_table` |
| `_pass_call_graph` | 1 | `jedi_call_graph_edges`; **(≥2)** `_get_pycg_call_graph` + `merge_edges`; `filter_external_edges`; edge sort; `PyApplication.builder()`; `resolve_imports`; `repository_info`; `assign_ids`; `_home_external_symbols`; `populate_l1_body`; **(≥2)** `backfill_callees`; `reidentify_call_graph` | `symbol_table` → `app`, `sig_to_id` |
| `_pass_intraproc_dataflow` | 3 | `build_function_pdgs` (`SyntacticOracle`), `emit_l3_body` (with `set(options.graphs.split(","))`) | `app`, `sig_to_id` → `infos` |
| `_pass_interproc_dataflow` | 4 | `build_program_graphs` (`make_alias_oracle`/Scalpel), `emit_l4`, `emit_ddg_pointsto_delta` (reusing `ctx.infos`) | `app`, `sig_to_id`, **`infos`** → `ir` |

### `Codeanalyzer.analyze()` becomes a thin wrapper

It keeps what it already owns — venv lifecycle (`__enter__`/`__exit__`), cache
load/save, the analyzer-version check — and delegates the build:

```python
def analyze(self) -> Analysis:
    cache_file = self.cache_dir / "analysis_cache.json"
    cached = self._maybe_load_cache(cache_file)          # existing logic, unchanged
    seed = cached.application.symbol_table if cached else {}
    ctx = AnalysisContext(
        options=self.options, project_dir=self.project_dir,
        virtualenv=self.virtualenv, analysis_level=self.analysis_level,
        app_name=self.options.app_name or self.project_dir.name,
        cached_symbol_table=seed,
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

## Key decisions

### No explicit `skip` argument — passes self-gate on `min_level`

The pipeline holds the context, which already carries `analysis_level`, so a
caller-supplied `skip=level < 3` re-derives a fact the pass already has and
invites the wrong-predicate bug (`skip=level < 2` on an L3 pass). The level a
pass belongs to is an **intrinsic property of the pass**, declared once as the
`min_level` argument to `_run` (greppable, one line per pass). The chain reads
as a clean declarative list with no predicates, and the `⏭️ skipped
(level 2 < 3)` log makes the runtime gate observable.

### Internal L2 gates stay inside `_pass_call_graph`

The call graph always runs Jedi at L1 and only *adds* PyCG at ≥2; likewise
`backfill_callees` is L2-only. These are sub-steps of one pass, not whole
passes, so they remain plain `if ctx.analysis_level >= 2:` branches in the pass
body. There is nothing to hoist to the chain level.

### Caching and venv stay at the edge, not as passes

Cache load/save and venv provisioning are process/lifecycle concerns. Keeping
them in `Codeanalyzer` (out of the pipeline) keeps every pass a pure
`context -> context` function testable with a hand-built context. The pipeline
consumes the cached symbol table via `ctx.cached_symbol_table` and produces the
`Analysis` the wrapper then persists.

### The three helpers move out of `Codeanalyzer`

`_build_symbol_table`, `_get_pycg_call_graph`, and `_home_external_symbols`
become plain functions (in the pipeline module or their home
`syntactic_`/`semantic_` modules) taking explicit arguments, so a pass has no
hidden dependency back on the `Codeanalyzer` instance. This is the bulk of the
mechanical churn. Callers are **updated** rather than shimmed (see Testing) — no
dead compatibility surface.

## Behavior-preservation guarantee

The refactor moves code without changing what it produces. Output is already
deterministic — the `PYTHONHASHSEED=0` re-exec (`__main__._pin_hash_seed`) and
the sorted call graph (issue #99) — so **byte-identical `analysis.json` at every
level** is a legitimate, non-flaky gate, and the existing monotonicity invariant
(`L1 ⊆ L2 ⊆ L3 ⊆ L4`) must continue to hold.

## Testing plan

Four layers, built in TDD order.

1. **Characterization snapshot — the pin (written & committed FIRST, against
   current code).** New `test/test_pipeline_equivalence.py`. Capture
   `analysis.json` for ~4 fast `single_functionalities/*` fixtures at `-a 1,2,3,4`
   (`--no-venv`) from today's `analyze()`, commit as golden. After the refactor
   the same runs must be **byte-identical**. Catches any reordering the
   structural asserts would wave through (e.g. `resolve_imports` / provenance /
   `assign_ids` landing in a different order, or L4 recomputing PDGs instead of
   reusing `ctx.infos`). One fixture's `graph.cypher` is snapshotted too, to pin
   the Neo4j projection.

2. **The existing suite = the broad net (runs unchanged, zero output-changing
   edits).** `test_v2_superset` (monotonicity), `conftest_v2.assert_conformant`
   (keystone), `test_v2_l1_body`/`l2`/`l3`/`l4`, `test_dataflow_*`,
   `test_v2_cache`, `test_v2_two_projection_agreement`, `test_neo4j_*`,
   `test_cli` all drive through `analyze()`/the CLI, so they exercise the new
   pipeline automatically. All green with no edits ⇒ behavior preserved.

3. **New unit tests for the genuinely-new mechanics — `test_pipeline.py` (the
   RED tests, written against the new API before it exists).**
   - **`_run` gating:** a pass below its `min_level` is a no-op — its context
     field stays empty, the skip line is logged, and it returns `self`.
   - **Each `_pass_*(ctx)` in isolation** against a hand-built context:
     populates only its output field, and asserts its precondition so a
     misordered chain fails loudly.
   - **Self-gating reproduces the old gates:** a context at level 2 → full chain
     → `infos`/`ir` empty and `app` carries no `cfg`/`ddg`.
   - **L3→L4 reuse ordering:** at level 4, `ctx.infos` is already populated when
     the interproc pass runs.

4. **Helper-move regression (mechanical).** Moving the three helpers off
   `Codeanalyzer`. Known impact: **update `test/test_env_interpreter.py:151`**
   (it calls `analyzer._build_symbol_table(cached_symbol_table={})`) to the new
   location, and update the two prose comment references in
   `dataflow/scalpel_oracle.py` and `semantic_analysis/pycg/pycg_analysis.py` if
   the names change.

**Process:** (1) commit golden snapshots against current code → (2) write
`test_pipeline.py` (fails, RED) → (3) implement the pipeline + move helpers →
(4) green: unit tests pass, equivalence is byte-identical, existing suite
passes, fix the one `test_env_interpreter` call → (5) verify by running the CLI
at each level on a fixture and eyeballing the `⏭️/✅` per-pass logs.

## Risks and rollback

- **Hidden ordering dependency** — the byte-identical snapshot is the specific
  guard; any reorder that changes output fails it immediately.
- **L4 losing the `infos` reuse** — the chain guarantees the L3 pass runs before
  the L4 pass at level 4; the reuse-ordering unit test and the equivalence
  snapshot both cover it.
- **Rollback** is trivial: the change is isolated to `core.py` plus the new
  `pipeline.py`; reverting the commit restores the procedural `analyze()`.

## Out of scope

- Splitting the call graph into finer passes, or making passes registerable /
  data-driven — deliberately deferred; four subsystem-grouped passes is the
  chosen granularity.
- Any change to Ray distribution, sharding, or the dataflow kernels themselves.
