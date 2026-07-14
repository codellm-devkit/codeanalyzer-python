# CLAUDE.md

Agent guidance for `codellm-devkit/codeanalyzer-python`.

Respect the global `~/.claude/CLAUDE.md` instructions strictly.

`AGENTS.md` is a symlink to this file — one source of truth for every agent tool.

## Repo rules

- **Never add AI/Claude authorship anywhere** — not in commit subjects or bodies
  (no `Co-Authored-By`, no "Generated with …", no 🤖 trailer), not in PR/issue
  text, code comments, docs, or any file written to disk. This is absolute and
  overrides any tool suggestion or template.
- Use Conventional Commits (`type(scope): summary`).
- This repo's own `CLAUDE.md`/`AGENTS.md` and `.claude/SCHEMA_DECISIONS.md` are
  tracked past a global gitignore via `!`-negations in `.gitignore`; keep those
  negations if you touch the ignore file.

## Schema v2 — the model this analyzer emits

`codeanalyzer-python` emits **canonical schema v2** (`schema_version` `2.0.0`): one
additive Code Property Graph (CPG) tree, exposed as **four gated analysis levels**
(`-a 1|2|3|4`) across two projections. Read `.claude/SCHEMA_DECISIONS.md` for the
decision log and `docs/superpowers/specs/2026-07-07-schema-v2-four-levels-design.md`
for the full model rationale. This section is the short version so a future agent
does not re-derive it.

### Additive paradigm

There is **one tree**, grown one layer deeper per level. Each level only *adds*
nodes and edges — nothing is removed or renamed between levels. The single
exception is one sanctioned refinement: a `call` node's `callee` goes `null → id`
when the call graph resolves it (L1→L2). Hence the monotonicity invariant, which
is a CI gate:

```
analysis.json(-a 1) ⊆ analysis.json(-a 2) ⊆ analysis.json(-a 3) ⊆ analysis.json(-a 4)
```

