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
