# CLAUDE.md

Agent guidance for `codellm-devkit/codeanalyzer-python` (`canpy`).

Respect the global `~/.claude/CLAUDE.md` instructions strictly.

## What this project is

`canpy` is the CLDK Python static analyzer. It emits the canonical CLDK
`analysis.json` — a **symbol table** plus a **call graph** — and can project that same
analysis into a **Neo4j** property graph. It mirrors its
[TypeScript](https://github.com/codellm-devkit/codeanalyzer-typescript) (`cants`) and
[Java](https://github.com/codellm-devkit/codeanalyzer-java) sibling analyzers, so
output-shape parity with them is a first-class concern.

The engine is **[Jedi](https://jedi.readthedocs.io/)** (plus stdlib `ast`/`tokenize`)
for the symbol table, with a two-level call graph: level 1 is Jedi lexical resolution
(always on); level 2 (`--analysis-level 2`) adds the embedded
[PyCG](https://github.com/vitsalis/PyCG) flow analyzer, which recovers edges the lexical
pass misses. Merged edges keep a `provenance` tag (`jedi` / `pycg`); the schema also
reserves `joern`. (Heads-up: the README/`pyproject` still mention CodeQL, but there is
**no CodeQL provider** in the source — PyCG is the level-2 backend.)

## Architecture — follow the pipeline

The whole analyzer is one orchestrator: the **`Codeanalyzer`** context-manager class in
`codeanalyzer/core.py`, entered via **`analyze() -> PyApplication`**. Read it first;
everything else is a stage it calls, in order:

1. **cache load** — `_load_pyapplication_from_cache()` reuses `analysis_cache.json`
   unless `--eager`.
2. **symbol table** (`codeanalyzer/syntactic_analysis`) — `_build_symbol_table()` drives
   `SymbolTableBuilder.build_pymodule_from_file()` per file (serial or Ray), reusing
   unchanged files via mtime/size + SHA256 `content_hash`.
3. **call graph** (`codeanalyzer/semantic_analysis`) — `resolve_unresolved_constructors`,
   then `jedi_call_graph_edges` (provenance `jedi`); at `-a 2`, `_get_pycg_call_graph`
   adds PyCG edges (provenance `pycg`), coalesced by `merge_edges`.
4. **externals** — `filter_external_edges` drops lib→lib edges; `_compute_external_symbols`
   builds the `PyExternalSymbol` map for undeclared call targets (ghost nodes).
5. **assemble + cache** — `PyApplication.builder()…build()`, then `_save_analysis_cache()`.

Output is dispatched by the CLI (`codeanalyzer/__main__.py`, not `analyze()`): stdout
JSON, `analysis.json`/`analysis.msgpack`, or a Neo4j `emit_neo4j`/`emit_schema`.

The shape of everything is the **Pydantic schema** in `codeanalyzer/schema/py_schema.py`
(`PyApplication` is the top type). The Neo4j schema is a *separate*, versioned contract in
`codeanalyzer/neo4j/catalog.py` — treat it as a contract enforced by conformance tests.

## Directory map

| Path | Responsibility |
|------|----------------|
| `codeanalyzer/__main__.py` | Entry point + Typer CLI, flag parsing, output dispatch |
| `codeanalyzer/core.py` | `Codeanalyzer.analyze()` orchestrator — the spine |
| `codeanalyzer/options` | `AnalysisOptions` + `OutputFormat`/`EmitTarget`/`ShardStrategy` enums |
| `codeanalyzer/syntactic_analysis` | Symbol table (Jedi + `ast`/`tokenize` traversal) |
| `codeanalyzer/semantic_analysis` | Call-graph helpers (jedi edges, merge/filter); `pycg/` = level-2 provider + coupling-aware sharding |
| `codeanalyzer/schema` | `PyApplication` Pydantic models (the output contract) |
| `codeanalyzer/neo4j` | Graph projection: `catalog.py` (schema + `SCHEMA_VERSION`), `cypher.py` (snapshot), `bolt.py` (incremental push), `emit.py` (facade) |
| `codeanalyzer/utils` | logging, progress bar |
| `test` | Pytest suite + `test/fixtures` (flask, requests) |

## Commands

Tooling is **uv** + **hatchling** + **pytest** (no Makefile).

- `uv sync --all-groups` — install/sync deps.
- `uv run canpy --input /path/to/project` — run the analyzer from source.
- `uv run pytest` — run tests. Neo4j Bolt integration test (needs Docker):
  `RUN_CONTAINER_TESTS=1 uv run pytest test/test_neo4j_bolt.py -s`.
- `uv run canpy --emit schema > schema.neo4j.json` — regenerate the Neo4j schema contract.
- `uv run python scripts/update_readme.py` — regenerate the README's `canpy --help` block.

There is **no lint/typecheck command configured** (no ruff/black/mypy) — the package ships
`py.typed`, and `pre-commit` is the only quality gate in the dev group.

## I implement features myself — you assist

For feature work, **I write the implementation** to stay fluent in my own analyzer.
Act as a helper, not the author:

- **Don't write the feature code** or apply edits to implement it unless I explicitly
  ask ("write this", "implement X", "apply it"). Default to guiding, not doing.
- **Do** move me fast: explain the relevant stage, point at prior art (e.g. the existing
  Jedi edge builder in `semantic_analysis` as the template for a new provider), sketch
  signatures/types, outline an approach, and answer questions about the codebase.
- **Review on request:** when I share a diff or push, critique it — correctness,
  **parity with the TypeScript/Java backends**, schema conformance, missing tests, edge
  cases — and suggest concrete improvements.
- Scaffolding like tests or boilerplate is fine **when I ask**; otherwise leave the
  keyboard to me.
- If you think I'm about to go wrong, say so briefly and let me decide — don't pre-empt
  by implementing the fix.

## Rules

1. **Think before coding.** State assumptions explicitly; ask rather than guess. Push
   back when a simpler approach exists. Stop when confused.
2. **Simplicity first.** Guide me toward the minimum idiomatic code that solves the
   problem. Nothing speculative; no abstractions for single-use code.
3. **Issue → branch → work → PR.** Every change starts as an issue, on a branch named
   `feat/issue-XXX`, `fix/issue-XXX`, `chore/issue-XXX`, and lands via a PR.
4. **Guard the contract.** Changes to `codeanalyzer/schema` or Neo4j output must keep
   parity with the sibling analyzers and pass the schema conformance test
   (`test/test_neo4j_schema.py`, which also asserts the checked-in `schema.neo4j.json` is
   current).

## Goal-driven execution, as a teaching loop

Success is measured by the sole fact that **I understand it**. The success criterion:
I can point to the exact line of code where any feature lives, however remote or
obscure, and explain why it's there and how it behaves.

To that end, be my teacher and a Socratic one — not an answer key:

- Lead with questions that make me derive the answer; don't hand me the solution.
- Verify understanding, not just behavior — have me locate and explain the relevant
  LOC, walk edge cases, and predict what a change would do before running it.
- Teach, help improve, and strengthen the weak spots you surface; circle back to them.
- The loop closes when I can **teach it back** and place every feature on a line, not
  merely when the tests pass.
- Over the session, frequently — but not so much that I am stymied — ask spaced
  repetition questions so concepts are internalized.

Learning progress is tracked globally, not per-repo: see the SRS deck and the
"continual learning" defaults in `~/.claude/CLAUDE.md`.

# Auxiliary support tasks

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
