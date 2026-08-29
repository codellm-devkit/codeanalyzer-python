---
name: analyzing-canpy-graphs
description: Use when querying the canpy (codeanalyzer-python) Neo4j graph — call-graph, dataflow, taint, slicing, entrypoint, exit-point, inheritance, dependency/SBOM, or configuration questions, or when writing any Cypher over schema v2's projection.
---

# Analyzing canpy graphs (schema v2, Neo4j projection)

One additive tree + typed edge overlays, projected as a property graph
(`--emit neo4j` → `graph.cypher` snapshot or live Bolt push). Vocabulary is
fixed — **never guess a label, property, or key** — it is all in
[references/vocabulary.md](references/vocabulary.md). The exhaustive analysis
catalogue (structure, calls, entrypoints, inheritance, imports, CFG/CDG/DDG,
slicing, taint, dependencies/artifacts, health metrics) is
[references/analyses.md](references/analyses.md); every recipe states its
minimum `-a` level.

## What exists at which level

| `-a` | tree | edges |
| --- | --- | --- |
| 1 | callables + `call` body nodes + entrypoints + artifacts/dependencies | `HAS_ARTIFACT`, `DECLARES_DEPENDENCY`, `LOCKS`, `PY_PROVIDES`, `PY_UNRESOLVED_IMPORT` |
| 2 | `callee` resolved | `PY_CALLS` (prov `jedi`/`defuse`), `PY_RESOLVES_TO` |
| 3 | full statement `body`, `@entry`/`@exit` | `PY_CFG_NEXT`, `PY_CDG`, `PY_DDG` (prov `ssa`, `reaching-defs`) |
| 4 | `formal_in/out`, `actual_in/out` vertices | `PY_PARAM_IN`, `PY_PARAM_OUT`, `PY_SUMMARY`, ddg widened `points-to` |

Artifacts, dependencies, unresolved imports, and entrypoints are **L1 data** —
present and identical at every level. Anything interprocedural needs `-a 4`.
Neo4j is always projected full-depth for the level analyzed.

## Identity in 20 seconds

- **`can://` ids are opaque** — match on properties, never delimiter-split ids.
- `PyBodyNode.id` is the GLOBAL ordinal `"<callable-id>@<local>"`; locals are
  `"line:col"`, `"@entry"`, `"@exit"`, `"@formal_in:<i>"`, `"@formal_out"`,
  `"<callsite>/actual_in:<i>"`, `"<callsite>/actual_out"`.
- `Artifact.id` is language-neutral (`can://artifact/<app>/<path>`);
  `Package.id` is a purl (`pkg:pypi/<name>`) — cross-language merge keys.

## The standing traps

1. **Bound every transitive walk.** Unbounded `*1..` enumerates paths —
   exponential on real corpora (odoo L4: 4.6M `PY_DDG` edges). Use `*1..N`
   with `DISTINCT`, or `shortestPath` for existence questions.
2. **Discriminated edges.** `PY_DDG` merges per `(var, prov)` and
   `PY_CFG_NEXT` per `kind`, `DECLARES_DEPENDENCY` per `kind` — via the
   internal `_k` property. Endpoint-pair matching alone under-counts.
3. **ddg `prov` mixes tiers at L4** (`ssa`/`reaching-defs`/`points-to`) —
   filter when you need only the syntactic subset.
4. **`:PyExternal` ghosts come in two grains**: member-level
   (`…/@external/<module>/<name>`, call-graph targets) and module-level
   (`…/@external/<module>`, dependency/import joins). Same label; join by the
   `module` property when you need both.
5. **Entrypoints live on properties** (`is_entrypoint`,
   `entrypoint_frameworks`) of `PyCallable` AND `PyClass` — there is no
   `:Entrypoint` label.

Taint is composed, not stored: reachability over
`PY_DDG ∪ PY_PARAM_IN ∪ PY_PARAM_OUT ∪ PY_SUMMARY` (provider/client boundary —
source/sink packs are the SDK's job). Exit points = returns + external calls +
caller-visible writes; both have full recipes in analyses.md.
