# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.1] - 2026-07-27

### Fixed
- **L4 SDG port layer is connected to the statement ddg** (#115): the binding
  edges `def stmt → actual_in`, `actual_out → callsite`, `formal_in → first use`
  and `return → formal_out` were built by the SDG assembler but dropped by the
  v2 emission, leaving the interprocedural port lattice an island — no
  end-to-end `flows_to(def, callee_formal)` path could cross a call. They are
  now emitted on each callable's `ddg` with `prov:["reaching-defs"]` (the same
  label codeanalyzer-typescript ships for its port-routing edges). Strictly
  additive over the L3 `ssa` set, so `L3 ⊆ L4` monotonicity holds.
- **Nested call vertices are anchored to their statement** (#115): a call inside
  a larger statement (`y = f(x)`) sits off the CFG spine by design (a dataflow
  satellite); from L3 it now carries `parent` = its enclosing statement's local
  id — the same anchoring `actual_in`/`actual_out` vertices use. Sanctioned
  `null → value` refinement at L2→L3, mirroring `callee: null → id` at L1→L2;
  recorded in `.claude/SCHEMA_DECISIONS.md`.

## [1.1.0] - 2026-07-27

### Changed
- The Scalpel-backed L4 points-to oracle is now **vendored** (`typed_ast`-free)
  and the default on all supported Python (3.9–3.14); `python-scalpel` is no
  longer an optional dependency and the `[scalpel]` extra is removed. On Python
  3.12+ (and anywhere the `[scalpel]` extra was not installed), L4
  `prov:["points-to"]` data-dependence edges are now Scalpel-precise rather than
  the coarser type-based over-approximation; the `prov:["ssa"]` set and the
  `L3 ⊆ L4` monotonicity invariant are unchanged. Adds `astor` as a runtime
  dependency.

## [1.0.3] - 2026-07-21

### Fixed
- **Analysis env provisioning is parso-version-aware** (#107): on hosts whose default
  `python3` is newer than the newest grammar the installed parso ships (e.g. Python 3.14
  with parso ≤ 0.8.4), every file failed jedi parsing and the run completed "successfully"
  with an empty symbol table. Provisioning now derives parso's supported ceiling at runtime
  from its shipped grammar files and prefers the newest supported interpreter on the host
  (versioned PATH names, then pyenv installs), falling back loudly only when none exists;
  an explicit `SYSTEM_PYTHON` is still honored, with a warning when unsupported. A run in
  which every discovered file fails now logs a prominent error instead of staying silent,
  and `parso>=0.8.5` (the first release with the 3.14 grammar) is a direct dependency.

## [1.0.2] - 2026-07-16

### Fixed
- **Neo4j `code` property was silently null** (#104): schema v2 removed the per-node `code`
  field (source lives once on `PyModule.source`, sliced by spans), but the Neo4j projection
  still read the old field — every `:PyClass`/`:PyCallable` node was written without `code`,
  deadening the `py_code_fts` fulltext index and the SDK's `RETURN c.code` queries. The
  projection now derives `code` at projection time by slicing the owning module's source
  with the node's utf-8 byte span. Regression gate:
  `test_projected_code_property_is_the_module_source_span_slice`.

## [1.0.1] - 2026-07-15

### Fixed
- **Deterministic output** (#99): the same flags on the same input now emit byte-identical
  `analysis.json`. Four independent nondeterminism sources were closed:
  - the CLI re-execs once with `PYTHONHASHSEED=0` (export the variable to opt out or pin another
    seed) — PyCG's capped fixpoint iterates hash-ordered sets, so an unpinned per-interpreter
    seed shifted its frontier arbitrarily (observed 1,527 vs 4,712 edges on the same input);
    Ray workers get the pinned seed via the runtime env;
  - the PyCG mini-project root is content-derived instead of a random `mkdtemp` (PyCG state keys
    on absolute module paths), with an exclusive sidecar lock so concurrent analyses of the same
    project serialize instead of deleting each other's tree mid-run;
  - entry points are sorted (was filesystem order) and the emitted `call_graph` is canonically
    ordered by `(src, dst)`;
  - Jedi inference candidates are tie-broken deterministically (sorted, not set order) — a
    union-typed receiver (e.g. `IOBase | BufferedRandom | TextIOWrapper`) previously resolved to
    a different member per run.
  Regression gates: `test_l2_runs_are_byte_identical` plus the #87 audit gate below.
- **L2 audit gate** (#87 acceptance): ≥95% of resolved non-constructor callsites must bind to the
  invoked attribute name, and no callsite may fall back to a class id when the class declares the
  exact method (`test_l2_audit_gate_callee_name_equality`). The underlying anchoring fix shipped
  in 1.0.0 via the v0.3.1 merge.

## [1.0.0] - 2026-07-14

### Added
- **Four analysis levels** (`-a 1|2|3|4`): L1 is symbol table and Jedi call graph; L2 adds
  PyCG call-graph edges (`--pycg-shard` scales to large apps); L3 adds intraprocedural dataflow
  (CFG/CDG/DDG, syntactic, `prov:["ssa"]`); L4 adds interprocedural SDG with synthetic
  parameter-in/out and summary edges, alias-aware DDG (`prov:["points-to"]`). Dataflow graphs are
  built in-process from the stdlib `ast` (exceptional CFGs with synthetic ENTRY/EXIT and
  exception/yield/await edges; Ferrante–Ottenstein–Warren control dependence; reaching-definitions
  data dependence over k-limited access paths; a Horwitz–Reps–Binkley SDG with parameter and
  bottom-up summary edges over the Tarjan SCC condensation). They are emitted inline on each
  callable as `body`/`cfg`/`cdg`/`ddg` under the v2 `application` tree — not a separate section.
- **`--graphs` and `--graph-field-depth` flags** (level 3+): `--graphs cfg,dfg,pdg,sdg` scopes the
  emitted program-graph sections (strict validation — unknown values or use below `-a 3` exit
  non-zero; `sdg` requires `-a 4`); `--graph-field-depth` sets the access-path k-limit (default 3),
  the bound that guarantees the interprocedural fixpoint terminates.
- **Context-sensitive backward slicing** as an SDG query (`codeanalyzer.dataflow.slicing`,
  HRB two-phase traversal). Taint is deliberately left to the CLDK SDK — post-SDG it is
  language-independent labeled reachability.
- **Scalpel points-to oracle** for L4 alias analysis (optional `python-scalpel` dependency:
  `pip install "codeanalyzer-python[scalpel]"`). Automatic type-based fallback when absent.
- **Neo4j schema v2.0.0** — the property graph is re-keyed onto canonical `can://` node IDs.
  New `PY_PARAM_IN`, `PY_PARAM_OUT`, `PY_SUMMARY` edges. `PY_DDG` carries `prov` property
  to distinguish syntactic (ssa) from semantic (points-to) dependence.
- **Neo4j CPG overlay at levels 3–4** (part of schema v2.0.0): `PyCFGNode` nodes plus the
  `PY_HAS_CFG_NODE`/`PY_CFG_NEXT`/`PY_CDG`/`PY_DDG` edges project each callable's control-flow and
  dependence graph, PY_-namespaced like every other row family so a multi-language database never
  mingles analyzers' dependence edges.

### Changed
- **BREAKING: canonical schema v2** (`analysis.json`). Structure is now a single additive CPG
  tree under an `Analysis` envelope (`schema_version`, `language`, `max_level`,
  `analyzer{name,version}`, `k_limit` at L3+, `application`). The old flat `symbol_table` +
  `call_graph` + separate `program_graphs` is replaced: consumers must read
  `application.symbol_table`, `application.call_graph` (now nested under `application`), and
  inline `body`/`cfg`/`cdg`/`ddg` on each callable. Nodes carry canonical `can://` identifiers.
  Module `source` is stored once with byte-offset spans; per-node `code` is dropped. Upgrade:
  pin `codeanalyzer-python==1.0.0` and update any code that read top-level
  `symbol_table`/`call_graph` to go through `application`.
- **BREAKING: keystone conformance sweep** (#98, part of the schema-v2 change above; key-for-key
  parity with the canonical CPG keystone shared by every analyzer):
  - Containment uses the shared canonical names: modules nest `types`/`functions`, classes nest
    `callables`/`types`, callables nest `callables`/`types`. The historical
    `classes`/`methods`/`inner_classes`/`inner_callables` keys are gone.
  - `call_graph` edges are `{src, dst, prov, weight}` — the list name is the edge type, so the
    `type: "CALL_DEP"` field and the old `source`/`target`/`provenance` keys are gone.
  - No dangling edge endpoints: imported/builtin call targets are homed as
    `can://<lang>/<app>/@external/<module>/<name>` entries in `application.external_symbols`
    (keyed by that id, `kind:"external"`); first-party endpoints always resolve to their tree id.
  - Envelope carries `analyzer: {name, version}`; `k_limit` is emitted only at L3+ (it is a
    dataflow bound and was meaningless at L1/L2).
  - Neo4j projection follows: `:PyExternal` ghosts merge on `id` (the `@external` id, not the
    dotted signature) and `PY_CALLS` carries `prov` (was `provenance`).

### Fixed
- **Neo4j edge identity no longer collapses distinct edges** between the same endpoint pair:
  `PY_DDG` merges per `(var, prov)` and `PY_CFG_NEXT` per `kind` via an internal `_k`
  relationship discriminant. Previously a plain endpoint-pair `MERGE` silently dropped
  per-variable data dependences (and could collapse a conditional's true/false CFG pair),
  so a live Bolt push materialized fewer relationships than the projection produced.
- **Full-run orphan prune now removes the L3/L4 CPG overlay** of a vanished module: the
  containment closure used for pruning was missing `PY_HAS_CFG_NODE`, stranding every
  `PyCFGNode` (and its dependence edges) in the database after its module was deleted.
- **Emitted `analysis.json` round-trips through the `Analysis` model again.**
  `PyVariableDeclaration.type` was required-but-nullable; `exclude_none` emission drops the
  key when Jedi cannot infer a variable's type, so re-validating the analyzer's own output
  failed with "Field required" (43k errors on an Odoo-scale run). The field now defaults to
  `None`; a round-trip gate rides `test_v2_keystone.py`.

## [0.3.1] - 2026-07-14

### Added
- **Class-attribute initializers** — `PyClassAttribute.initializer` captures the assignment RHS (`_name = 'account.account'` is now recoverable), projected as `PyAttribute.initializer` ([#83](https://github.com/codellm-devkit/codeanalyzer-python/issues/83)).
- **Structured call-site arguments** — `PyCallsite.arguments: [{ast_kind, inferred_type?}]` separates the AST category from the Jedi-inferred type (absent when unknown), projected as `PyCallSite.arguments_json` ([#86](https://github.com/codellm-devkit/codeanalyzer-python/issues/86)).
- **Snapshot provenance** — `PyApplication.repository {uri, revision, dirty}` (git queried read-only at analysis time; absent for non-checkouts) and `PyApplication.analyzer {name, version, config}`, mirrored as flattened `:PyApplication` properties ([#85](https://github.com/codellm-devkit/codeanalyzer-python/issues/85)).
- **Internal import resolution** — each `PyImport` gains `resolved_module` (the analyzed target module's file key); resolved imports project as `PY_IMPORTS` edges to the real `:PyModule` (one edge per module pair, raw spellings preserved in a `spellings` array), externals keep `:PyPackage`, and unresolved relative spellings (`.`, `.foo`, `..x`) no longer mint bogus `:PyPackage` nodes ([#82](https://github.com/codellm-devkit/codeanalyzer-python/issues/82)).
- Neo4j `SCHEMA_VERSION` bumped `1.1.0 → 1.2.0` (additive: new properties on `PyAttribute`, `PyCallSite`, `PyApplication`, `PY_IMPORTS`; `PY_IMPORTS` may now target `:PyModule`).

### Deprecated
- `PyCallsite.argument_types` (and the `PyCallSite.argument_types` graph property): it silently mixes AST node categories with inferred type names in one list. Use `arguments`/`arguments_json` instead; the legacy field is unchanged and will be removed in schema v2 ([#86](https://github.com/codellm-devkit/codeanalyzer-python/issues/86)).

### Fixed
- Attribute-call callees (`receiver.method(...)`) now resolve to the invoked method instead of the receiver's type — Jedi inference was anchored at the call expression's first character, i.e. the receiver token, so nearly every method call's `callee_signature` (and the Neo4j `PY_RESOLVES_TO` edge) pointed at the receiver's class ([#80](https://github.com/codellm-devkit/codeanalyzer-python/issues/80)).
- `PyCallsite.return_type` now holds the inferred type of the call *result* (the callee's inferred return type, or the instance for a constructor call), and is absent when Jedi cannot tell. Previously it held the type inferred at the call expression's start — effectively the receiver's type ([#80](https://github.com/codellm-devkit/codeanalyzer-python/issues/80)).
- `SymbolTableBuilder` no longer crashes with `ValueError` when given a relative `project_dir`: the path is normalized with `os.path.abspath` at construction (symlinks intact, matching Jedi's own path normalization) ([#90](https://github.com/codellm-devkit/codeanalyzer-python/issues/90)).
- Path-derived fallback signatures no longer corrupt module prefixes containing `.py` as a substring (`odoo/tools/pycompat.py` → `odoo.toolscompat`): only the terminal `.py` suffix is stripped, via a single `_fallback_signature` helper replacing three copies of the buggy expression ([#84](https://github.com/codellm-devkit/codeanalyzer-python/issues/84)).

## [0.3.0] - 2026-06-27

### Added
- **`--analysis-level {1,2}`** (reintroduced): 1 is symbol table + Jedi call graph, 2 adds the PyCG call graph.
- **Coupling-aware PyCG sharding** (`--pycg-shard`) so level 2 scales to large apps. Shards are chosen by Jedi module coupling (strongly-connected-component condensation, so import cycles never split, plus Louvain community detection) instead of a flat file count, so few call edges are severed between shards. PyCG runs on each shard inside a symlink mini-project that bounds it to that shard's files. Ray-parallel.
- **Iterative decomposition of runaway shards.** PyCG's fixpoint can diverge on heavy metaclass / mixin code. A shard that hits the wall-clock timeout is re-sharded at half the budget and re-run, down to a floor (10 files). The residue that still diverges, or an atomic cycle that will not split, falls back to Jedi-only coverage. On the Odoo benchmark (1028 modules) this recovered 22210 PyCG edges versus 17149 for the best uniform ceiling, losing only 20 of 1028 files instead of a whole shard.
- **New flags**: `--pycg-shard-strategy {jedi,package}` (default `jedi`), `--pycg-shard-ceiling` (default 100, the starting per-shard budget), `--pycg-shard-timeout` (default 120, per-shard wall clock), `--pycg-max-iter` (default 50, caps PyCG fixpoint passes so a divergent shard returns a partial graph instead of hanging).

### Changed
- **BREAKING: CodeQL is replaced by PyCG as the level 2 call graph backend.** `--codeql/--no-codeql` is removed in favor of `--analysis-level`. Edge `provenance` literal `codeql` becomes `pycg`. New dependency: `pycg` (Apache 2.0). The `codeanalyzer.semantic_analysis.codeql` package is removed.

### Fixed
- The shard planner keys its module graph by file path. `PyModule.module_name` is only the file stem (every `__init__.py`, `models.py`, ...), so keying by name collided and silently dropped files from shards.
- PyCG no longer follows imports into an in-tree dependency venv (e.g. `.codeanalyzer/`) during the whole-project level 2 run: it runs in a symlink mini-project whose root holds project source only, so dependencies resolve outside the bound and stay ghost nodes.
- `_uv_bin` uses only the vendored `uv` from the `uv` PyPI dependency and no longer falls back to a `uv` on `PATH`, so the analyzer always uses the pinned binary. Returns `None` only if the package is missing, in which case callers fall back to pip.

## [0.2.1] - 2026-06-22

### Added
- **Homebrew tap** — `brew install codellm-devkit/tap/codeanalyzer-python`. The release workflow auto-generates a formula (`packaging/homebrew/generate_formula.sh`) that installs the pinned PyPI release as an isolated `uv` tool, and pushes it to `codellm-devkit/homebrew-tap`. Because the package is pure-Python with heavy native dependencies (`ray`, `pandas`, `numpy`), the formula depends on `uv` and runs the release via `uvx` rather than vendoring every transitive dependency as a Homebrew resource.
- **First-class external symbols** — `PyApplication.external_symbols` (a `{signature → PyExternalSymbol{name, module}}` map) records call-graph targets outside the analyzed project, mirroring the `codeanalyzer-typescript` backend. `analysis.json` now carries external info that was previously only a bare target string, and the Neo4j projection emits `:PyExternal` authoritatively from it ([#44](https://github.com/codellm-devkit/codeanalyzer-python/issues/44)).
- **`--no-venv` / `--venv` flag** — skip virtualenv creation and dependency installation and resolve imports against the ambient Python interpreter. Useful in CI / containers where the project's dependencies are already installed, for sandboxed runs without network, and for speed ([#46](https://github.com/codellm-devkit/codeanalyzer-python/issues/46)).

### Changed
- The per-project analysis virtualenv is now installed with **`uv`** (parallel downloads + a shared global cache; falls back to `pip`), and is now **wired to Jedi** — previously `self.virtualenv` stayed `None`, so the install was never used by the symbol-table builder ([#47](https://github.com/codellm-devkit/codeanalyzer-python/issues/47)).
- Neo4j `:PyExternal` gains a `module` property; `SCHEMA_VERSION` bumped `1.0.0 → 1.1.0` (additive) ([#44](https://github.com/codellm-devkit/codeanalyzer-python/issues/44)).

### Fixed
- `--emit neo4j` no longer drops call edges whose target is a bare imported module name (e.g. `os`, `re`, `json`): a `:PyPackage` name can no longer shadow a call target's `:PySymbol` signature, and the node-identity tracking is keyed by `(label, value)` so deferred `PY_EXTENDS` / `PY_RESOLVES_TO` edges can't be shadowed either ([#44](https://github.com/codellm-devkit/codeanalyzer-python/issues/44)).
- `--emit neo4j` (Bolt) full-run orphan prune is now scoped to the `:PyApplication` anchor, so a full-run push for one application no longer deletes another application's modules from a shared database ([#45](https://github.com/codellm-devkit/codeanalyzer-python/issues/45)).

## [0.2.0] - 2026-06-20

### Added
- **Neo4j property-graph output** (`--emit neo4j`). The same in-memory analysis (`PyApplication`) is projected to a labeled property graph, mirroring the `codeanalyzer-typescript` backend. Node labels are `Py`-prefixed and relationship types are `PY_`-prefixed (e.g. `:PyClass`, `PY_CALLS`) so multiple language analyzers can coexist in one database without label or relationship-type collisions. Two writers:
  - **`graph.cypher` snapshot** (default) — a self-contained Cypher script (constraints + indexes, a scoped wipe of the project's prior subgraph, then batched `UNWIND … MERGE`). Load it with `cypher-shell < graph.cypher`. Needs no extra dependencies.
  - **Live Bolt push** (`--neo4j-uri`) — an **incremental** writer: only modules whose `content_hash` changed are rewritten, and on a full run modules whose source file vanished are pruned. Requires the optional `neo4j` driver (`pip install 'codeanalyzer-python[neo4j]'`).
- **`--emit schema`** — emit the machine-readable, version-stamped Neo4j schema contract (`schema.json`: node labels, relationships, properties, constraints, indexes). Needs no project; bundled in every release as a GitHub Release asset and checked in as `schema.neo4j.json`. A `schema_version` (`1.0.0`) is stamped onto every graph's `:PyApplication` node.
- **New CLI options** mirroring the TypeScript analyzer's entrypoints: `--emit {json,neo4j,schema}`, `--app-name`, `--neo4j-uri`, `--neo4j-user`, `--neo4j-password`, `--neo4j-database`. `-i/--input` is now optional (not required for `--emit schema`). The four Neo4j connection options also read from the standard `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` environment variables when the flag is omitted (an explicit flag wins), so the password need not appear in shell history or the process list.
- **`codeanalyzer.neo4j`** package: `catalog` (the single source-of-truth schema catalog), `project` (pure IR → graph rows), `cypher` (snapshot writer), `bolt` (incremental writer), and `rows` (the output-agnostic intermediate).
- **Schema conformance test** (`test/test_neo4j_schema.py`, always runs) — asserts the emitter never produces a label/relationship/property the catalog doesn't declare, and that the checked-in `schema.neo4j.json` is regenerated.
- **Neo4j Testcontainers integration test** (`test/test_neo4j_bolt.py`, opt-in via `RUN_CONTAINER_TESTS=1`) — spins up a real Neo4j and asserts the pushed graph, idempotent re-push, vanished-declaration cleanup, and full-run orphan pruning.
- **Install script** (`packaging/install/canpy-installer.sh`) — a `curl … | sh` installer that provisions the CLI via uv / pipx / pip, published as a release asset.
- **`schema-uml.drawio`** — a clean UML of the `analysis.json` schema (the `PyApplication` containment tree).

### Changed
- **The CLI command is now `canpy`** (was `codeanalyzer`), matching the `cants` (TypeScript) sibling. The PyPI package name is unchanged (`codeanalyzer-python`), as is the importable `codeanalyzer` module. The old `codeanalyzer` command is retained as a **deprecated alias** that prints a notice (to stderr) and then runs unchanged; it will be removed in a future release.
- The README `canpy --help` block is now generated from the live CLI (`scripts/update_readme.py`, between `<!-- BEGIN/END canpy-help -->` markers) so it can't drift from the code.
- The release workflow now installs the `[neo4j]` extra, syncs both the README `--help` block and `schema.neo4j.json` from source before publishing, and uploads the schema contract (`schema.json`) and installer script as GitHub Release assets.

## [0.1.15] - 2026-05-15

### Fixed
- **CodeQL call-graph edges silently dropped on `(file, start_line)` join miss** ([#25](https://github.com/codellm-devkit/codeanalyzer-python/issues/25)). CodeQL endpoints were matched back into Jedi's `PyCallable` signature space by an exact `(absolute_file_path, start_line)` key; when CodeQL and Jedi disagreed on a definition's start line (commonly with decorated functions), the caller lookup missed and the entire edge was discarded (callee misses degraded to ghost nodes). Replaced the exact-only index with a resolution ladder: exact `(file, start_line)` → candidates sharing `(file, short_name)` (single candidate taken directly, else nearest `start_line` among those whose parameter count matches the CodeQL positional arity) → no match (caller skipped / callee ghost, as before). The CodeQL query now emits `Function.getName()` and positional arity for both endpoints. Jedi's parameter count includes `*args`/`**kwargs`/keyword-only slots while CodeQL's arity is positional only, so the arity filter is exact for plain signatures and yields to the nearest-line tiebreak otherwise.

## [0.1.14] - 2026-05-13

### Added
- **Call graph in analysis output**: `PyApplication.call_graph: List[PyCallEdge]`. Every run now produces a call graph in addition to the symbol table. Edges carry `source`, `target` (both `PyCallable.signature`), `weight`, and `provenance` (`jedi` / `codeql` / `joern`).
- **`call_graph` module** (`codeanalyzer.semantic_analysis.call_graph`) with `to_digraph` / `from_digraph` networkx adapters, `jedi_call_graph_edges`, and `merge_edges`. Endpoints absent from the symbol table become ghost nodes so RPC / third-party / framework edges are preserved.
- **CodeQL Python query** rewritten against the CodeQL Python library (was Java idioms before). Resolves direct calls and constructor calls via `ClassValue.lookup("__init__")`, using the modern `Value.getACall()` predicate (CodeQL Python 7.x).
- **`augment_call_sites`**: when `--codeql` is enabled, CodeQL backfills `PyCallsite.callee_signature` entries Jedi left unresolved.
- **`resolve_unresolved_constructors`**: heuristic fallback that walks the symbol table by class short-name and scope to fill in constructor sites neither Jedi nor CodeQL resolved (common for classes nested inside functions/methods). Synthesizes `<class>.__init__` signatures.
- **`iter_classes_in_symbol_table`**: full recursive walker over classes — including inner classes, classes nested in functions, and classes nested in class methods.

### Changed
- **BREAKING**: Removed `--analysis-level` / `analysis_level`. The call graph is built unconditionally; use `--codeql/--no-codeql` to control CodeQL participation. Jedi-derived edges are always available.
- **Jedi constructor calls now resolve to `<class>.__init__`** (was: bare `<class>`). When `script.infer()` returns a class, the qualified name is rewritten to point at the constructor — matching where method `PyCallable`s actually live in the symbol table. `PyCallsite.is_constructor_call` now reflects Jedi's type inference (was: `method_name == "__init__"`, only true for explicit `obj.__init__()` calls).
- **`_call_sites` scope correctness**: replaced naive `ast.walk` with `_iter_calls_in_scope`, which stops at nested `FunctionDef` / `AsyncFunctionDef` / `ClassDef` bodies (those have their own `PyCallable.call_sites`). Decorators, default arguments, return annotations, base classes and class keyword args are still walked since they execute in the enclosing scope. Previously, outer functions over-attributed every call from every nested definition.
- CodeQL CLI binary is now downloaded into `<cache_dir>/codeql/bin/` (per-project, respecting `--cache-dir`) and discovered before any CodeQL operation — including when the database cache is reused. The downloaded archive is removed after extraction.
- `CodeQLQueryRunner` now accepts the resolved binary path instead of relying on `PATH`. The temporary `.ql` file is written **inside** a per-project qlpack (`<cache_dir>/codeql/qlpack/`) whose `codeql/python-all` dependency is resolved once via `codeql pack install`, eliminating the lockfile / search-path gymnastics.

### Fixed
- **`zipfile` extraction dropped Unix permissions** on the CodeQL CLI launcher, causing `PermissionError` on first query run. Entries are now extracted with their stored `external_attr` mode applied, plus a defensive `chmod +x` on the resolved binary.
- **`rglob("codeql")` matched the bundled `codeql/codeql/` directory** before the launcher file, returning a directory instead of an executable. Both `CodeQLLoader` and `_ensure_codeql_bin` now filter to `is_file()`.
- **`CodeQLQueryRunner` crashed on subprocess errors** with `'NoneType' object has no attribute 'stderr'` because `stderr=None` returns `None` from `communicate()`. Now captures `stderr=PIPE` and decodes bytes safely.

## [0.1.13] - 2025-07-22

### Improved
- **CLI Help Documentation**: Comprehensive help text added for all command-line options
  - Added descriptive help messages for all CLI parameters including `--output`, `--format`, `--analysis-level`, etc.
  - Enhanced user experience with clear option descriptions in `--help` output
  - Improved CLI parameter organization using `Annotated` type hints for better maintainability
  - Added case-insensitive support for `--format` option
  - Updated verbosity option help to clearly indicate multiple usage (`-v`, `-vv`, `-vvv`)

### Technical Details
- Refactored CLI function signature to use consistent `Annotated` type hint pattern
- Added comprehensive help text for all 12 command-line options
- Improved code organization and type safety in CLI parameter definitions

## [0.1.12] - 2025-07-21

### Changed
- **BREAKING CHANGE**: Refactored `Codeanalyzer` constructor to use `AnalysisOptions` dataclass [in response to #12](https://github.com/codellm-devkit/codeanalyzer-python/issues/12)
  - Replaced multiple individual parameters with single `AnalysisOptions` object for cleaner API
  - Improved type safety and configuration management through centralized options structure
  - Enhanced maintainability and extensibility for future configuration additions
  - Updated CLI integration to create and pass `AnalysisOptions` instance
  - Maintained backward compatibility in terms of functionality while improving code architecture

### Added
- New `AnalysisOptions` dataclass in `codeanalyzer.options` module [in response to #12](https://github.com/codellm-devkit/codeanalyzer-python/issues/12)
  - Centralized configuration structure with all analysis parameters
  - Type-safe configuration with proper defaults and validation
  - Support for `OutputFormat` enum integration
  - Clean separation between CLI and library configuration handling

### Technical Details
- Added new `codeanalyzer.options` package with `AnalysisOptions` dataclass
- Updated `Codeanalyzer.__init__()` to accept single `options` parameter instead of 9 individual parameters
- Modified CLI handler in `__main__.py` to create `AnalysisOptions` instance from command line arguments
- Improved code organization and maintainability for configuration management
- Enhanced API design following best practices for parameter object patterns

## [0.1.11] - 2025-07-21

### Fixed
- **CRITICAL**: Fixed NumPy build failure on Python 3.12+ (addresses [#19](https://github.com/codellm-devkit/codeanalyzer-python/issues/19))
  - Updated NumPy dependency constraints to handle Python 3.12+ compatibility
  - Split NumPy version constraints into three tiers:
    - `numpy>=1.21.0,<1.24.0` for Python < 3.11
    - `numpy>=1.24.0,<2.0.0` for Python 3.11.x
    - `numpy>=1.26.0,<2.0.0` for Python 3.12+ (requires NumPy 1.26+ which supports Python 3.12)
  - Resolves `ModuleNotFoundError: No module named 'distutils'` errors on Python 3.12+
  - Ensures compatibility with Python 3.12 which removed `distutils` from the standard library
- Fixed Pydantic v1/v2 compatibility issues in JSON serialization throughout codebase
  - Added comprehensive Pydantic version detection and compatibility layer
  - Introduced `model_dump_json()` and `model_validate_json()` helper functions for cross-version compatibility
  - Fixed `PyApplication.parse_raw()` deprecated method usage (replaced with `model_validate_json()`)
  - Updated CLI output methods to use compatible serialization functions
  - Resolved forward reference updates only for Pydantic v1 (v2 handles these automatically)

### Changed
- Enhanced Pydantic compatibility infrastructure in schema module
  - Added runtime Pydantic version detection using `importlib.metadata`
  - Created compatibility abstraction layer for JSON serialization/deserialization
  - Improved forward reference resolution logic to work with both Pydantic v1 and v2
  - Updated all JSON serialization calls to use new compatibility functions
  - Better error handling for missing Pydantic dependency

### Technical Details
- Added `packaging` dependency for robust version comparison
- Enhanced schema module with runtime version detection and compatibility helpers
- Updated core analysis caching system to use compatible Pydantic JSON methods
- Improved CLI output formatting with cross-version Pydantic support

## [0.1.10] - 2025-07-20

### Added
- Ray distributed processing support for parallel symbol table generation (addresses [#16](https://github.com/codellm-devkit/codeanalyzer-python/issues/16))
- `--ray/--no-ray` CLI flag to enable/disable Ray-based distributed analysis
- `--skip-tests/--include-tests` CLI flag to control whether test files are analyzed (improves analysis performance)
- `--file-name` CLI flag for single file analysis (addresses part of [#16](https://github.com/codellm-devkit/codeanalyzer-python/issues/16))
- Incremental caching system with SHA256-based file change detection
  - Automatic caching of analysis results to `analysis_cache.json`
  - File-level caching with content hash validation to avoid re-analyzing unchanged files
  - Significant performance improvements for subsequent analysis runs
  - Cache reuse statistics logging
- Custom exception classes for better error handling in symbol table building:
  - `SymbolTableBuilderException` (base exception)
  - `SymbolTableBuilderFileNotFoundError` (file not found errors)
  - `SymbolTableBuilderParsingError` (parsing errors)
  - `SymbolTableBuilderRayError` (Ray processing errors)
- Enhanced PyModule schema with metadata fields for caching:
  - `last_modified` timestamp tracking
  - `content_hash` for precise change detection
- Progress bar support for both serial and parallel processing modes
- Enhanced test fixtures including xarray project for comprehensive testing
- Comprehensive `__init__.py` exports for syntactic analysis module
- Smart dependency installation with conditional logic:
  - Only installs requirements files when they exist (requirements.txt, requirements-dev.txt, dev-requirements.txt, test-requirements.txt)
  - Only performs editable installation when package definition files are present (pyproject.toml, setup.py, setup.cfg)
  - Improved virtual environment setup with better dependency detection and installation logic

### Changed
- **BREAKING CHANGE**: Updated Python version requirement from `>=3.10` to `>=3.9` for broader compatibility (closes [#17](https://github.com/codellm-devkit/codeanalyzer-python/issues/17))
- **BREAKING CHANGE**: Updated dependency versions with more conservative constraints for better stability:
  - `pydantic` downgraded from `>=2.11.7` to `>=1.8.0,<2.0.0` for stability
  - `pandas` constrained to `>=1.3.0,<2.0.0`
  - `numpy` constrained to `>=1.21.0,<1.24.0`
  - `rich` constrained to `>=12.6.0,<14.0.0`
  - `typer` constrained to `>=0.9.0,<1.0.0`
  - Other dependencies updated with conservative version ranges for better compatibility
- Major Architecture Enhancement: Complete rewrite of analysis caching system
  - `analyze()` method now implements intelligent caching with PyApplication serialization
  - Symbol table building redesigned to support incremental updates and cache reuse
  - File change detection using SHA256 content hashing for maximum accuracy
- Enhanced `Codeanalyzer` constructor signature to accept `file_name` parameter for single file analysis
- Refactored symbol table building from monolithic `build()` method to cache-aware file-level processing
- Enhanced `Codeanalyzer` constructor signature to accept `skip_tests` and `using_ray` parameters
- Improved error handling with proper context managers in core analyzer
- Updated CLI to use Pydantic v1 compatible JSON serialization methods
- Reorganized syntactic analysis module structure with proper exception handling and exports
- Enhanced virtual environment detection with better fallback mechanisms
- Symbol table builder now sets metadata fields (`last_modified`, `content_hash`) for all PyModule objects

### Fixed
- Fixed critical symbol table bug for nested functions (closes [#15](https://github.com/codellm-devkit/codeanalyzer-python/issues/15))
  - Corrected `_callables()` method recursion logic to properly capture both outer and inner functions
  - Previously, only inner/nested functions were being captured in the symbol table
  - Now correctly processes module-level functions, class methods, and all nested function definitions
- Fixed nested method/function signature generation in symbol table builder
  - Corrected `_callables()` method to properly build fully qualified signatures for nested structures
  - Fixed issue where nested functions and methods were getting incorrect signatures (e.g., `main.__init__` instead of `main.outer_function.NestedClass.__init__`)
  - Added `prefix` parameter to `_callables()` and `_add_class()` methods to maintain proper nesting context
  - Signatures now correctly reflect the full nested hierarchy (e.g., `main.outer_function.NestedClass.nested_class_method.method_nested_function`)
  - Updated class method processing to pass class signature as prefix to nested callable processing
  - Improved path relativization to project directory for cleaner signature generation
- Fixed Pydantic v2 compatibility issues by reverting to v1 API (`json()` instead of `model_dump_json()`)
- Fixed missing import statements and type annotations throughout the codebase
- Fixed symbol table builder to support individual file processing for distributed execution
- Improved error handling in virtual environment detection and Python interpreter resolution
- Fixed schema type annotations to use proper string keys for better serialization
- Enhanced import ordering and removed unnecessary blank lines in CLI module
- Improved virtual environment setup reliability:
  - Fixed unnecessary pip installs by adding conditional logic to only install when dependencies are available
  - Only attempts to install requirements files if they actually exist in the project
  - Only performs editable installation when package definition files are present
  - Prevents errors and warnings from attempting to install non-existent dependencies

### Technical Details
- Added Ray as a core dependency for distributed computing capabilities (addresses [#16](https://github.com/codellm-devkit/codeanalyzer-python/issues/16))
- Implemented `@ray.remote` decorator for parallel file processing
- Comprehensive caching system implementation:
  - `_load_pyapplication_from_cache()` and `_save_analysis_cache()` methods for PyApplication serialization
  - `_file_unchanged()` method with SHA256 content hash validation
  - Cache-aware symbol table building with selective file processing
  - Automatic cache statistics and performance reporting
- Enhanced progress tracking for both serial and parallel execution modes with Rich progress bars
- Updated schema to use `Dict[str, PyModule]` instead of `dict[Path, PyModule]` for better serialization
- Extended PyModule schema with optional `last_modified` and `content_hash` fields for caching metadata
- Added comprehensive exception hierarchy for better error classification and handling
- Refactored symbol table building into modular, file-level processing suitable for distribution
- Enhanced Python interpreter detection with support for multiple version managers (pyenv, conda, asdf)
- Added `hashlib` integration for file content hashing throughout the codebase
- Enhanced virtual environment setup logic:
  - Modified `_add_class()` method to accept `prefix` parameter and pass class signature to method processing
  - Updated `_callables()` method signature to include `prefix` parameter for nested context tracking  
  - Enhanced signature building logic to use prefix when available, falling back to Jedi resolution for top-level definitions
  - Fixed recursive calls to pass current signature as prefix for proper nesting hierarchy
  - Implemented conditional dependency installation with existence checks for requirements files and package definition files

### Notes
- This release significantly addresses the performance improvements requested in [#16](https://github.com/codellm-devkit/codeanalyzer-python/issues/16):
  - ✅ Ray parallelization implemented
  - ✅ Incremental caching with SHA256-based change detection implemented  
  - ✅ `--file-name` option for single-file analysis implemented
  - ❌ `--nproc` options not yet included (still uses all available cores with Ray)
- ✅ Critical bug fix for nested function detection ([#15](https://github.com/codellm-devkit/codeanalyzer-python/issues/15)) is now included in this version
- Expected performance improvements: 2-10x faster on subsequent runs depending on code change frequency
- Enhanced symbol table accuracy ensures all function definitions are properly captured
- Virtual environment setup is now more robust and only installs dependencies when they are actually available

## [0.1.9] - 2025-07-14

### Fixed
- Fixed `AttributeError: 'OutputFormat' object has no attribute 'casefold'` when using `--format` flag with case-insensitive options
- Changed `OutputFormat` enum to inherit from `str` to support typer's case-insensitive string processing

## [0.1.8] - 2025-07-14

### Added
- Added missing config module with OutputFormat enum for better code organization
- Added proper `__init__.py` and `config.py` files to the config directory

### Fixed
- Fixed missing config directory files that were not previously tracked in git

## [0.1.7] - 2025-07-14

### Changed
- Relaxed Python version requirement from `==3.10.*` to `>=3.10` for improved flexibility
- Enhanced compatibility to support Python 3.10+ versions while maintaining backward compatibility

## [0.1.6] - 2025-07-14

### Changed
- **BREAKING CHANGE**: Updated Python version requirement from `>=3.12` to `==3.10` for improved backwards compatibility
- Enhanced backwards compatibility by supporting Python 3.10 environments commonly used in enterprise and CI/CD systems

### Fixed
- Fixed Python version compatibility issue that was unnecessarily blocking installation on Python 3.10 and 3.11 systems
- Resolved adoption barriers for users on older but still supported Python versions

### Technical Notes
- All codebase features are fully compatible with Python 3.10 (ast.unparse, built-in generics, type hints)
- No Python 3.11+ or 3.12+ specific features are used in the implementation
- All dependencies support Python 3.10+

## [0.2.0] - 2025-07-11

### Changed
- **BREAKING CHANGE**: Renamed `AnalyzerCore` class to `Codeanalyzer` for better library naming consistency
- Refactored core class to support direct library import: `from codeanalyzer import Codeanalyzer`
- Updated all internal references and documentation to use the new class name
- Enhanced library interface for programmatic usage while maintaining CLI compatibility

### Added
- Direct library import support allowing users to import and use `Codeanalyzer` as a library
- Proper `__all__` export in `__init__.py` for clean package interface

## [0.1.5] - 2025-07-11

### Fixed
- Fixed `TypeError` when calling `BaseModel.model_dump_json()` with unsupported `separators` argument (Issue #8)
- Fixed ANSI color codes appearing in CLI test output despite `color=False` setting in CI/CD environments (Issue #9)
- Replaced deprecated `astor.to_source()` with built-in `ast.unparse()` for better Python 3.12+ compatibility

### Changed
- Updated JSON output formatting to use `indent=None` for compact output instead of unsupported `separators` parameter
- Enhanced CLI test configuration to explicitly disable colors using environment variables (`NO_COLOR=1`, `TERM=dumb`)
- Improved test robustness for CI/CD environments, particularly GitHub Actions
- Updated test assertions to properly validate JSON output structure and content

### Removed
- Removed `astor` dependency in favor of Python's built-in `ast.unparse()` functionality

### Added
- Comprehensive coverage configuration in `pyproject.toml` with proper source mapping and exclusions
- Enhanced test fixtures with improved project root path calculation
- Proper logging configuration for test environments
- Coverage reporting with HTML output and configurable thresholds

## [0.1.4] - 2025-07-11

### Added
- MessagePack output format support for ultra-compressed analysis results (achieving 80-90% size reduction)
- `--format` flag allowing users to choose between JSON and MessagePack output formats
- Built-in decompression logic for MessagePack files
- Enhanced output file handling with format-specific extensions

### Changed
- Improved output file compression and serialization performance
- Enhanced schema handling for better serialization efficiency
- Updated CLI to support multiple output formats

### Fixed
- Performance issues with large analysis result files through compression

## [0.1.3] - 2025-07-10

### Fixed
- Fixed broken logo display on PyPI package page (Issue #4)
- Updated README.md to use absolute URLs instead of relative paths for images

### Changed
- Updated package metadata for better PyPI presentation

## [0.1.2] - 2025-07-10

### Fixed
- Fixed CLI installation and execution issues (Issue #2)
- Resolved package import problems in installed package

### Changed
- Reorganized project structure from `src/codeanalyzer` to `codeanalyzer` for better packaging
- Updated build configuration in `pyproject.toml` to properly include all package files
- Moved test directory from `src/test` to `test` for cleaner project structure

## [0.1.1] - 2025-07-10

### Added
- Input path validation in CLI main function to check if the provided input path exists
- Logger import for better error handling and logging capabilities
- Comprehensive system package requirements documentation in README.md
- Platform-specific installation instructions for Ubuntu/Debian, Fedora/RHEL/CentOS, and macOS
- Enhanced error reporting in command execution helper with detailed error output logging
- Support for multiple Python version managers (pyenv, conda, asdf) in base interpreter detection

### Changed
- Improved CLI error handling with proper logging when input path does not exist
- Test configuration updates:
  - Removed unused app import from conftest.py
  - Updated test_cli_call_symbol_table to use verbose flag (-v) instead of --quiet flag
- Improved virtual environment creation error handling with informative error messages
- Enhanced `_get_base_interpreter()` method with robust Python interpreter detection across different environments
- Updated README.md with detailed prerequisite packages including development tools and Python headers

### Fixed
- CLI now properly exits with error code 1 when input path doesn't exist instead of proceeding with invalid path
- Virtual environment creation issues on systems missing python3-venv package
- Type safety issues in subprocess command execution by ensuring all arguments are strings
- Cross-platform compatibility for Python interpreter detection

## [0.1.0] - Initial Release

### Added
- Initial release of codeanalyzer
- Static analysis capabilities for Python source code using Jedi, CodeQL, and Tree-sitter
- Command-line interface with typer
- Support for symbol table analysis and call graph generation
- Configurable analysis levels (1: symbol table, 2: call graph)
- CodeQL integration with optional enable/disable
- Caching system with configurable cache directory
- Output artifacts in JSON format
- Verbosity controls for logging
- Eager/lazy analysis modes
