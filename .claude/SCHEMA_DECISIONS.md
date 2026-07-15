# Schema decisions — codeanalyzer-python

Decision log for schema-affecting choices, kept as input for the CLDK SDK
model work (the frontend skill encodes these as shared Pydantic models). The
level-1/2 schema (`PyApplication`, symbol table, call graph) predates this
log; entries below start at level 3.

## Level 3 — `program_graphs` (issue #67, schema_version 1.0.0)

Contract baseline: the CLDK dataflow-graphs contract (shared node kinds, edge
types, JSON shapes; `(signature, node_id)` identity; `CFG_NEXT`/`CDG`/`DDG`/
`CALL`/`PARAM_IN`/`PARAM_OUT`/`SUMMARY` vocabulary). Divergences and
additions, all additive:

1. **Parameter nodes are first-class and live in a per-function
   `param_nodes` list**, not inside `cfg.nodes`. The contract's CFG gate
   (single ENTRY/EXIT, every node reachable-from-ENTRY/reaches-EXIT, EXIT =
   last CFG id) stays exact over `cfg.nodes`; HRB parameter-passing nodes
   (`formal_in`/`formal_out`/`actual_in`/`actual_out`) share the function's
   id space with ids allocated after EXIT, and carry `var` (the parameter
   name, `<return>`, `<capture>:name`, or `<global>:module::name`) plus
   `call_node` (owning callsite statement) for actuals.
2. **SUMMARY edges are emitted in `sdg_edges` with both endpoints in the same
   signature** (actual_in → actual_out at one callsite). The contract comment
   says "cross-function only"; SUMMARY is inherently intra-function in HRB
   form and cannot be typed as a `pdg` edge (`CDG|DDG`), so it rides
   `sdg_edges`. CALL/PARAM_IN/PARAM_OUT remain cross-function.
3. **Globals are qualified `module::name`** (double colon keeps the qualifier
   out of the field-path grammar `base(.field|[*])*`). Cross-module global
   identity holds when access flows through the defining module's functions;
   direct `from m import g` rebinding is a documented precision loss.
4. **The return value is the pseudo-path `<return>`**, defined at every
   return statement and wired to the `formal_out` of the same name.
5. **Python-specific CFG edge kinds used from the shared vocabulary:**
   `yield` (resume successor + abandonment edge to EXIT) and `await_resume`.
   No renamed or repurposed kinds; node kinds used: `entry`, `exit`,
   `statement`, `branch`, `loop`, `return`, `raise`, `handler`.
6. **Infinite loops get a synthetic `exception` edge header → EXIT** (any
   Python loop can exit via an async signal), keeping post-dominance rooted.
7. **Call mutations are suffixed weak defs** (`xs.*`): caller-visible
   mutation is distinguishable from local rebinding, which decides
   `formal_out` allocation for parameters.
8. **`dfg` has no separate section** (per contract): `--graphs dfg` emits the
   PDG with only DDG edges; `sdg` implies the dependence edges it stitches.
9. **Taint (`taint_flows`) is not emitted by this analyzer** — deliberately
   deferred to the CLDK SDK, where labeled SDG reachability is shared across
   languages; only source/sink/sanitizer model packs are per-language.

## Level-3 CPG (Neo4j) — schema.neo4j.json 1.2.0 (additive)

- New label `PyCFGNode` (merge key `id` = `<signature>#<node_id>`; props
  `kind`, `var`, `call_node`, `start_line`, `end_line`, `_module`). Both CFG
  statements and parameter nodes ride this one label, distinguished by
  `kind` — the parity clause's label set stays minimal.
- New edge types `PY_HAS_CFG_NODE` (PyCallable → PyCFGNode), `PY_CFG_NEXT`
  (prop `kind`), `PY_CDG`, `PY_DDG` (prop `var`), `PY_PARAM_IN`,
  `PY_PARAM_OUT`, `PY_SUMMARY`.
