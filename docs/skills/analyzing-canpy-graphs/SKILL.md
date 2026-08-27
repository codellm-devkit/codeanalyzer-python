---
name: analyzing-canpy-graphs
description: Use when querying codeanalyzer-python (canpy) output — analysis.json or the Neo4j projection — for call-graph, dataflow, taint, entrypoint, exit-point, or slicing questions, or when writing Cypher/JSON traversals over schema v2.
---

# Analyzing canpy graphs (schema v2)

One additive tree + typed edge overlays, two projections: `analysis.json` and Neo4j.
Vocabulary is fixed — **never guess a label, property, or key shape**; it is all in
[references/vocabulary.md](references/vocabulary.md). Query recipes (entrypoints, taint,
exit points, slicing, both projections) are in [references/recipes.md](references/recipes.md).

## What exists at which level

| `-a` | tree | edges |
| --- | --- | --- |
| 1 | callables + `call` body nodes (`callee: null`) + entrypoints | — |
| 2 | `callee` backfilled | `call_graph` / `PY_CALLS` (prov `jedi`/`defuse`) |
| 3 | full `body`, `@entry`/`@exit` | `cfg`, `cdg`, `ddg` (prov `ssa`, `reaching-defs`) |
| 4 | `formal_in/out`, `actual_in/out` vertices | `param_in`, `param_out`, `summary`, ddg widened with prov `points-to` |

Entrypoints (`is_entrypoint`, `entrypoint_frameworks` on callables **and** classes) are
L1 data — present at every level. Interprocedural anything needs `-a 4`.

## Identity in 20 seconds

- **`can://` ids are opaque** — read fields, never delimiter-split.
- **LOCAL ids** (body-map keys, intra-callable edge endpoints): `"line:col"`,
  `"@entry"`, `"@exit"`, `"@formal_in:<i>"`, `"@formal_out"`,
  `"<callsite-local>/actual_in:<i>"`, `"<callsite-local>/actual_out"`.
- **GLOBAL ids** (Neo4j `PyBodyNode.id`, `param_in/out` endpoints): `"<callable-id>@<local>"`.

## The four traps (each observed in baseline testing)

1. **Container key asymmetry.** `PyModule.types` and `PyClass.types` are keyed by the
   **dotted signature** (`"src.flask.sessions.SessionMixin"`); `callables` / `functions`
   are keyed by the **bare name** (`"permanent"`). Class signatures derive from the
   module path — a repo with a `src/` layout has `src.`-prefixed signatures.
2. **Span slicing is bytes, not str.** `span.bytes` are UTF-8 byte offsets:
   `module.source.encode("utf-8")[lo:hi].decode("utf-8")`. A plain `source[lo:hi]`
   is silently wrong after the first non-ASCII character in the file.
3. **Bound your taint walks.** `-[:PY_DDG|…*1..]->` enumerates paths — exponential on
   real corpora (odoo L4: 4.6M DDG edges). Use the bounded/frontier recipes in
   references/recipes.md.
4. **`PY_DDG` is one edge per `(var, prov)`** (internal `_k` discriminant), and ddg
   `prov` at L4 mixes `ssa`/`reaching-defs`/`points-to` — filter on `prov` when you
   need only the syntactic subset.

## Exit points, defined

Data leaves a callable through three channels; class-level = union over
`PY_HAS_METHOD`:

- **return channel** — `body` nodes with `kind: "return"` (L4: `@formal_out`).
- **external calls** — `PY_CALLS` into `:PyExternal` / `callee` present in
  `application.external_symbols`.
- **caller-visible writes** — ddg edges whose `var` is rooted at `self.` or a
  `global:` path; L4 `summary` edges expose the transitive in→out relation.

Taint is **not a schema section** (provider/client boundary: sources/sinks are the
SDK's job) — you compose it as reachability over `PY_DDG ∪ PY_PARAM_IN ∪
PY_PARAM_OUT ∪ PY_SUMMARY`. Recipes file has the exact patterns.
