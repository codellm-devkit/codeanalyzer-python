# Hand-off bundle — `codeanalyzer-python` schema v2 reference outputs

This directory is the **hand-off manifest** for the `cldk-sdk-frontend` skill: a
frozen, human-readable set of reference outputs from `codeanalyzer-python` at each
analysis level, plus the machine-readable Neo4j schema contract. It is the input
substrate for revising the Python CLDK SDK's Pydantic model layer to canonical
**schema v2** (a separate major release — see *Follow-up* below). Everything here
is generated output; nothing in this directory is a source of truth on its own —
the contract lives in the spec, the decision log, and `schema.neo4j.json`.

## Package + version to pin

Pin the analyzer that produced these samples:

```
pip install "codeanalyzer-python==0.4.0"
```

For L4 alias analysis (the Scalpel points-to oracle), install the optional extra —
without it, L4 degrades to the type-based fallback oracle (still sound, coarser):

```
pip install "codeanalyzer-python[scalpel]==0.4.0"
```

## Schema contract

The authoritative contract for consuming these outputs is, in order:

- **The model** — `docs/superpowers/specs/2026-07-07-schema-v2-four-levels-design.md`:
  the canonical schema v2 design (root envelope, single additive CPG tree, `can://`
  identity, the four-level ladder and per-level gates).
- **The decision log** — `.claude/SCHEMA_DECISIONS.md`: schema-affecting choices
  (param-node placement, SUMMARY endpoints, global qualification, the Scalpel
  points-to verdict). These are the deltas the SDK's shared Pydantic models must encode.
- **The Neo4j contract** — `docs/handoff/schema.neo4j.json` (this bundle): the
  versioned Neo4j projection contract, **`schema_version` 2.0.0** — node labels,
  merge keys, property types, and relationship types. Byte-identical to the repo-root
  `schema.neo4j.json` and to `python -m codeanalyzer --emit schema`.

### Containment vocabulary (keystone-conformant as of 0.4.0)

The `analysis.json` symbol tree uses the **canonical keystone member names** —
no per-language mapping layer is needed (issue #98 closed the old
`classes`/`methods` divergence):

- `PyModule` nests members under **`types`** and **`functions`** (dicts keyed by
  dotted signature).
- `PyClass` nests its members under **`callables`** (plus `attributes`, nested
  **`types`**, `base_classes`).
- A callable (`PyCallable`) carries `callables`/`types` for nesting, and the
  per-level graph slots inline on the callable: `body`, `cfg`, `cdg`, `ddg`,
  `summary`, `parameters`, `call_sites`.
- `call_graph` edges are `{src, dst, prov, weight}` (the list name IS the type);
  every endpoint joins the id space — imported/builtin targets are homed as
  `can://…/@external/<module>/<name>` entries in `external_symbols` (keyed by id,
  `kind:"external"`).

Root envelope keys: `schema_version`, `language`, `max_level`,
`analyzer{name,version}`, `k_limit` (present at L3+ only), `application`.
`application` keys: `symbol_table`, `id`, `kind`, `call_graph`, `external_symbols`,
`param_in`, `param_out`.

## CLI surface

The canonical `--help` is the repo README's **Usage / Options** section
(`README.md` → *Usage*). The options the SDK frontend drives:

```
canpy [OPTIONS]              # module form: python -m codeanalyzer [OPTIONS]

  -i, --input PATH           Project root (not required for --emit schema).
  -o, --output PATH          Output directory for artifacts.
  -f, --format [json|msgpack]   Output format for --emit json (default: json).
      --emit [json|neo4j|schema]
                             Output target: json (analysis.json, default) |
                             neo4j (graph.cypher or live Bolt push) |
                             schema (the versioned Neo4j schema.json contract).
  -a, --analysis-level [1..4]   Analysis depth (default: 1). See levels below.
      --graphs TEXT          Level 3+ only: scope emitted graph sections
                             (cfg,dfg,pdg,sdg). Unknown values / use below -a 3 exit non-zero.
      --app-name TEXT        Logical app name for the :PyApplication anchor
                             and can:// ids (default: input dir name).
      --no-venv / --venv     Skip virtualenv resolution (default: venv).
  -c, --cache-dir PATH       Cache directory.
      --neo4j-uri / --neo4j-user / --neo4j-password / --neo4j-database
                             Push the graph live over Bolt (omit to write graph.cypher).
```

