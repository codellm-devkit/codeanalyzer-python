# Jedi + defuse linker call graph (PyCG removal)

**Status:** approved (design session 2026-08-25)
**Scope:** `codeanalyzer-python` only
**Tracking:** #148 (repurposed work item; one PR)
**Supersedes:** #145 / PR #147 (closed unmerged), the #148 visit-budget design

## Motivation

PyCG's Andersen-style global fixpoint is the analyzer's cost pathology. On the
2,364-file odoo_slim fixture, a seed-pinned sharded run (17 shards, 10-way Ray
parallel) ran **3h19m without a single shard converging** — zero PyCG edges —
while Jedi produced 29,721 edges in 0.1s after the symbol table. `--pycg-max-iter`
cannot bound it (the cap is consulted only between passes; a single pass ran
>80 minutes), and the removed `--pycg-shard-timeout` bounded it by machine load,
which made output non-deterministic (#145's 11% edge spread). A metered
visit-budget prototype (bit-identical 1,257,472 visits across calibration runs)
proved a deterministic bound is possible, but bounding a fundamentally
mis-scaled analysis is treating the symptom.

The replacement follows the Joern / Fraunhofer CPG architecture: a fast base
call graph from name/type resolution, then a **local** linker pass over the
per-callable CPG (def-use chains — no global fixpoint) that backfills call
edges the base resolver missed. The expensive substrate already exists in this
repo as the L3 kernels (`dataflow/defuse.py`, `access_paths.py`, CFG).

## Contract-impact triage

| Question | Answer |
| --- | --- |
| Schema v2 shape (node/edge kinds, fields, ids) | unchanged |
| `prov` vocabulary | `"pycg"` disappears; **`"defuse"`** coined for linker-derived call edges (technique-named, like the DDG's `"ssa"` / `"points-to"` / `"reaching-defs"`) |
| Refinement contract | unchanged — linker runs *inside* the L2 build, so `callee: null→id` remains the single sanctioned L1→L2 refinement; no new L2→L3 refinement |
| Monotonicity gate | unaffected (edges only added at L2, as today) |
| Repos | `codeanalyzer-python` only; **verify-item:** grep python-sdk / TS SDK for hardcoded `"pycg"` (expected: none, `prov` is passthrough) |
| CLI (breaking) | `--pycg-shard`, `--pycg-shard-ceiling`, `--pycg-shard-strategy`, `--pycg-max-iter`, `--call-graph` all removed; one code path, no backend flag |

## Locked decisions

1. **Jedi is the base call graph, always.** Its edges keep `prov: ["jedi"]`.
2. **PyCG is removed wholesale**: `codeanalyzer/semantic_analysis/pycg/`
   (analysis, shard planner, symlink fencing, Ray workers), the `pycg`
   dependency, all `--pycg-*` flags, the `--call-graph` enum (unreleased), the
   shard-determinism test suite, and the README sharding section.
3. **The defuse linker runs at L2 with targeted kernels**: `-a 2` builds
   def-use state internally only for callables that still contain unresolved
   call sites after Jedi. Per-callable, no fixpoint, deterministic by
   construction (sorted iteration mandated; no hash-order sensitivity).
4. **Linker edges carry `prov: ["defuse"]`**, merged with Jedi's via
   `merge_edges` (an edge found by both becomes `["defuse", "jedi"]`).
   External targets home in `external_symbols` exactly as today.
5. **No backend flag.** The linker is cheap and deterministic; nothing to opt
   out of.

## Linker design (as implemented)

For each call site whose `callee_signature` is null after Jedi:

1. **Bare names** resolve through the lexical chain of *function* scopes out
   to the module (class bodies are transparent, matching Python's own rule):
   local/nested `def`s, alias-assignment chains (`f = handler; f(x)`), and
   `from m import f [as g]` bindings — the latter resolved **cross-module**
   through the symbol table (absolute and relative spellings; module quals
   derived from file keys, `requests/api.py` → `requests.api`). Recursion
   self-loops are kept.
2. **`self.X()` / `cls.X()`** resolves against the enclosing class and its
   same-module base chain (BFS over `base_classes`) — Jedi misses a
   surprising number of these on decorated and mixin-heavy classes.
3. A resolution emits the edge with `prov: ["defuse"]` and lands in a
   returned resolutions map that `l2_callees.backfill_callees` applies to the
   L1 `call` body nodes. Resolutions are **never written into
   `callee_signature`**: the symbol table round-trips through the analysis
   cache, and a persisted resolution would resurface on a warm run as a Jedi
   edge, silently changing provenance.

**Interprocedural round (implemented 2026-08-26).** One deterministic
propagation round on top of the local pass — a `_TypeOracle` built from the
symbol table plus the same per-module AST facts: (1) Jedi's declared
parameter types; (2) cross-site voting — every resolved call site's
positional argument `inferred_type` votes for the callee parameter, a
singleton internal-class vote types it; (3) return summaries (unique
`return C(...)` or `return name` of a ctor-typed local, plus Jedi
`return_type`); (4) `self.attr` instance-attribute types from `self.X = C()`
/ literal assignments anywhere in the class. Receivers the local pass could
not type are retried against the oracle in two bounded rounds (round one's
resolutions vote before round two) — no fixpoint. Votes only admit
internal-class types, which keeps #146's flapping IO types out of the
lattice; A/B runs stay byte-identical modulo #146.

**Name-linked tier (implemented 2026-08-26).** Receiver sites that survive
every typing tier resolve by CHA-by-name: the call may target any internal
callable of that name (bounded per site), which is exactly the
over-approximation Joern emits for untyped receivers. The tier also lowers
iteration everywhere it appears (for-loops, comprehensions, generator
expressions) and methods on builtin temporaries
(`TypeError(...).with_traceback`). Sound may-call; applied only after the
typed tiers so precise resolutions are never widened.

**Completion ledger (2026-08-26).** With the tiers above, the call graph is a
superset of every real edge both reference tools produce on both fixtures:

- Joern requests: 211/212 internal pairs; the one residual targets Joern's
  synthetic `<lambda>0` node, unnameable in this schema.
- Joern flask: 182/190; the 8 residuals are all synthetic Joern nodes —
  parameters-as-callees (`load`, `loads`, `decorator`, `response`),
  `<body>`, `<lambda>0`, and `<redefined>N` duplicates.
- Fraunhofer requests/flask: every real edge covered; residuals are their
  inference stubs (targets that do not exist in source: `None.*`,
  `object.object`, `t.Any.*`, methods fabricated on classes that never
  declare them), instance/parameter attribute stubs where we hold the
  fully-resolved edge, private-impl naming variants (`RLock`/`_RLock`,
  `ref`/`ReferenceType`), and one blank-caller synthetic.

A/B determinism after all tiers: byte-identical `call_graph` on both
fixtures across paired runs (#146 remains open as a probabilistic Jedi
source; the linker's vote lattice admits internal-class names only and
cannot amplify it).

Still out of scope: Scalpel copy-closure alias widening.

## Reference validation (2026-08-25/26)

Iterated edge-for-edge against Joern (`pysrc2cpg`, v4.0.611) and
Fraunhofer-AISEC CPG (main, jep frontend) on the requests and flask fixtures
until ours was a superset of every real, existing-target edge both tools
produce. Along the way this hardened far more than the linker: the reference
diff exposed L1 symbol-table gaps (defs under `if`/`try` at any nesting were
invisible), Jedi junk resolutions (`typing.Callable`,
`functools._lru_cache_wrapper`, `builtins.NoneType` stamps), and a
`filter_external_edges` gap that dropped every module-scope edge to a library
target. Documented exceptions, by class:

- **Their inference stubs** — targets that do not exist in source
  (`None.read`, `object.object` implicit-base constructors,
  `FlaskClient._add_cookies_to_wsgi` fabricated on the subclass while the
  method lives on werkzeug's `Client`; we emit the external base instead).
- **Attribute-named variants** — they name the class attribute
  (`Flask.request_class`); we resolve through it to the real target
  (`Request.__init__`), usually via Jedi.
- **Private-impl naming** — `RLock` vs `_dummy_threading._RLock`, `fspath`
  vs `os._fspath`, `weakref.ref` vs `_weakref.ReferenceType`.
- **Whole-program type flows** (Joern only, 16 pairs on requests after the
  interprocedural round landed — was 21) — hook registries, types crossing
  external-library returns, attribute-chain receivers; the L4 SDG-summary
  propagation unit.
- Joern's remaining ~4.4k rows are speculative typed-attribute fan-out
  (`dict.__iter__.read.split`, `None.split`) and `<metaClass*>` machinery —
  candidate enumeration, not resolution; matching it would mean fabricating
  edges to nonexistent symbols.

**What is knowingly lost vs PyCG:** cross-module global registries (odoo's
registry pattern) and deep dynamic dispatch. Those edges were unobtainable in
practice anyway — PyCG never converged on exactly the projects that have them.

## Acceptance

- Spike metric, measured during implementation: of PyCG's unique edges on the
  flask and requests fixtures (PyCG converges there), the % the linker
  recovers — reported in the PR, no hard gate.
- Flask A/B reproducibility stays REPRODUCIBLE modulo #146 (Jedi `open()`
  overload nondeterminism — after this change, the only known source).
- Full suite green; monotonicity CI gate green; `scripts/update_readme.py
  --check` green.

## Release plan

One PR on `feat/issue-148-jedi-only-call-graph` closes #148; ships in the next
minor alongside the already-queued breaking entries (`--pycg-shard-timeout`
removal is subsumed by full PyCG removal — CHANGELOG consolidates). #147
closes unmerged; #145 closes as superseded (its non-determinism is removed
with its subject).
