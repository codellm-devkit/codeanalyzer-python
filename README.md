<div align="center">

<img src="https://github.com/codellm-devkit/codeanalyzer-python/blob/main/docs/assets/logo.png?raw=true" alt="CodeLLM-DevKit" />

# codeanalyzer-python (`canpy`)

**A Python static-analysis toolkit — the CLDK backend that emits a canonical symbol table and call graph, as `analysis.json` or a Neo4j property graph.**

[![PyPI](https://img.shields.io/pypi/v/codeanalyzer-python?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/codeanalyzer-python/)
[![GitHub release](https://img.shields.io/github/v/release/codellm-devkit/codeanalyzer-python?style=for-the-badge&logo=github&label=GitHub&color=2dba4e)](https://github.com/codellm-devkit/codeanalyzer-python/releases/latest)
[![Release](https://img.shields.io/github/actions/workflow/status/codellm-devkit/codeanalyzer-python/release.yml?style=for-the-badge&label=release&logo=githubactions&logoColor=white)](https://github.com/codellm-devkit/codeanalyzer-python/actions/workflows/release.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue?style=for-the-badge)](./LICENSE)

</div>

---

`canpy` is a static analyzer for Python built on [Jedi](https://jedi.readthedocs.io/), with optional
[CodeQL](https://codeql.github.com/)-resolved call edges and
[Tree-sitter](https://tree-sitter.github.io/) parsing. It produces the canonical CodeLLM-DevKit
(CLDK) `analysis.json` — a symbol table plus a call graph — and can project that same analysis into a
**Neo4j property graph**. It is the Python backend behind
[CLDK](https://github.com/codellm-devkit/python-sdk), mirroring its
[TypeScript](https://github.com/codellm-devkit/codeanalyzer-typescript) (`cants`) and
[Java](https://github.com/codellm-devkit/codeanalyzer-java) siblings.

Every run produces a symbol table **and** a call graph. Edges come from Jedi's lexical resolution by
default; `--codeql` resolves additional edges (RPC / third-party / dynamically-dispatched targets)
and merges them with the Jedi-derived edges, also backfilling callees Jedi could not resolve.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
  - [Prerequisites](#prerequisites)
  - [Install via pip (PyPI)](#install-via-pip-pypi)
  - [Install via shell script](#install-via-shell-script)
  - [Install via Homebrew](#install-via-homebrew)
  - [Build from source](#build-from-source)
- [Usage](#usage)
  - [Options](#options)
  - [Examples](#examples)
- [Output targets](#output-targets)
  - [`analysis.json` (default)](#analysisjson-default)
  - [Neo4j graph](#neo4j-graph)
  - [Schema contract](#schema-contract)
- [Development](#development)
- [License](#license)

## Features

- **Symbol table** — modules, classes, functions, methods, variables, decorators, imports, and
  docstrings, with precise source spans.
- **Call graph** — Jedi's lexical resolver by default (level 1), with optional **PyCG**-resolved
  edges merged in at `--analysis-level 2` (provenance-tagged, coupling-aware sharding for large
  apps).
- **Dataflow graphs (level 3)** — native, whole-program dependence graphs built from Python's own
  `ast`: per-callable exceptional **CFG**s and **PDG**s (control + data dependence), stitched into
  a Horwitz–Reps–Binkley **SDG** with parameter/summary edges, emitted as the `program_graphs`
  section at `--analysis-level 3` and queryable with a context-sensitive backward slicer.
- **Neo4j output** — project the analysis into a labeled property graph: a self-contained
  `graph.cypher` snapshot, or an **incremental** push to a live database over Bolt.
- **Versioned schema** — a machine-readable, version-stamped Neo4j schema contract (`--emit schema`),
  checked in as `schema.neo4j.json` and shipped with every release.
- **Incremental cache** — per-file results are cached under `.codeanalyzer`; `--lazy` (default)
  reuses them, `--eager` forces a clean rebuild. `--ray` distributes the work across cores.
- **Compact output** — canonical `analysis.json`, or binary `analysis.msgpack` for smaller artifacts.

## Installation

### Prerequisites

- **Python 3.10 or newer.**
- A C toolchain and the `venv` / development headers — the analyzer builds an isolated virtual
  environment per project (via Python's `venv`) so Jedi can resolve types and imports:

  ```sh
  # Ubuntu / Debian
  sudo apt install python3-venv python3-dev build-essential

  # Fedora / RHEL / CentOS
  sudo dnf group install "Development Tools" && sudo dnf install python3-venv python3-devel

  # macOS
  xcode-select --install
  ```

### Install via pip (PyPI)

```sh
pip install codeanalyzer-python
canpy --help
```

For the optional **live Neo4j push** (`--emit neo4j --neo4j-uri …`), install the `neo4j` extra:

```sh
pip install 'codeanalyzer-python[neo4j]'
```

### Install via shell script

Install the CLI as an isolated tool with the one-line installer (provisions via uv / pipx / pip):

```sh
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/codellm-devkit/codeanalyzer-python/releases/latest/download/canpy-installer.sh | sh
```

### Install via Homebrew

```sh
brew install codellm-devkit/tap/codeanalyzer-python
```

The formula depends on [uv](https://docs.astral.sh/uv/) and installs `canpy` as an isolated,
version-pinned uv tool (the package and its dependencies are resolved and cached on first run).

### Build from source

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```sh
git clone https://github.com/codellm-devkit/codeanalyzer-python
cd codeanalyzer-python
uv sync --all-groups
uv run canpy --help
```

## Usage

```sh
canpy --input /path/to/python/project
```

With no `--output`, the analysis is printed to stdout as compact JSON; with `--output <dir>` it is
written to `analysis.json` (or `graph.cypher` for `--emit neo4j`, or `analysis.msgpack` with
`--format msgpack`) in that directory.

### Options

<!-- BEGIN canpy-help -->

```text
$ canpy --help

 Usage: canpy [OPTIONS] COMMAND [ARGS]...

 Static Analysis on Python source code using Jedi, PyCG and Tree sitter.

╭─ Options ────────────────────────────────────────────────────────────────────────────────────────╮
│ --version                                                              Show the canpy version    │
│                                                                        and exit.                 │
│ --input                -i                     PATH                     Path to the project root  │
│                                                                        directory (not required   │
│                                                                        for --emit schema).       │
│ --output               -o                     PATH                     Output directory for      │
│                                                                        artifacts.                │
│ --format               -f                     [json|msgpack]           Output format for --emit  │
│                                                                        json: json or msgpack.    │
│                                                                        [default: json]           │
│ --emit                                        [json|neo4j|schema]      Output target: json       │
│                                                                        (analysis.json, default)  │
│                                                                        | neo4j (graph.cypher or  │
│                                                                        live Bolt push) | schema  │
│                                                                        (the Neo4j schema.json    │
│                                                                        contract).                │
│                                                                        [default: json]           │
│ --app-name                                    TEXT                     Logical application name  │
│                                                                        for the graph             │
│                                                                        :PyApplication anchor     │
│                                                                        (default: input dir       │
│                                                                        name).                    │
│ --neo4j-uri                                   TEXT                     Push the graph to a live  │
│                                                                        Neo4j over Bolt           │
│                                                                        (incremental); omit to    │
│                                                                        write graph.cypher.       │
│                                                                        [env var: NEO4J_URI]      │
│ --neo4j-user                                  TEXT                     Neo4j username.           │
│                                                                        [env var: NEO4J_USERNAME] │
│                                                                        [default: neo4j]          │
│ --neo4j-password                              TEXT                     Neo4j password. Prefer    │
│                                                                        the env var over the flag │
│                                                                        (the flag is visible in   │
│                                                                        shell history / process   │
│                                                                        list).                    │
│                                                                        [env var: NEO4J_PASSWORD] │
│                                                                        [default: neo4j]          │
│ --neo4j-database                              TEXT                     Neo4j database name       │
│                                                                        (default: server          │
│                                                                        default).                 │
│                                                                        [env var: NEO4J_DATABASE] │
│ --analysis-level       -a                     INTEGER RANGE [1<=x<=3]  Analysis depth: 1=symbol  │
│                                                                        table+Jedi call graph,    │
│                                                                        2=+PyCG call graph,       │
│                                                                        3=+native dataflow graphs │
│                                                                        (CFG/PDG/SDG).            │
│                                                                        [default: 1]              │
│ --graphs                                      TEXT                     Level 3 only:             │
│                                                                        comma-separated           │
│                                                                        program-graph sections to │
│                                                                        emit (cfg, dfg, pdg,      │
│                                                                        sdg). Default: all. `dfg` │
│                                                                        emits the PDG's data      │
│                                                                        edges only; `sdg` implies │
│                                                                        the dependence edges it   │
│                                                                        stitches.                 │
│                                                                        [default:                 │
│                                                                        cfg,dfg,pdg,sdg]          │
│ --graph-field-depth                           INTEGER RANGE [x>=1]     Level 3 only: k-limit on  │
│                                                                        access-path depth         │
│                                                                        (x.f.g.h with k=3 becomes │
│                                                                        x.f.g.*). Mandatory bound │
│                                                                        — it is what guarantees   │
│                                                                        the interprocedural       │
│                                                                        fixpoint terminates.      │
│                                                                        [default: 3]              │
│ --ray                      --no-ray                                    Enable Ray for            │
│                                                                        distributed analysis.     │
│                                                                        [default: no-ray]         │
│ --eager                    --lazy                                      Enable eager or lazy      │
│                                                                        analysis. Defaults to     │
│                                                                        lazy.                     │
│                                                                        [default: lazy]           │
│ --skip-tests               --include-tests                             Skip test files in        │
│                                                                        analysis.                 │
│                                                                        [default: skip-tests]     │
│ --no-venv                  --venv                                      Skip virtualenv creation  │
│                                                                        and dependency            │
│                                                                        installation; resolve     │
│                                                                        imports against the       │
│                                                                        ambient Python            │
│                                                                        environment instead.      │
│                                                                        [default: venv]           │
│ --file-name                                   PATH                     Analyze only the          │
│                                                                        specified file (relative  │
│                                                                        to input directory).      │
│ --cache-dir            -c                     PATH                     Directory to store        │
│                                                                        analysis cache. Defaults  │
│                                                                        to '.codeanalyzer' in the │
│                                                                        input directory.          │
│ --clear-cache              --keep-cache                                Clear cache after         │
│                                                                        analysis. By default,     │
│                                                                        cache is retained.        │
│                                                                        [default: keep-cache]     │
│                        -v                     INTEGER                  Increase verbosity: -v,   │
│                                                                        -vv, -vvv                 │
│                                                                        [default: 0]              │
│ --pycg-shard               --no-pycg-shard                             Shard PyCG call-graph     │
│                                                                        analysis by Python        │
│                                                                        package (level 2 only).   │
│                                                                        When the project exceeds  │
│                                                                        the 500-file ceiling,     │
│                                                                        PyCG is run independently │
│                                                                        per top-level package     │
│                                                                        with cross-package        │
│                                                                        imports treated as ghost  │
│                                                                        nodes. Without this flag, │
│                                                                        projects over the ceiling │
│                                                                        fall back to Jedi-only    │
│                                                                        edges.                    │
│                                                                        [default: no-pycg-shard]  │
│ --pycg-shard-ceiling                          INTEGER RANGE [x>=1]     Maximum files per shard   │
│                                                                        when --pycg-shard is      │
│                                                                        active (default 100).     │
│                                                                        Shards exceeding this     │
│                                                                        limit are skipped; their  │
│                                                                        call edges are omitted    │
│                                                                        from the call graph (Jedi │
│                                                                        edges for those packages  │
│                                                                        are still included).      │
│                                                                        Lower values are safer    │
│                                                                        for packages with deep    │
│                                                                        class hierarchies or      │
│                                                                        heavy import graphs.      │
│                                                                        [default: 100]            │
│ --pycg-shard-timeout                          INTEGER RANGE [x>=0]     Per-shard wall-clock      │
│                                                                        timeout in seconds when   │
│                                                                        --pycg-shard is active    │
│                                                                        (default 120). A shard    │
│                                                                        that exceeds this limit   │
│                                                                        is skipped gracefully.    │
│                                                                        PyCG's fixpoint is        │
│                                                                        bimodal: it either        │
│                                                                        converges quickly or      │
│                                                                        diverges indefinitely, so │
│                                                                        the timeout acts as a     │
│                                                                        final safety net after    │
│                                                                        the file-count ceiling.   │
│                                                                        Set to 0 to disable.      │
│                                                                        POSIX only (macOS /       │
│                                                                        Linux); ignored on        │
│                                                                        Windows.                  │
│                                                                        [default: 120]            │
│ --pycg-shard-strategy                         [jedi|package]           How --pycg-shard groups   │
│                                                                        files (level 2 only).     │
│                                                                        'jedi' (default)          │
│                                                                        partitions the Jedi       │
│                                                                        module-dependency graph   │
│                                                                        (SCC + Louvain) so        │
│                                                                        tightly-coupled modules   │
│                                                                        co-compute and few call   │
│                                                                        edges are severed between │
│                                                                        shards; import cycles are │
│                                                                        never split. 'package'    │
│                                                                        uses the legacy           │
│                                                                        one-shard-per-package-di… │
│                                                                        grouping.                 │
│                                                                        [default: jedi]           │
│ --pycg-max-iter                               INTEGER RANGE [x>=-1]    Cap on PyCG's fixpoint    │
│                                                                        passes per shard/project  │
│                                                                        (level 2; default 50).    │
│                                                                        PyCG iterates until its   │
│                                                                        points-to state stops     │
│                                                                        changing, but its         │
│                                                                        access-path domain has no │
│                                                                        convergence bound, so     │
│                                                                        heavy metaclass/mixin     │
│                                                                        code (e.g. an ORM) can    │
│                                                                        loop with each pass       │
│                                                                        costing seconds. The cap  │
│                                                                        returns a                 │
│                                                                        sound-but-incomplete call │
│                                                                        graph instead of looping  │
│                                                                        until the timeout kills   │
│                                                                        it. Set to -1 for PyCG's  │
│                                                                        unbounded                 │
│                                                                        run-to-convergence        │
│                                                                        behaviour.                │
│                                                                        [default: 50]             │
│ --help                                                                 Show this message and     │
│                                                                        exit.                     │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
```

<!-- END canpy-help -->

### Examples

1. **Basic analysis to stdout, or to a file:**
   ```sh
   canpy --input ./my-python-project                        # compact JSON on stdout
   canpy --input ./my-python-project --output ./out         # → ./out/analysis.json
   ```

2. **Binary output (msgpack):**
   ```sh
   canpy --input ./my-python-project --output ./out --format msgpack   # → ./out/analysis.msgpack
   ```

3. **Resolve extra call edges with CodeQL:**
   ```sh
   canpy --input ./my-python-project --codeql
   ```
   By default, edges come from Jedi's lexical analysis. Adding `--codeql` resolves additional edges
   (including RPC / third-party / dynamically-dispatched targets) and merges them with the
   Jedi-derived edges; CodeQL also backfills resolved callees Jedi could not resolve. CodeQL
   integration is experimental; the CLI is downloaded into `<cache_dir>/codeql/` on first use.

4. **Emit a Neo4j snapshot, or push to a live database:**
   ```sh
   canpy --input ./my-python-project --emit neo4j --output ./out   # → ./out/graph.cypher
   canpy --input ./my-python-project --emit neo4j \
     --neo4j-uri bolt://localhost:7687 --neo4j-user neo4j --neo4j-password secret
   ```

5. **Emit the Neo4j schema contract:**
   ```sh
   canpy --emit schema                   # print schema.json to stdout (no project needed)
   canpy --emit schema --output ./out    # → ./out/schema.json
   ```

6. **Force a clean rebuild with a custom cache directory:**
   ```sh
   canpy --input ./my-python-project --eager --cache-dir /path/to/custom-cache
   ```

7. **Native dataflow graphs (level 3) — CFG/PDG/SDG + slicing:**
   ```sh
   canpy --input ./my-python-project -a 3 --output ./out          # + program_graphs section
   canpy --input ./my-python-project -a 3 --graphs cfg,pdg        # scope the emitted sections
   canpy --input ./my-python-project -a 3 --graph-field-depth 2   # tighter access-path k-limit
   ```
   Level 3 also enriches the Neo4j projection (`--emit neo4j`) with the CPG overlay
   (`:CFGNode` nodes and `CFG_NEXT`/`CDG`/`DDG`/`PARAM_IN`/`PARAM_OUT`/`SUMMARY` edges).

## Analysis levels

| Level | Flag | What it adds | Cost |
| --- | --- | --- | --- |
| 1 | `-a 1` (default) | Symbol table + Jedi resolver call graph | Cheap |
| 2 | `-a 2` | PyCG call-graph enrichment (provenance-merged) | Moderate |
| 3 | `-a 3` | Native CFG/PDG/SDG (`program_graphs`) + CPG Neo4j overlay + backward slicing | Heavy, whole-program |

Levels are cumulative — `-a 3` includes level 2's call graph (the SDG is stitched over it).
Nothing at level 3 runs unless requested: `-a 1`/`-a 2` timings and output are unaffected.

## Architecture & Tooling

Locked level-3 substrate decisions
([#67](https://github.com/codellm-devkit/codeanalyzer-python/issues/67)):

- **CFG source:** hand-built from the stdlib `ast` module — the same parse the symbol-table
  builder uses, so graph nodes join back to symbol-table signatures by construction. One
  synthetic `ENTRY`/`EXIT` per callable, statement-level nodes keyed `(signature, node_id)`
  in source-span order, exceptional edges first-class.
- **Def-use source:** hand-built reaching definitions (classic forward worklist) over k-limited
  access paths (`--graph-field-depth`, default 3) — no usable SSA library exists for Python.
- **Points-to oracle:** a **type-based may-alias MVP stub** — two access paths may alias iff
  their suffixes are prefix-compatible and their bases' Jedi-inferred types are compatible
  (unknown types conservatively alias). Frozen behind `may_alias()`; upgrading to a real
  points-to substrate is staged follow-up work. Call dispatch comes from the merged
  Jedi(+PyCG) call graph, treated as a frozen oracle.
- **Summaries:** relational formal-in → formal-out flows composed bottom-up over the Tarjan
  SCC condensation of the call graph, monotone fixpoint within SCCs; globals ride as extra
  formals (`<global>:module::name`), closure captures bind at definition sites.
- **Clients:** backward slicing ships in-process (two-phase context-sensitive HRB traversal,
  `codeanalyzer.dataflow.slicing`). Taint is deliberately left to the CLDK SDK: once the SDG
  is emitted it is language-independent labeled reachability.
- **Precision posture:** sound-leaning and over-approximate — prefer false positives to missed
  flows. **Known unsoundness (documented, not silently absorbed):** `eval`/`exec`, reflection
  (`getattr`/`setattr` with dynamic names), monkey-patching, C extensions, `import` side
  effects, and module top-level statements (globals are modeled as formals instead).

## Output targets

`canpy` builds one analysis in memory and can emit it three ways (`--emit`):

### `analysis.json` (default)

A `PyApplication` document — the canonical CLDK contract:

```jsonc
{
  "symbol_table":   { /* file path → module (classes, functions, variables, imports, …) */ },
  "call_graph":     [ /* CALL_DEP edges: { source, target, weight, provenance } keyed by callable signature */ ],
  "program_graphs": { /* -a 3 only: schema_version, k_limit, per-callable { cfg, pdg, param_nodes }, sdg_edges */ }
}
```

By default this is printed to stdout in JSON; with `--output` it is written to `analysis.json` (or
`analysis.msgpack` with `--format msgpack`, a more compact binary format).

### Neo4j graph

`--emit neo4j` projects the same analysis into a labeled property graph. Every node label is
`Py`-prefixed and every relationship type is `PY_`-prefixed (e.g. `:PyClass`, `PY_CALLS`) so multiple
language analyzers can share one database without label or relationship-type collisions. Declarations
are keyed by their signature under a shared `:PySymbol` label; calls, imports, inheritance,
decorators, and call sites are relationships:

- **Without `--neo4j-uri`** — writes a self-contained `graph.cypher` (constraints + indexes, a scoped
  wipe, then batched `MERGE`s). Load it with `cypher-shell < graph.cypher`. Needs no extra
  dependencies.
- **With `--neo4j-uri`** — pushes to a live Neo4j over Bolt **incrementally**: only modules whose
  content hash changed are rewritten, and on a full run modules whose source file vanished are
  pruned. Requires the `neo4j` extra. Every graph carries a `schema_version` on its `:PyApplication`
  node.

Call-graph endpoints that aren't present in the symbol table (third-party / framework / RPC targets)
are materialized as `:PyExternal` ghost nodes, mirroring the analyzer's own ghost-node behaviour.

The connection options also read from the standard Neo4j environment variables — `NEO4J_URI`,
`NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` — when the corresponding flag is omitted (an
explicit flag wins). Prefer the env var for the password so it doesn't land in shell history or the
process list:

```sh
export NEO4J_URI=bolt://localhost:7687
export NEO4J_PASSWORD=secret
canpy -i ./my-project --emit neo4j     # credentials picked up from the environment
```

### Schema contract

`--emit schema` writes the machine-readable, version-stamped Neo4j schema (`schema.json`: node labels,
relationships, properties, constraints, and indexes). It needs no project and is checked into the repo
as `schema.neo4j.json` and bundled in every release as a GitHub Release asset, so a consumer can
validate producer/consumer compatibility without invoking the tool. The shape of the contract matches
the [`codeanalyzer-typescript`](https://github.com/codellm-devkit/codeanalyzer-typescript) backend.

A UML of the `analysis.json` schema (the `PyApplication` containment tree) is checked in as
[`schema-uml.drawio`](./schema-uml.drawio), and the property-graph schema as
[`neo4j-schema.drawio`](./neo4j-schema.drawio).

## Development

This project uses [uv](https://docs.astral.sh/uv/).

```sh
uv sync --all-groups
uv run canpy --input /path/to/project           # run from source
uv run canpy --emit schema > schema.neo4j.json  # regenerate the checked-in schema contract
uv run python scripts/update_readme.py          # regenerate the canpy --help block above
uv run pytest                                   # run the test suite
```

The Neo4j schema-conformance test always runs. The Neo4j **bolt** integration test spins up a real
Neo4j via [Testcontainers](https://testcontainers.com/) and is **opt-in** — it needs a container
runtime (Docker or Podman) and is enabled with an environment variable:

```sh
RUN_CONTAINER_TESTS=1 uv run pytest test/test_neo4j_bolt.py -s
```

## License

Apache 2.0 — see [LICENSE](./LICENSE).