These samples were produced with `--no-venv` on a single-module sample project.

## Sample files — one per level (cumulative; L1 ⊆ L2 ⊆ L3 ⊆ L4)

Each `analysis.lN.json` carries `"schema_version": "2.0.0"` and `"max_level": N`.
Pretty-printed for reading; machine paths sanitized to `/path/to`.

| File | Level | What it adds over the previous level |
| --- | --- | --- |
| `analysis.l1.json` | `-a 1` | Symbol table to callable + Jedi call graph; `call` nodes in each callable `body` with `callee: null`. |
| `analysis.l2.json` | `-a 2` | PyCG call-graph edges; `call` node `callee` backfilled `null → can:// id`. Adds `call_graph`. |
| `analysis.l3.json` | `-a 3` | Intraprocedural dataflow: the rest of `body` + `@entry`/`@exit`, and `cfg`/`cdg`/`ddg` (syntactic, `prov:["ssa"]`). |
| `analysis.l4.json` | `-a 4` | Interprocedural SDG: synthetic param vertices (`formal_in`/`formal_out`/`actual_in`/`actual_out`), `param_in`/`param_out`/`summary` edges, and semantic `ddg` deltas (`prov:["points-to"]`, alias-aware). |
| `graph.cypher` | `-a 4`, `--emit neo4j` | The Neo4j property-graph projection of the same analysis (`MERGE` statements over `can://` ids; `PY_*` edge vocabulary incl. `PY_PARAM_IN`/`PY_PARAM_OUT`/`PY_SUMMARY` and `PY_DDG.prov`). |
| `schema.neo4j.json` | `--emit schema` | The versioned Neo4j contract (2.0.0) — labels, merge keys, property types, relationship types. |

### How the samples were generated

A ~23-line single-module sample (`service.py`) exercising the interesting shapes:
a base class + subclass with a method, a free function that calls another function
passing a parameter (drives L4 `param_in`/`param_out`/`summary`), and an aliased
local `y = x` (drives the alias-aware points-to DDG delta):

```python
class BaseService:
    pass

class Service(BaseService):
    def announce(self, flag):
        message = build(flag)
        if flag:
            message = message + "!"
        return message

def build(x):
    y = x
    return y

def run(flag):
    svc = Service()
    return svc.announce(flag)
```

```
python -m codeanalyzer -i <sample> -a 1 -o <tmp> --no-venv   # → analysis.l1.json
python -m codeanalyzer -i <sample> -a 2 -o <tmp> --no-venv   # → analysis.l2.json
python -m codeanalyzer -i <sample> -a 3 -o <tmp> --no-venv   # → analysis.l3.json
python -m codeanalyzer -i <sample> -a 4 -o <tmp> --no-venv   # → analysis.l4.json
python -m codeanalyzer -i <sample> --emit neo4j -o <tmp> --no-venv   # → graph.cypher
python -m codeanalyzer --emit schema -o <tmp>                # → schema.json
```

In `analysis.l4.json`, the interprocedural flow to verify:
`Service.announce` calls `build(flag)` → an `actual_in`/`actual_out` pair at the
callsite, a `summary` edge over `build`'s pass-through, `param_in`/`param_out`
edges bridging actual ⇄ `build`'s formals, and a `prov:["points-to"]` `ddg` edge
in `run` from the `Service()` construction to the `svc.announce` use.

## Follow-up: the SDK model revision consumes this bundle

The **Python CLDK SDK Pydantic model revision to schema v2** is the separate
`cldk-sdk-frontend` major release that consumes these samples — this bundle is its
input, not part of it. Two boundaries to carry into that work:

- **Model mapping.** The SDK's shared Pydantic models must round-trip every
  `analysis.lN.json` here (envelope + additive CPG tree + `can://` ids), honoring the
  field-name divergence above.
- **Taint / slicing are SDK-side.** The analyzer stops at the interprocedural SDG. It
  emits only the `summary` substrate and **no `taint_flows` section**; backward
  slicing and taint are language-independent labeled reachability computed on the SDK
  side over the emitted graph. Do not expect taint outputs in these samples.