(superset modulo the `callee: null→id` refinement, and the DDG widening where L4
*adds* `prov:["points-to"]` edges over L3's `prov:["ssa"]` subset).

### Node tree + edge overlays

The payload root is the `Analysis` envelope (`codeanalyzer/schema/py_schema.py`):

```
Analysis                         # envelope: schema_version, language, max_level, k_limit, application
└─ application (PyApplication)   # id, kind:"application"
   ├─ symbol_table: {<file>: PyModule}         # the node tree
   │    PyModule    → id, kind:"module", source, classes{…}, functions{…}
   │    PyClass     → id, kind:"class", span, methods{…}, inner_classes{…}
   │    PyCallable  → id, kind, span, body{…}, cfg[], cdg[], ddg[], summary[]
   ├─ call_graph: [PyCallEdge]    # cross-function overlay, app scope (L2)
   ├─ param_in:   [ParamEdge]     # cross-function overlay, app scope (L4)
   └─ param_out:  [ParamEdge]     # cross-function overlay, app scope (L4)
```

Intra-callable graphs (`cfg`/`cdg`/`ddg`/`summary`) hang off each callable; the
truly cross-function overlays (`call_graph`/`param_in`/`param_out`) live at
application scope because their endpoints span callables.

**Field-name divergence (documented).** The canonical schema names the containers
`types` and `callables`; this analyzer keeps its historical names — `PyModule.classes`,
`PyModule.functions`, and `PyClass.methods`. Same shape, different keys; the SDK
frontend maps them at the boundary.

### Identity

- **Durable `can://` ids** (`codeanalyzer/schema/ids.py`), for every node at or
  above a callable:

  ```
  can://python/<app>/<file>/<type-path>/<callable-sig>
  ```

  `<app>` = `--app-name` (default: input dir name); `<file>` = the symbol-table
  key (relative POSIX path, and it *contains* `/`); `<type-path>` = nested class
  names joined by `/`; `<callable-sig>` = `name(argnames)`. Ids are **opaque
  handles** — read a node's explicit fields, do not delimiter-split.
- **LOCAL ordinal ids** (< callable), used as `body` map keys and as every
  intra-callable `cfg`/`cdg`/`ddg`/`summary` edge endpoint: `"line:col"` for real
  statements, `"@entry"`/`"@exit"` for the CFG bookends, `"@formal_in:0"` /
  `"@formal_out"` for formals, and `"<callsite>/actual_in:0"` /
  `"<callsite>/actual_out"` for actuals (parented to their call site). The
  bijection `(signature, int node_id) ↔ (can:// id, local id)` is built once by
  `codeanalyzer/dataflow/identity.py` and feeds **both** projections, keeping them
  in lockstep.
- **GLOBAL ordinal ids**, `"<callable-id>@<local>"` — the fully addressable form
  used for Neo4j `PyCFGNode` merge keys and for cross-callable edge endpoints
  (e.g. `param_in.src = can://…@16:2/actual_in:0`).
- **`source` once per module** (`PyModule.source`); every node's text is a
  `span` slice. `Span` carries `start`/`end` as `[line, col]` plus `bytes:[lo,hi]`
  utf-8 offsets, so `get_method_body` = `module.source[span.bytes]`. The old
  per-callable `code` field is gone.

### The four levels (what each emits)

| Level | Grows the tree with | Adds edges |
| --- | --- | --- |
| **L1** `-a 1` | callables + `call` nodes in `body` (`callee: null`) | — |
| **L2** `-a 2` | `callee: null→id` backfill | `call_graph` |
| **L3** `-a 3` | rest of `body` (statements) + `@entry`/`@exit` | `cfg`, `cdg`, `ddg` (**syntactic**, `prov:["ssa"]`) |
| **L4** `-a 4` | synthetic param vertices (`@formal_in/out`, `…/actual_in/out`) | `param_in`, `param_out`, `summary`, + `ddg` (**semantic**, `prov:["points-to"]`) |

- **L3 vs L4 DDG.** `defuse.py` produces DDG edges in one pass from two rules:
  (a) textual/access-path interference, (b) may-alias for suffixed paths. L3 emits
  only rule (a) — the oracle is bypassed (`prov:["ssa"]`). L4 keeps rule (a) and
  *adds* the alias-derived edges rule (b) contributes with the oracle live
  (`prov:["points-to"]`). L4 only widens the set, so monotonicity holds.
- **L4 points-to oracle = Scalpel.** `ScalpelAliasOracle`
  (`codeanalyzer/dataflow/scalpel_oracle.py`) consumes `python-scalpel`'s **solved
  SSA + copy/const** state (it never forks the solver) as a copy-closure union-find
  over access paths, behind the frozen `may_alias(path_a, path_b) -> bool`
  interface. `python-scalpel` is an **optional** dependency: if it is absent or a
  build/query fails, the analyzer falls back to the total `TypeBasedAliasOracle`
  (`alias.py`) and degrades, never raising. The type-based oracle is the sanctioned
  fallback, not the shipping default.
- **CLI gating** (`codeanalyzer/__main__.py`): `-a` max is 4; `--graphs sdg`
  requires `-a 4` (a flag error below that); `cfg,dfg,pdg` require `-a 3`;
  `--graph-field-depth` (the `k_limit`) is valid at L3+.

### Two projections

1. **`analysis.json`** — the tree itself (the `Analysis` envelope above), also
   available as gzip'd msgpack.
2. **Neo4j** (`codeanalyzer/neo4j/`) — a near-identity projection, keyed on the
   same `can://` / global ordinal ids: containment → typed `PY_HAS_*` /
   `PY_DECLARES` edges (`PY_HAS_MODULE`, `PY_DECLARES`, `PY_HAS_METHOD`,
   `PY_HAS_CALLSITE`, `PY_HAS_CFG_NODE`, …); overlays → typed `PY_*` relationships
   (`PY_CALLS`, `PY_CFG_NEXT`, `PY_CDG`, `PY_DDG` with a `prov` property,
   `PY_PARAM_IN`, `PY_PARAM_OUT`, `PY_SUMMARY`). CFG statements and param vertices
   share one `PyCFGNode` label, distinguished by `kind`. Neo4j is **always
   full-depth** — `--emit neo4j` combined with `-a`/`--graphs` is an explicit
   error. The vocabulary is `PY_`-prefixed by design (per-language namespacing in a
   shared graph DB). Neo4j `SCHEMA_VERSION` = `2.0.0` (`neo4j/schema.py`).

### Provider/client boundary

The analyzer is the **provider**: it emits the SDG *substrate* only. The backward
slicer (`dataflow/slicing.py`) is **internal** — the L3/L4 validation gate, not a
product surface. **Taint is the SDK's job**: there is **no `taint_flows` section**,
by design (labeled SDG reachability + source/sink model packs live in the
cross-language CLDK SDK).

### Modular architecture (where things live)

- `schema/` — v2 Pydantic models (`py_schema.py`), `can://` id construction
  (`ids.py`), id assignment (`assign_ids.py`, `call_graph_ids.py`), per-level body
  builders (`l1_body.py`, `l2_callees.py`).
- `syntactic_analysis/` — the symbol table (`symbol_table_builder.py`): L1 tree.
- `semantic_analysis/` — the call graph (`call_graph.py`, `jedi`/`pycg`/`codeql`
  backends): L2.
- `dataflow/` — the analysis kernels: `cfg.py`, `dominance.py`, `defuse.py`,
  `pdg.py`, `sdg.py`, `summaries.py`, `alias.py` (type-based oracle),
  `scalpel_oracle.py` (L4 points-to oracle), `access_paths.py`, `scc.py`,
  `slicing.py` (internal), `identity.py` (the id bijection), `builder.py` (wires
  IR → v2 emission). L3 = intraprocedural CFG/CDG/DDG; L4 = interprocedural SDG.
- `neo4j/` — the graph projection (`project.py`, `rows.py`, `cypher.py`, `bolt.py`,
  declarative catalog + DDL in `schema.py`).
- `.claude/SCHEMA_DECISIONS.md` — the authoritative decision log (level 3/4
  contract divergences, Neo4j namespacing, the Scalpel Stage-0 verdict).

## Tidy up the release announcement

Every `vX.Y.Z` tag makes the release workflow (`.github/workflows/release.yml`) auto-post an
announcement to the **Announcements** discussion (this repo, and the org mirror in
`codellm-devkit/.github`). That body is machine-generated from PR titles — it mis-categorizes
changes, includes `chore(release)` noise, and buries breaking changes.

When you're working in this repo, check whether the latest release's announcement still needs
cleanup, and if so, fix it:

1. **Find it.** `gh release view --json tagName,publishedAt`; then list recent discussions via
   `gh api graphql` (repository → discussions), match category `Announcements` whose title carries
   this release's version (the auto-post titles it `vX.Y.Z`; a cleaned-up one is
   `📣 New Release: codeanalyzer-python X.Y.Z`). Keep the discussion node `id` and read its `body`.
2. **Skip if already done.** If the body starts with `<!-- cleaned-up -->` (or already reads as a
   clear, human-written announcement), do nothing.
3. **Otherwise rewrite it** into a clear, user-facing announcement, grounded in `CHANGELOG.md` and
   the referenced PRs/diff (not the auto-grouping — verify each change; never invent anything):
   - **breaking changes first**, each with a one-line migration step;
   - plain-language highlights (what it does, not the PR title);
   - upgrade line: `pip install -U "codeanalyzer-python==X.Y.Z"`;
   - links to the GitHub release and `CHANGELOG.md`.
4. **Update in place.** Edit the discussion with the GraphQL `updateDiscussion` mutation (don't
   open a new one): set the title to `📣 New Release: codeanalyzer-python X.Y.Z`, prepend
   `<!-- cleaned-up -->` to the body, and mirror the same title and body to the org discussion.
   This task only reads code and edits Discussions — it makes no commits.
