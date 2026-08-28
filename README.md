<div align="center">

<img src="https://github.com/codellm-devkit/codeanalyzer-python/blob/main/docs/assets/logo.png?raw=true" alt="CodeLLM-DevKit" />

# codeanalyzer-python (`canpy`)

**A Python static-analysis toolkit — the CLDK backend that emits the canonical schema v2 Code Property Graph, as `analysis.json` or a Neo4j property graph.**

[![PyPI](https://img.shields.io/pypi/v/codeanalyzer-python?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/codeanalyzer-python/)
[![GitHub release](https://img.shields.io/github/v/release/codellm-devkit/codeanalyzer-python?style=for-the-badge&logo=github&label=GitHub&color=2dba4e)](https://github.com/codellm-devkit/codeanalyzer-python/releases/latest)
[![Release](https://img.shields.io/github/actions/workflow/status/codellm-devkit/codeanalyzer-python/release.yml?style=for-the-badge&label=release&logo=githubactions&logoColor=white)](https://github.com/codellm-devkit/codeanalyzer-python/actions/workflows/release.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue?style=for-the-badge)](./LICENSE)

</div>

---

`canpy` is a static analyzer for Python built on [Jedi](https://jedi.readthedocs.io/),
and [Tree-sitter](https://tree-sitter.github.io/). It
emits the **canonical CodeLLM-DevKit (CLDK) schema v2** — a single, additive Code Property Graph
tree — either as `analysis.json` or projected into a **Neo4j property graph**. It is the Python
backend behind [CLDK](https://github.com/codellm-devkit/python-sdk), mirroring its
[TypeScript](https://github.com/codellm-devkit/codeanalyzer-typescript) (`cants`) and
[Java](https://github.com/codellm-devkit/codeanalyzer-java) siblings.

The payload is **one tree grown one layer at a time** across four analysis levels (`-a 1|2|3|4`): a
symbol table, a call graph, intraprocedural control- and data-dependence graphs, and a whole-program
interprocedural system dependence graph. Each level is a strict superset of the one below it
(`analysis.json(-a 1) ⊆ … ⊆ analysis.json(-a 4)`), so a consumer can request exactly the depth it
needs.

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
- [Analysis levels](#analysis-levels)
- [Architecture & Tooling](#architecture--tooling)
- [Output shape (canonical schema v2)](#output-shape-canonical-schema-v2)
- [Output targets](#output-targets)
  - [`analysis.json` (default)](#analysisjson-default)
  - [Neo4j graph](#neo4j-graph)
  - [Schema contract](#schema-contract)
- [Development](#development)
- [License](#license)

## Features

- **Canonical schema v2** — one additive Code Property Graph tree (`schema_version` `2.0.0`),
  stamped with `language`, `max_level`, `analyzer{name,version}`, and (at L3+) `k_limit`, rooted
  at a single `application` node with durable `can://` ids on every callable and above.
- **Symbol table** — modules, classes, functions, methods, variables, decorators, imports, and
  docstrings, with precise byte-offset source spans; each module carries its `source` once.
- **Call graph** — Jedi's lexical resolver at level 1, enriched at level 2 by a per-callable
  **defuse linker** that resolves call sites through local def-use chains and module-scope
  bindings (provenance-tagged, deterministic, no global fixpoint).
- **Dataflow graphs** — native, per-callable exceptional **CFG** plus **control-** and
  **data-dependence** edges (`cfg`/`cdg`/`ddg`) at level 3, stitched into a whole-program
  **interprocedural SDG** (synthetic parameter vertices, `param_in`/`param_out`/`summary`,
  alias-aware DDG) at level 4 — all built in-process from the stdlib `ast`.
- **Neo4j output** — project the analysis into a labeled property graph: a self-contained
  `graph.cypher` snapshot, or an **incremental** push to a live database over Bolt.
- **Versioned schema** — a machine-readable, version-stamped Neo4j schema contract (`--emit schema`),
  checked in as `schema.neo4j.json` (`2.0.0`) and shipped with every release.
- **Incremental cache** — per-file results are cached under `.codeanalyzer`; `--lazy` (default)
  reuses them, `--eager` forces a clean rebuild. `--ray` distributes the work across cores.
- **Compact output** — one canonical `analysis.json` per run.

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

The **Scalpel-backed points-to oracle** at level 4 is vendored and built in — no extra install
required. If Scalpel cannot resolve a construct, level 4 automatically falls back to the built-in
type-based oracle.

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
written to `analysis.json` (or `graph.cypher` for `--emit neo4j`) in that directory.

### Options

<!-- BEGIN canpy-help -->

```text
$ canpy --help

 Usage: canpy [OPTIONS] COMMAND [ARGS]...

 Static Analysis on Python source code using Jedi and Tree sitter.

╭─ Options ────────────────────────────────────────────────────────────────────────────────────────╮
│ --version                                                                 Show the canpy version │
│                                                                           and exit.              │
│ --input                  -i                        <path>                 Path to the project    │
│                                                                           root directory (not    │
│                                                                           required for --emit    │
│                                                                           schema).               │
│ --output                 -o                        <path>                 Output directory for   │
│                                                                           artifacts.             │
│ --emit                                             <json|neo4j|schema>    Output target: json    │
│                                                                           (analysis.json,        │
│                                                                           default) | neo4j       │
│                                                                           (graph.cypher or live  │
│                                                                           Bolt push) | schema    │
│                                                                           (the Neo4j schema.json │
│                                                                           contract).             │
│                                                                           [default: json]        │
│ --app-name                                         <str>                  Logical application    │
│                                                                           name for the graph     │
│                                                                           :PyApplication anchor  │
│                                                                           (default: input dir    │
│                                                                           name).                 │
│ --neo4j-uri                                        <str>                  Push the graph to a    │
│                                                                           live Neo4j over Bolt   │
│                                                                           (incremental); omit to │
│                                                                           write graph.cypher.    │
│                                                                           [env var: NEO4J_URI]   │
│ --neo4j-user                                       <str>                  Neo4j username.        │
│                                                                           [env var:              │
│                                                                           NEO4J_USERNAME]        │
│                                                                           [default: neo4j]       │
│ --neo4j-password                                   <str>                  Neo4j password. Prefer │
│                                                                           the env var over the   │
│                                                                           flag (the flag is      │
│                                                                           visible in shell       │
│                                                                           history / process      │
│                                                                           list).                 │
│                                                                           [env var:              │
│                                                                           NEO4J_PASSWORD]        │
│                                                                           [default: neo4j]       │
│ --neo4j-database                                   <str>                  Neo4j database name    │
│                                                                           (default: server       │
│                                                                           default).              │
│                                                                           [env var:              │
│                                                                           NEO4J_DATABASE]        │
│ --analysis-level         -a                        <int range> [1<=x<=4]  Analysis depth:        │
│                                                                           1=symbol table+Jedi    │
│                                                                           call graph,            │
│                                                                           2=+defuse-linker call  │
│                                                                           graph, 3=+native       │
│                                                                           intraprocedural        │
│                                                                           dataflow (CFG/PDG),    │
│                                                                           4=+interprocedural SDG │
│                                                                           (param/summary edges,  │
│                                                                           alias-aware DDG).      │
│                                                                           [default: (1)]         │
│ --graphs                                           <str>                  Level 3+ only:         │
│                                                                           comma-separated        │
│                                                                           program-graph sections │
│                                                                           to emit (cfg, dfg,     │
│                                                                           pdg, sdg). Default:    │
│                                                                           cfg,dfg,pdg. `dfg`     │
│                                                                           emits the PDG's data   │
│                                                                           edges only; `sdg`      │
│                                                                           requires -a 4.         │
│                                                                           Incompatible with      │
│                                                                           --emit neo4j (always   │
│                                                                           full-depth).           │
│                                                                           [default:              │
│                                                                           (cfg,dfg,pdg)]         │
│ --graph-field-depth                                <int range> [x>=1]     Level 3 only: k-limit  │
│                                                                           on access-path depth   │
│                                                                           (x.f.g.h with k=3      │
│                                                                           becomes x.f.g.*).      │
│                                                                           Mandatory bound — it   │
│                                                                           is what guarantees the │
│                                                                           interprocedural        │
│                                                                           fixpoint terminates.   │
│                                                                           [default: 3]           │
│ --ray                        --no-ray                                     Enable Ray for         │
│                                                                           distributed analysis.  │
│                                                                           [default: no-ray]      │
│ --eager                      --lazy                                       Enable eager or lazy   │
│                                                                           analysis. Defaults to  │
│                                                                           lazy.                  │
│                                                                           [default: lazy]        │
│ --skip-tests                 --include-tests                              Skip test files in     │
│                                                                           analysis.              │
│                                                                           [default: skip-tests]  │
│ --no-venv                    --venv                                       Skip virtualenv        │
│                                                                           creation and           │
│                                                                           dependency             │
│                                                                           installation; resolve  │
│                                                                           imports against the    │
│                                                                           ambient Python         │
│                                                                           environment instead.   │
│                                                                           [default: venv]        │
│ --resolve-installed                                                       Additionally bind      │
│                                                                           imports via the        │
│                                                                           project venv's         │
│                                                                           installed metadata     │
│                                                                           (*.dist-info); output  │
│                                                                           becomes                │
│                                                                           machine-dependent      │
│                                                                           (prov:                 │
│                                                                           installed-metadata).   │
│ --file-name                                        <path>                 Analyze only the       │
│                                                                           specified file         │
│                                                                           (relative to input     │
│                                                                           directory).            │
│ --cache-dir              -c                        <path>                 Directory to store     │
│                                                                           analysis cache.        │
│                                                                           Defaults to            │
│                                                                           '.codeanalyzer' in the │
│                                                                           input directory.       │
│ --clear-cache                --keep-cache                                 Clear cache after      │
│                                                                           analysis. By default,  │
│                                                                           cache is retained.     │
│                                                                           [default: keep-cache]  │
│                          -v                        <int>                  Increase verbosity:    │
│                                                                           -v, -vv, -vvv          │
│                                                                           [default: 0]           │
│ --entrypoint-rules                                 <path>                 Extra entrypoint rules │
│                                                                           file (YAML).           │
│                                                                           Repeatable; merges     │
│                                                                           with the shipped       │
│                                                                           rules. A malformed     │
│                                                                           file is an error.      │
│ --artifact-text              --no-artifact-text                           Capture verbatim       │
│                                                                           `source` text on       │
│                                                                           discovered artifacts.  │
│                                                                           --no-artifact-text     │
│                                                                           empties `source`       │
│                                                                           everywhere (inventory  │
│                                                                           unchanged).            │
│                                                                           [default:              │
│                                                                           artifact-text]         │
│ --artifact-text-max-by…                            <int range> [x>=1]     Per-file byte cap on   │
│                                                                           captured artifact      │
│                                                                           `source`; a decodable  │
│                                                                           file over the cap is   │
│                                                                           truncated              │
│                                                                           (text_truncated=True). │
│                                                                           sha256/size_bytes      │
│                                                                           always reflect the     │
│                                                                           full file.             │
│                                                                           [default: 262144]      │
│ --help                                                                    Show this message and  │
│                                                                           exit.                  │
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

3. **Enrich the call graph with the defuse linker (level 2):**
   ```sh
   canpy --input ./my-python-project -a 2
   ```
   Level 1 edges come from Jedi's lexical resolution. `-a 2` runs the **defuse linker** —
   per-callable resolution over lexical scopes, import bindings, class hierarchies, and a
   bounded type-propagation round — and merges its edges with Jedi's, backfilling the
   callees Jedi could not resolve. Every edge is provenance-tagged (`jedi`, `defuse`).

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

7. **Dataflow graphs — intraprocedural (level 3) and interprocedural (level 4):**
   ```sh
   canpy --input ./my-python-project -a 3 --output ./out          # per-callable cfg/cdg/ddg
   canpy --input ./my-python-project -a 4 --output ./out          # + interprocedural SDG
   canpy --input ./my-python-project -a 3 --graphs cfg,pdg        # scope the emitted sections
   canpy --input ./my-python-project -a 4 --graphs sdg            # sdg requires -a 4
   canpy --input ./my-python-project -a 3 --graph-field-depth 2   # tighter access-path k-limit
   ```
   Levels 3 and 4 also enrich the Neo4j projection (`--emit neo4j`) with the CPG overlay
   (`:PyBodyNode` nodes wired by `PY_CFG_NEXT`/`PY_CDG`/`PY_DDG`, plus the level-4
   `PY_PARAM_IN`/`PY_PARAM_OUT`/`PY_SUMMARY` edges — the cross-language dataflow vocabulary,
   PY_-namespaced like every other row family so multi-language databases never mingle
   analyzers' edges).

## Analysis levels

Each level is the same tree grown one layer deeper, plus the edge family over that new layer. The
levels are cumulative and additive — `analysis.json(-a 1) ⊆ … ⊆ analysis.json(-a 4)`.

| Level | Flag | What it adds | Where it lands |
| --- | --- | --- | --- |
| **1** | `-a 1` (default) | Symbol table, Jedi call graph, and `call` nodes in each callable's `body` | `body` calls (`callee: null`) |
| **2** | `-a 2` | Defuse-linker call-graph enrichment; each call's `callee` backfilled to a `can://` id | `call_graph`, `body` callees |
| **3** | `-a 3` | Native **intraprocedural** CFG/CDG/DDG (syntactic, name-equality, `prov: ["ssa"]`) | `cfg`, `cdg`, `ddg`, `@entry`/`@exit` on each callable |
| **4** | `-a 4` | **Interprocedural** SDG: synthetic param vertices, alias-aware DDG (`prov: ["points-to"]`) | `param_in`, `param_out`, `summary`, semantic `ddg` |

`-a 1`/`-a 2` timings and output are unaffected by the heavier levels — nothing at level 3+ runs
unless requested. Flag gating: `--graphs sdg` requires `-a 4`; `--graphs cfg,dfg,pdg` and
`--graph-field-depth` require `-a 3`.

## Architecture & Tooling

The dataflow substrate is hand-built from the standard library so every graph node joins back to a
symbol-table signature by construction
([#67](https://github.com/codellm-devkit/codeanalyzer-python/issues/67)):

- **CFG source:** a hand-built **exceptional** control-flow graph from the stdlib `ast` module — the
  same parse the symbol-table builder uses. One synthetic `@entry`/`@exit` per callable,
  statement-level nodes keyed `line:col` in source order, with exception / `yield` / `await` edges
  first-class.
- **Def-use source:** hand-built **reaching definitions** (a classic forward worklist) over
  k-limited access paths (`--graph-field-depth`, default 3) — there is no usable SSA library for
  Python. This yields the level-3 syntactic DDG (name-equality, `prov: ["ssa"]`).
- **Points-to oracle (level 4):** the **Scalpel** may-alias oracle — `ScalpelAliasOracle`
  (`codeanalyzer/dataflow/scalpel_oracle.py`) — consumes Scalpel's SSA copy/const facts to answer
  `may_alias(path_a, path_b)`, adding the alias-aware DDG edges (`prov: ["points-to"]`) and the
  interprocedural summaries. Scalpel is **vendored** — a `typed_ast`-free slice built into the
  package under `codeanalyzer/dataflow/scalpel/` — so it is the **default** level-4 oracle with no
  external dependency to install; the analyzer falls back to the built-in `TypeBasedAliasOracle`
  (Jedi-inferred types; unknown types conservatively alias) only when Scalpel can't resolve a
  construct or a per-callable build fails, keeping the `may_alias` interface total. Call dispatch
  comes from the merged Jedi + defuse-linker call graph, treated as a frozen oracle.
- **Summaries:** relational formal-in → formal-out flows composed bottom-up over the Tarjan SCC
  condensation of the call graph, a monotone fixpoint within SCCs; globals ride as extra formals,
  closure captures bind at definition sites.
- **Slicing and taint are the SDK's responsibility.** A backward slicer ships in-process
  (`codeanalyzer.dataflow.slicing`), but only as an **internal validation utility** for the L3/L4
  gates — it is not a product surface. Once the SDG is emitted, slicing and taint become
  language-independent labeled reachability and belong to the CLDK SDK across the provider/client
  boundary; the analyzer emits the `summary` substrate and **no `taint_flows` section**.
- **Precision posture:** sound-leaning and over-approximate — prefer false positives to missed
  flows. **Known unsoundness (documented, not silently absorbed):** `eval`/`exec`, reflection
  (`getattr`/`setattr` with dynamic names), monkey-patching, C extensions, `import` side effects,
  and module top-level statements (globals are modeled as formals instead).

## Output shape (canonical schema v2)

Every run produces the same envelope — an `Analysis` document — regardless of level; deeper levels
just populate more of the same tree:

```jsonc
{
  "schema_version": "2.0.0",
  "language": "python",
  "max_level": 4,                 // the level this run was produced at
  "k_limit": 3,                   // access-path depth bound (--graph-field-depth); L3+ only
  "analyzer": { "name": "codeanalyzer-python", "version": "1.0.0" },
  "application": {
    "id": "can://python/<app>",
    "kind": "application",
    "symbol_table": {             // relative POSIX path → module
      "pkg/mod.py": {
        "id": "can://python/<app>/pkg/mod.py",
        "kind": "module",
        "source": "…full file text, stored once per module…",
        "types":     { "<Class>": { "id": "…", "kind": "class", "callables": { /* methods */ } } },
        "functions": { "<sig>":   { /* callable, see below */ } }
      }
    },
    "call_graph": [ { "src": "can://…/main(a)", "dst": "can://…/helper(x)",
                      "weight": 1, "prov": ["defuse", "jedi"] } ],
    "external_symbols": {         // imported/builtin call targets, keyed by id
      "can://python/<app>/@external/os/getcwd":
        { "id": "can://python/<app>/@external/os/getcwd", "kind": "external",
          "name": "getcwd", "module": "os" }
    },
    "param_in":  [ { "src": "can://…/main(a)@6:4/actual_in:0", "dst": "can://…/helper(x)@formal_in:0" } ],
    "param_out": [ { "src": "can://…/helper(x)@formal_out",   "dst": "can://…/main(a)@6:4/actual_out" } ]
  }
}
```

A **callable** (function or method) carries its own CPG, keyed by node id:

```jsonc
{
  "id": "can://…/main(a)", "kind": "function",
  "span": { "start": [5, 0], "end": [7, 12], "bytes": [43, 86] },  // byte offsets into module.source
  "body": {                                       // node id → node
    "@entry": { "kind": "entry" },
    "6:4":    { "kind": "statement", "span": { … } },
    "6:8":    { "kind": "call", "span": { … }, "callee": "can://…/helper(x)" },  // callee null until L2
    "@formal_in:0":    { "kind": "formal_in", "of": "a" },              // L4 param vertices
    "6:4/actual_in:0": { "kind": "actual_in", "of": "a", "parent": "6:4" },
    "@exit":  { "kind": "exit" }
  },
  "cfg":     [ { "src": "@entry", "dst": "6:4", "kind": "fallthrough" } ],    // L3
  "cdg":     [ { "src": "@entry", "dst": "6:4" } ],                           // L3
  "ddg":     [ { "src": "6:4", "dst": "7:4", "var": "h", "prov": ["ssa"] } ], // L3 ssa / L4 points-to
  "summary": [ { "src": "6:4/actual_in:0", "dst": "6:4/actual_out" } ]        // L4
}
```

The application envelope also contains three substrate sections:

- **`artifacts`** — discovered non-code files (manifests, configs, Docker files, CI workflows,
  packaging files, scripts, docs, and legal files) with extraction status (`none`, `partial`, or
  `full`; default `none`), keyed by relative path; each artifact carries the
  `can://artifact/<app>/<path>` id namespace. Config files carry extracted `config_keys`
  (keys, values, namespaces, and references) and `DEFINES_CONFIG` Neo4j edges.
- **`dependencies`** — declared packages with kind (`runtime`/`dev`/`optional`/`build`), spec,
  locked version, and provenance (`prov`): where each binding came from (manifest file, lock file,
  installed metadata).
- **`unresolved_imports`** — modules imported but not resolvable in the declared dependency set,
  one entry per module.

Notable properties:

- **Durable `can://` ids** identify every node at callable granularity and above
  (`can://python/<app>/<file>/<callable-sig>`); nodes below a callable use ordinal ids
  (`@entry`, `@exit`, `line:col`, `@formal_in:N`, `line:col/actual_in:N`).
- **`source` lives once per module**; every node's text is the `module.source[span.bytes]` slice.
- **Cross-function edges** — `call_graph`, `param_in`, `param_out` — live at **application** scope;
  the intraprocedural `cfg`/`cdg`/`ddg` and the `summary` edges live **on the callable**.
- **No dangling endpoints** — every `call_graph` `src`/`dst` joins the id space: declared
  callables by their tree id, imported/builtin targets by a `…/@external/<module>/<name>` id
  homed in `application.external_symbols`.
- **Breaking change from v1:** there is no more flat top-level `symbol_table`/`call_graph`, and no
  separate program-graphs section. Everything now hangs off `application`, and the dataflow graphs
  are inlined on each callable. Read `analysis.application.symbol_table` (was
  `analysis.symbol_table`) and `analysis.application.call_graph` (was `analysis.call_graph`).

## Output targets

`canpy` builds one analysis in memory and can emit it three ways (`--emit`):

### `analysis.json` (default)

The `Analysis` envelope described above. By default it is printed to stdout as JSON; with `--output`
it is written to `analysis.json` (or `analysis.msgpack` with `--format msgpack`, a more compact
binary format).

### Neo4j graph

`--emit neo4j` projects the same schema v2.0.0 analysis into a labeled property graph. Every
Python-specific node label is `Py`-prefixed and every Python-specific relationship type is
`PY_`-prefixed (e.g. `:PyClass`, `PY_CALLS`) so multiple language analyzers can share one database
without label or relationship-type collisions. The one deliberate exception is the language-neutral
`Artifact`/`Package` subgraph (non-code files and third-party dependencies) — those nodes carry no
`Py` prefix, since they are meant as cross-language merge targets: a sibling-language analyzer over
the same repo should land on the same `Artifact`/`Package` nodes, not a per-language duplicate.
Declarations are keyed by their **`can://` id** under a shared `:PySymbol` label; calls, imports,
inheritance, decorators, and call sites are relationships. At `-a 3`/`-a 4` the projection gains the
**CPG overlay** — `:PyBodyNode` nodes (statements, and at level 4 the parameter vertices) wired by
`PY_CFG_NEXT`/`PY_CDG`/`PY_DDG`, plus the level-4 `PY_PARAM_IN`/`PY_PARAM_OUT`/`PY_SUMMARY` edges:

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
relationships, properties, constraints, and indexes; currently `schema_version` `2.0.0`). It needs no
project and is checked into the repo as `schema.neo4j.json` and bundled in every release as a GitHub
Release asset, so a consumer can validate producer/consumer compatibility without invoking the tool.
The shape of the contract matches the
[`codeanalyzer-typescript`](https://github.com/codellm-devkit/codeanalyzer-typescript) backend.

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
