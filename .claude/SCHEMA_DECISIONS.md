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

## L4 graph completeness — port wiring + call anchoring (#115, 1.1.1)

Two connectivity gaps closed in the L4 emission (no vocabulary invented; both
decisions use surface the keystone already ships):

1. **Statement ↔ port ddg wiring, `prov:["reaching-defs"]`.** The SDG's
   binding edges — `def stmt → actual_in`, `actual_out → callsite`,
   `formal_in → first use`, `def/return → formal_out` — existed in the IR
   (`fg.extra_edges`, wired by `assemble_sdg`) and were emitted by the old v1
   `program_graphs` projection, but the v2 emission dropped them, leaving the
   port lattice an island (no end-to-end `flows_to` witness could cross a
   call). `emit_l4` now emits them onto each callable's `ddg` tagged
   `prov:["reaching-defs"]` — the label codeanalyzer-typescript already ships
   for its port-routing edges, so the prov vocabulary stays keystone-shared:
   `ssa` (L3 syntactic) ⊂ + `points-to` (L4 alias delta) + `reaching-defs`
   (L4 port bindings). Monotonicity: both L4 families are additive over the
   untouched ssa set, and every port endpoint exists only at L4.
2. **Call vertices anchor via `parent`, not the CFG spine.** A call nested in
   a larger statement (`y = f(x)`) keeps its own `"line:col"` body key and
   deliberately stays OFF the cfg spine — calls are dataflow satellites of
   their statement, not control-flow steps. From L3 (when statements exist)
   such a call carries `parent` = its enclosing statement's local id — the
   same anchoring `actual_in`/`actual_out` vertices already use. A bare-call
   statement shares its key with the CFG node (no self-parent). This is a
   sanctioned `null → value` refinement of `BodyNode.parent` at the L2→L3
   boundary, mirroring the `callee: null → id` refinement at L1→L2; the
   superset gates compare body keys, so no gate exception was needed.

## Neo4j label rename — `PyCFGNode` → `PyBodyNode` (issue #139)

The entry under "Level-3 CPG (Neo4j) — schema.neo4j.json 1.2.0" above records the
label as it was introduced; this supersedes the name, not the design.

`PyCFGNode` named a control-flow concept but held every `body{}` entry at every
level: `call` nodes from L1, `statement`/`return`/`branch`/`entry`/`exit` from L3,
and the `formal_in`/`formal_out`/`actual_in`/`actual_out` param vertices from L4.
Most are not CFG vertices — a nested call is deliberately a dataflow satellite
that never joins the spine (decision 2 in the L4 section above), and param
vertices are not control flow at all. After #120 moved call-site detail onto the
same node, the name was doubly wrong.

It was also a projection divergence: `analysis.json` calls the container `body{}`
while the graph called its contents CFG nodes — two words for one thing, across
the two projections that are supposed to agree.

- Label and merge label: `PyCFGNode` → **`PyBodyNode`**; constraint
  `pycfgnode_id` → `pybodynode_id`.
- Containment edge: `PY_HAS_CFG_NODE` → **`PY_HAS_BODY_NODE`**.
- `PY_CFG_NEXT`, `PY_CDG`, `PY_DDG` keep their names — those *are* control-flow
  and dependence edges. Only their endpoint label changed.
- The merge key is unchanged: still the global ordinal id
  `<callable can:// id>@<local body key>`, so the two projections still land on
  one identity.

`codeanalyzer-typescript` already ships `TSBodyNode` / `TS_HAS_BODY_NODE`, so this
adopts a name a sibling analyzer had already validated rather than coining a third.

**Breaking for an existing database**: nodes written under the old label are not
rewritten by a subsequent load, and the old uniqueness constraint remains. A graph
built before this change needs re-projecting, not migrating in place.

## 2026-08-25 — PyCG removed; `"defuse"` joins the call-edge `prov` vocabulary