- **Namespacing decision (maintainer, 2026-07-02):** the CPG vocabulary is
  cross-language in *shape* (same suffix names, properties, semantics) but
  **per-language-prefixed in the Neo4j projection**, like every other row
  family (`PySymbol`, `PY_CALLS`, …). Rationale: SDK Neo4j backends scope
  queries by label/type prefix; unprefixed `DDG`/`CFGNode` in a database
  holding multiple languages' graphs would mingle analyzers' dependence
  edges with no way to separate them. Each analyzer uses its language tag
  (`TS_`/`TSCFGNode` for TypeScript, etc.). The **JSON** `program_graphs`
  section keeps the unprefixed shared vocabulary — it lives inside each
  analyzer's own `analysis.json`, so there is no shared namespace to
  collide in; the SDK strips/adds the prefix at the projection boundary.
- `CALL` SDG edges are not projected: the callable-level `PY_CALLS` twin
  already carries calls; callsite-statement granularity is recoverable via
  `PY_HAS_CALLSITE`/`PY_RESOLVES_TO`.

## Stage 0 — Scalpel oracle spike

Research spike (issue #70) verifying **SMAT-Lab/Scalpel** as the primary L4
may-alias oracle before the interprocedural-dataflow stage writes any
integration. No product code changed; a throwaway probe under `test/spikes/`
was run and deleted.

**Decision.** Primary oracle = **`ScalpelAliasOracle`** implementing the
frozen interface `may_alias(path_a: str, path_b: str) -> bool`; automatic
fallback = the existing `TypeBasedAliasOracle` (for constructs Scalpel can't
resolve and for parse/build failures, keeping the interface total).

**Verdict: Scalpel is VIABLE** as the L4 oracle — consumed as **SSA + copy/const
facts**, not as a turnkey points-to engine (Scalpel ships no Andersen/
Steensgaard heap analysis; its "alias pairs" are copy/const records over SSA).

**Environment.** Installed `python-scalpel==1.0b0` (self-reports
`__version__ == "1.0dev"`) via `uv pip install python-scalpel` into the repo's
uv-managed `.venv`, **CPython 3.12.13** (the project interpreter per
`pyvenv.cfg`/`.envrc`; the bare `python` on PATH is a pyenv shim to 3.14.0 and
is *not* the project env). `import scalpel` and `import codeanalyzer` both work
afterward. `uv pip install` does not touch `uv.lock`; installed packages live
in the gitignored `.venv`.

**Modules / classes / functions to consume, and their output shape:**

1. **CFG substrate** — `from scalpel.cfg import CFGBuilder`;
   `CFGBuilder().build_from_src(name, src)` (or `build_from_file`). CFG is
   **basic-block level**: nested-function CFGs in `cfg.functioncfgs` keyed by
   `(entry_id, func_name)`, params in `cfg.function_args`. `Block.statements`
   are **real `ast` statement nodes retaining `.lineno`/`.col_offset`**.
2. **SSA + alias** (there is *no* standalone alias module) — `from
   scalpel.SSA.const import SSA`; `ssa_results, const_dict =
   SSA().compute_SSA(func_cfg)`. **Statement-level** on top of the block CFG.
   - `ssa_results`: `dict[block_id] -> [ {var_name: {def_version_ints}} ]` (one
     dict per statement) — the use-def chain; a version set with >1 element is
     a **phi/merge**; an empty set is a param/global/external use. Attribute
     access-path names (`a.field`) appear as keys.
   - `const_dict`: `dict[(var_name, version)] -> ast value node` — the **alias
     carrier**. Value an `ast.Name` ⇒ a copy edge (`('b',0)->Name 'a'` means
     b aliases a); value an `ast.Attribute` ⇒ attribute-path store; `<ret>`
     is the return-value pseudo-name. Version counter is function-global, so
     `(name, version)` is a stable intra-function SSA identity.
3. **Type inference** (for the type-guided branch) — `from
   scalpel.typeinfer.typeinfer import TypeInference`; **file-based**:
   `TypeInference(name, entry_point=path).infer_types(); get_types()` →
   `list[dict]` rows `{file, line_number, function, type: set[str],
   variable|parameter}`; `'any'` = unknown.

**Mapping onto access-path strings / `(signature, node_id)`.** Both the repo
(`codeanalyzer/dataflow/cfg.py` `CFGNode` carries `start_line`/`end_line` +
`ast_node`) and Scalpel build from the **same source AST**, so the join is by
source position: function ⇄ `functioncfgs` key; node ⇄ statement AST
`(lineno, col_offset)` equal to `CFGNode.start_line`(/col) ⇒ same `node_id`
(the integration can even reuse the repo AST, making it identity not a match).
Scalpel var names use the same `base(.field|[*])*` grammar (normalize
subscripts to `[*]`, re-`k_limit`). `may_alias` = transitively close
`const_dict` `Name`→`Name`/attribute copies into per-function equivalence
classes; TRUE iff bases share a class **and** suffixes are prefix-compatible
(reuse `suffix_of`/prefix logic); for unrelated bases consult `TypeInference`
(incompatible concrete types ⇒ not aliased, `any`/unknown ⇒ may-alias); on
anything unresolved, fall back to `TypeBasedAliasOracle`.

**Probe answers.** (a) alias/SSA output **is** keyable to `(function,
line/col)` — verified end-to-end. (b) **CFG block-level, SSA/use-def
statement-level.** (c) modern syntax: `walrus :=` OK, `async`/`await`/`async
with` OK, `match`/`case` **does not crash** but is **not** split into per-arm
CFG branches (block-level imprecision, mitigated by the repo's own
statement-level CFG).

**Concerns carried to Stage 4.** Copy-only + intra-function (no heap
points-to; two params/aliased container elements are not modeled — the
type-guided branch + sound-leaning fallback must cover them); `match` arms
unbranched in Scalpel's CFG; dependency hygiene — `python-scalpel` is a
low-maintenance pre-release that drags in `typed-ast` (C build, historically
fails on 3.13+) and a `dataclasses` backport (harmlessly shadowed on 3.12), so
pin/constrain them, gate the import as a soft dependency (missing/broken
Scalpel → fallback, not a hard failure), and confirm the build across the repo's
supported 3.9–3.13+ range; `compute_SSA`'s return contract is discovered from
source (not a documented public API) — wrap it behind `ScalpelAliasOracle` to
contain upstream drift.

## Stage 5 — keystone conformance sweep (issue #98, schema_version 2.0.0)

The stage-5 pre-release conformance check against the canonical schema-v2
keystone (paired-fixture parity with codeanalyzer-typescript-v2) found five
deviations; all landed before the 1.0.0 tag so no per-language special cases
bake into the SDK's shared `cpg` models:

1. **Edge keys.** `call_graph` edges are `{src, dst, prov, weight}` — the
   containing list's name IS the edge type, so the `type: "CALL_DEP"` field is
   gone (with `source`/`target`/`provenance`). `ParamEdge`/`DdgEdge`/
   `SummaryEdge` were already keystone-shaped.
2. **No dangling endpoints.** Every call-graph endpoint joins the id space.
   Declared endpoints re-identify to their `can://` tree id; imported/builtin
   targets are homed as `can://python/<app>/@external/<module>/<name>` entries
   in `application.external_symbols` — keyed by that id, `kind:"external"`
   (mirrors TS `homeExternals`). The homed ids also enter `sig_to_id`, so L2
   `callee` backfill resolves external callees too.
3. **Containment vocabulary.** `PyModule.types`/`.functions`,
   `PyClass.callables`/`.types`, `PyCallable.callables`/`.types` — the
   historical `classes`/`methods`/`inner_classes`/`inner_callables` attribute
   names were renamed (attribute = wire key; no alias layer). This supersedes
   the earlier "field-name divergence, SDK maps at the boundary" decision.
4. **param_in/param_out emptiness** on the paired fixture was downstream of
   (2): once first-party endpoints stopped dangling, the interprocedural
   linking produced both edge families (regression-gated by
   `test_v2_keystone.py::test_param_edges_nonempty_on_interproc_chain`).
5. **Envelope.** `analyzer: {name, version}` added (version read from package
   metadata); `k_limit` is emitted at L3+ only (`Optional`, dropped by
   `exclude_none` below the dataflow levels).

Neo4j projection follow-through (same contract version 2.0.0, pre-release):
`:PyExternal` ghosts merge on `id` (the `@external` id) instead of the dotted
`signature`; `PY_CALLS` carries `prov` (was `provenance`); and relationship
identity gained an internal `_k` discriminant — `PY_DDG` merges per
`(var, prov)` and `PY_CFG_NEXT` per `kind`, because a plain endpoint-pair
MERGE collapses legitimately-distinct edges (per-variable dependences; a
conditional's true/false pair) and a live Bolt push then materializes fewer
relationships than the projection produced (caught by the opt-in
`test_neo4j_bolt.py` count gates).
