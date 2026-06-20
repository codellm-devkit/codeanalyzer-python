![logo](https://github.com/codellm-devkit/codeanalyzer-python/blob/main/docs/assets/logo.png?raw=true)

# A Python Static Analysis Toolkit (and Library)

A comprehensive static analysis tool for Python source code that provides symbol table generation, call graph analysis, and semantic analysis using Jedi, CodeQL, and Tree-sitter — emitted as the canonical `analysis.json`, or projected into a **Neo4j property graph**.

## Installation

```bash
pip install codeanalyzer-python
```

For the optional **live Neo4j push** (`--emit neo4j --neo4j-uri …`), install the `neo4j` extra:

```bash
pip install 'codeanalyzer-python[neo4j]'
```

Or install the CLI as an isolated tool with the one-line installer (provisions via uv / pipx / pip):

```bash
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/codellm-devkit/codeanalyzer-python/releases/latest/download/codeanalyzer-installer.sh | sh
```

### Prerequisites

- Python 3.12 or higher

#### System Package Requirements

The tool creates virtual environments internally using Python's built-in `venv` module.

**Ubuntu/Debian systems:**
```bash
sudo apt update
sudo apt install python3.12-venv python3-dev build-essential
```

**Fedora/RHEL/CentOS systems:**
```bash
sudo dnf group install "Development Tools"
sudo dnf install python3-pip python3-venv python3-devel
```
or on older versions:
```bash
sudo yum groupinstall "Development Tools"
sudo yum install python3-pip python3-venv python3-devel
```

**macOS systems:**
```bash
# Install Xcode Command Line Tools (for compilation)
xcode-select --install

# If using Homebrew Python (recommended)
brew install python@3.12

# If using pyenv (popular Python version manager)
# First ensure pyenv is properly installed and configured
pyenv install 3.12.0  # or latest 3.12.x version
pyenv global 3.12.0   # or pyenv local 3.12.0 for project-specific

# If using system Python, you may need to install certificates
/Applications/Python\ 3.12/Install\ Certificates.command
```

> **Note:** These packages are required as the tool uses Python's built-in `venv` module to create isolated environments for analysis.

## Usage

The codeanalyzer provides a command-line interface for performing static analysis on Python projects.

### Basic Usage

```bash
codeanalyzer --input /path/to/python/project
```

### Command Line Options

To view the available options and commands, run `codeanalyzer --help`. You should see output similar to the following:

```bash
❯ codeanalyzer --help

 Usage: codeanalyzer [OPTIONS] COMMAND [ARGS]...

 Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.


╭─ Options ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│    --input           -i                  PATH            Path to the project root directory (not required for --emit schema). │
│    --output          -o                  PATH            Output directory for artifacts. [default: None]                      │
│    --format          -f                  [json|msgpack]  Output format for --emit json: json or msgpack. [default: json]      │
│    --emit                                [json|neo4j|    Output target: json (analysis.json) | neo4j (graph.cypher or live    │
│                                           schema]         Bolt push) | schema (the Neo4j schema.json contract). [default: json]│
│    --app-name                            TEXT            Logical application name for the graph :PyApplication anchor.          │
│    --neo4j-uri                           TEXT            Push the graph to a live Neo4j over Bolt. [env: NEO4J_URI]            │
│    --neo4j-user                          TEXT            Neo4j username. [env: NEO4J_USERNAME] [default: neo4j]               │
│    --neo4j-password                      TEXT            Neo4j password. [env: NEO4J_PASSWORD] [default: neo4j]               │
│    --neo4j-database                      TEXT            Neo4j database name. [env: NEO4J_DATABASE]                           │
│    --codeql              --no-codeql                     Enable CodeQL-based analysis. [default: no-codeql]                   │
│    --eager               --lazy                          Enable eager or lazy analysis. Defaults to lazy. [default: lazy]     │
│    --cache-dir       -c                  PATH            Directory to store analysis cache. [default: None]                   │
│    --clear-cache         --keep-cache                    Clear cache after analysis. [default: keep-cache]                    │
│                      -v                  INTEGER         Increase verbosity: -v, -vv, -vvv [default: 0]                       │
│    --help                                                Show this message and exit.                                          │
╰─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### Examples

1. **Basic analysis with symbol table:**
   ```bash
   codeanalyzer --input ./my-python-project
   ```

   This will print the symbol table to stdout in JSON format. If you want to save the output, you can use the `--output` option.

   ```bash
   codeanalyzer --input ./my-python-project --output /path/to/analysis-results
   ```

   Now, you can find the analysis results in `analysis.json` in the specified directory.

2. **Change output format to msgpack:**
   ```bash
   codeanalyzer --input ./my-python-project --output /path/to/analysis-results --format msgpack
   ```

   This will save the analysis results in `analysis.msgpack` in the specified directory.

3. **Analysis with CodeQL enabled:**
   ```bash
   codeanalyzer --input ./my-python-project --codeql
   ```
   Every run produces a symbol table **and** a call graph. By default, edges come from Jedi's lexical analysis. Adding `--codeql` resolves additional edges (including RPC / third-party / dynamically-dispatched targets) and merges them with the Jedi-derived edges. CodeQL also backfills resolved callees on Jedi-emitted call sites where Jedi couldn't resolve them.

    ***Note: CodeQL integration is experimental. The CLI is downloaded into `<cache_dir>/codeql/` on first use and reused thereafter.***

4. **Eager analysis with custom cache directory:**
   ```bash
   codeanalyzer --input ./my-python-project --eager --cache-dir /path/to/custom-cache
   ```
    This will rebuild the analysis cache at every run and store it in `/path/to/custom-cache/.codeanalyzer`.

5. **Emit a Neo4j snapshot, or push to a live database:**
   ```bash
   codeanalyzer --input ./my-python-project --emit neo4j --output ./out   # → ./out/graph.cypher
   codeanalyzer --input ./my-python-project --emit neo4j \
     --neo4j-uri bolt://localhost:7687 --neo4j-user neo4j --neo4j-password secret
   ```

6. **Emit the Neo4j schema contract:**
   ```bash
   codeanalyzer --emit schema                  # print schema.json to stdout (no project needed)
   codeanalyzer --emit schema --output ./out    # → ./out/schema.json
   ```

## Output targets

`codeanalyzer` builds one analysis in memory and can emit it three ways (`--emit`):

### `analysis.json` (default)

A `PyApplication` document — the canonical CLDK contract:

```jsonc
{
  "symbol_table": { /* file path → module (classes, functions, variables, imports, …) */ },
  "call_graph":   [ /* CALL_DEP edges: { source, target, weight, provenance } keyed by callable signature */ ]
}
```

By default this is printed to stdout in JSON; with `--output` it is written to `analysis.json` (or `analysis.msgpack` with `--format msgpack`, a more compact binary format).

### Neo4j graph

`--emit neo4j` projects the same analysis into a labeled property graph. Every node label is `Py`-prefixed and every relationship type is `PY_`-prefixed (e.g. `:PyClass`, `PY_CALLS`) so multiple language analyzers can share one database without label or relationship-type collisions. Declarations are keyed by their signature under a shared `:PySymbol` label; calls, imports, inheritance, decorators, and call sites are relationships:

- **Without `--neo4j-uri`** — writes a self-contained `graph.cypher` (constraints + indexes, a scoped wipe, then batched `MERGE`s). Load it with `cypher-shell < graph.cypher`. Needs no extra dependencies.
- **With `--neo4j-uri`** — pushes to a live Neo4j over Bolt **incrementally**: only modules whose content hash changed are rewritten, and on a full run modules whose source file vanished are pruned. Requires the `neo4j` extra. Every graph carries a `schema_version` on its `:PyApplication` node.

Call-graph endpoints that aren't present in the symbol table (third-party / framework / RPC targets) are materialized as `:PyExternal` ghost nodes, mirroring the analyzer's own ghost-node behaviour.

The connection options also read from the standard Neo4j environment variables — `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` — when the corresponding flag is omitted (an explicit flag wins). Prefer the env var for the password so it doesn't land in shell history or the process list:

```sh
export NEO4J_URI=bolt://localhost:7687
export NEO4J_PASSWORD=secret
codeanalyzer -i ./my-project --emit neo4j     # credentials picked up from the environment
```

### Schema contract

`--emit schema` writes the machine-readable, version-stamped Neo4j schema (`schema.json`: node labels, relationships, properties, constraints, and indexes). It needs no project and is checked into the repo as `schema.neo4j.json` and bundled in every release as a GitHub Release asset, so a consumer can validate producer/consumer compatibility without invoking the tool. The shape of the contract matches the [`codeanalyzer-typescript`](https://github.com/codellm-devkit/codeanalyzer-typescript) backend.

A UML of the `analysis.json` schema (the `PyApplication` containment tree) is checked in as [`schema-uml.drawio`](./schema-uml.drawio).

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management during development.

### Development Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)

2. Clone the repository:
   ```bash
   git clone https://github.com/codellm-devkit/codeanalyzer-python
   cd codeanalyzer-python
   ```

3. Install dependencies using uv:
   ```bash
   uv sync --all-groups
   ```
   This will install all dependencies including development and test dependencies.

### Running from Source

```bash
uv run codeanalyzer --input /path/to/python/project
uv run codeanalyzer --emit schema > schema.neo4j.json    # regenerate the checked-in schema contract
```

### Running Tests

```bash
uv run pytest --pspec -s
```

The Neo4j schema-conformance test always runs. The Neo4j **bolt** integration test spins up a real
Neo4j via [Testcontainers](https://testcontainers.com/) and is **opt-in** — it needs a container
runtime (Docker or Podman) and is enabled with an environment variable:

```bash
RUN_CONTAINER_TESTS=1 uv run pytest test/test_neo4j_bolt.py -s
```

### Development Dependencies

The project includes additional dependency groups for development:

- **test**: pytest and related testing tools (plus `neo4j` + `testcontainers` for the opt-in Neo4j test)
- **dev**: development tools like ipdb

Install all groups with:
```bash
uv sync --all-groups
```