Design: `docs/design/specs/2026-08-25-defuse-linker-call-graph-design.md`
(work item #148; supersedes #145 / PR #147).

Level 2's call graph is now Jedi (base) plus a per-callable **defuse linker**
that resolves remaining call sites through intra-callable def-use chains and
module-scope bindings — the Joern-style local-CPG pattern. PyCG's global
fixpoint is removed wholesale: on real projects it either never converged
(3h19m, 0/17 shards, seed-pinned) or had to be truncated by mechanisms that
made output load-dependent (#145).

- `PyCallEdge.prov` literal: `"pycg"` **removed**, `"defuse"` **added**
  (technique-named, like the DDG's `"ssa"`/`"points-to"`/`"reaching-defs"`;
  coined once — the parity clause binds siblings that adopt the technique).
- An edge found by both resolvers carries `["defuse", "jedi"]` (sorted union
  via `merge_edges`, as before).
- Refinement contract unchanged: the linker runs inside the L2 build, so
  `callee: null→id` remains the single sanctioned L1→L2 refinement.
- Neo4j projection unchanged (`PY_CALLS` carries `prov` as data).

## 2026-08-27 — Neutral `Artifact`/`Package` subgraph (issue #157, Task 6)

Design: `.superpowers/sdd/2026-08-27-artifacts-and-dependencies/task-6-brief.md`
(gitignored working brief — not committed; this entry is the durable record).

Projects `PyApplication.artifacts`/`dependencies`/`unresolved_imports` (Tasks
1-5) into the graph: new labels `Artifact`, `Package` (merge key `id`); new
rels `HAS_ARTIFACT` (PyApplication→Artifact), `DECLARES_DEPENDENCY`
(Artifact→Package, props `spec`/`kind`/`extras`/`prov`), `LOCKS`
(Artifact→Package, prop `version`), `PY_PROVIDES` (Package→PyExternal),
`PY_UNRESOLVED_IMPORT` (PyApplication→PyExternal, prop `prov`).

- **`Artifact`/`Package` deliberately break the `Py`-prefix convention** the
  Level-3 CPG section above establishes (`PySymbol`, `PyBodyNode`, `PY_CALLS`,
  …). Opposite rationale, same namespacing question: a manifest file or a
  PyPI package is not a Python-language concept — a TypeScript analyzer
  reading `package.json` in the same repo should MERGE onto the same
  `Artifact`/`Package` nodes, not create `TSArtifact`/`TSPackage` twins. The
  edges that stay this analyzer's own claim (`PY_PROVIDES` — "this analyzer
  resolved this import to this package", `PY_UNRESOLVED_IMPORT`) keep the
  `PY_` prefix; the nodes they connect to do not.
- **`PY_PROVIDES`/`PY_UNRESOLVED_IMPORT` target ids are minted here, not
  looked up.** `app.external_symbols` only homes call-graph endpoints
  (`Codeanalyzer._home_external_symbols` walks `app.call_graph` alone), so a
  module that is imported but never called — the common case for
  `PyDependency.provides_imports`, and the *only* case for an unresolved
  import — has no existing `:PyExternal` ghost to MERGE onto. The projection
  builds one with the same id shape `_call_endpoint`/`_home_external_symbols`
  already use for a dot-less (no `.`) call-graph signature: `<app can:// id>
  /@external/<name>`, `module` absent — and the same two-label
  `["PySymbol", "PyExternal"]` RowBuilder idiom every other ghost in this file
  uses (schema declares `PyExternal`'s merge label as `PySymbol`). If a call
  into that same bare name is ever projected too, both rows collapse onto one
  node under `RowBuilder`'s MERGE-by-`(label, id)` semantics — correctly,
  since they name the same real-world symbol.
- **`LOCKS` fans out to every lock artifact present**, not just the one that
  pinned a given dependency: `PyDependency.locked_version` merges all lock
  files' pins upstream (`build_dependency_view`) with no per-lock-file
  attribution left to project. One lock file is the overwhelmingly common
  case; revisit if a project with two conflicting lock files in one repo
  turns out to matter in practice.
- Always projected regardless of `-a` — this section is L1 data, identical at
  every analysis level (mirrors `analysis.json`), consistent with Neo4j's
  existing full-depth-always posture for `--emit neo4j`.

## 2026-08-27 — Artifacts, dependencies, and the `can://artifact/` namespace

Design: `docs/design/specs/2026-08-27-artifacts-and-dependencies-design.md`.

Schema v2 gains non-code coverage: `application.artifacts` (sibling map,
`symbol_table` stays code-only), `application.dependencies`, and
`application.unresolved_imports`. All three are L1 data — emitted identically
at every level, like entrypoints.

- **Artifact ids are language-neutral**: `can://artifact/<app>/<path>`. The
  first `can://` segment is now a namespace — a language for code nodes, the
  literal `artifact` for files — so sibling analyzers over the same repo emit
  the same artifact id (one node in a merged graph). Precondition: `<app>`
  must agree (`--app-name` pinned for joint analysis).
- **Dependency `prov` vocabulary** (coined once, parity clause applies):
  `declared`, `lockfile`, `installed-metadata`, `heuristic`. Deterministic
  default reads repo files only; `installed-metadata` requires the new
  `--resolve-installed` flag.
- **Neo4j**: neutral labels `:Artifact` / `:Package` (no `Py` prefix, shared
  MERGE targets across analyzers); `:Package.id` is a purl (`pkg:pypi/<name>`).
  `PY_PROVIDES` joins packages to existing-or-minted module-level `:PyExternal` ghosts, wiring
  dependencies into the call graph. New edges: `HAS_ARTIFACT`,
  `DECLARES_DEPENDENCY`, `LOCKS`, `PY_PROVIDES`, `PY_UNRESOLVED_IMPORT`.
- Capture broad (config files as nodes with `roles`), extract narrow
  (dependency manifests only this unit). Lock files backfill
  `locked_version`, never create records; transitive packages out of scope.
