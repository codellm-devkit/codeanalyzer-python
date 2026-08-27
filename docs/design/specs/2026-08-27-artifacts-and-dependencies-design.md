# Artifacts and Dependencies: Schema v2 Foundation for Non-Code Files

**Date:** 2026-08-27
**Status:** Approved (design dialogue in-session; decomposition: one issue, one PR)
**Scope:** codeanalyzer-python, schema v2 additive change (targets 1.3.0)

## Problem

Schema v2 is code-only by construction: `symbol_table` is keyed by `.py` file, and
nothing represents configuration files, dependency manifests, or the packages an
application depends on. This blocks three future capabilities — cross-service
topology, cross-language links, and dependency-aware queries — and leaves basic
questions ("which packages does this app declare?", "which imports are
undeclared?") unanswerable from the analysis output.

This spec is unit 1 of a four-unit arc (foundation, cross-service topology,
cross-language identity, config extractors). It designs the foundation only:
how a non-code file becomes a node, and dependency manifests as the first
extracted meaning.

## Locked decisions

### 1. Placement: sibling map, not symbol_table widening

`symbol_table` stays strictly code (`Dict[file, PyModule]`). Non-code files live
in a parallel map on the application node:

```
application.artifacts: Dict[str, PyArtifact]   # keyed by repo-relative POSIX path
```

### 2. Artifact identity is language-neutral

```
can://artifact/<app>/<repo-relative-path>
```

The first `can://` segment becomes a namespace: a language (`python`, `java`,
`typescript`) for code nodes, the literal `artifact` for non-code files. Two
analyzers over the same monorepo emit the **same id** for the same file — one
node in a merged graph, never per-language duplicates.

**Precondition for cross-analyzer joins:** `<app>` must agree between analyzers.
`--app-name` defaults to the input directory name, so analyzers pointed at
different subdirectories of a monorepo will disagree; joint analysis must pin
`--app-name` explicitly.

### 3. PyArtifact model

| field | type | notes |
| --- | --- | --- |
| `id` | str | `can://artifact/<app>/<path>` |
| `kind` | `"artifact"` | schema-v2 node discriminant |
| `format` | str | `toml` \| `yaml` \| `json` \| `ini` \| `requirements` \| `dockerfile` \| `text` |
| `roles` | List[str] | `dependency-manifest`, `service-topology`, `container-image`, `ci`, `env`, `tool-config`, `unknown` |
| `size_bytes` | int | |
| `sha256` | str | |
| `source` | str | verbatim content, **no size bound** (user decision: do not bound file size yet) |
| `extraction` | `"full"` \| `"partial"` \| `"none"` | `partial` currently only for dynamic `setup.py` |

Capture is **broad** (every recognized config-shaped file becomes a node);
extraction is **narrow** (only dependency manifests get an extractor in this
unit). Later units add extractors — additive edges/records on existing nodes,
never re-keying.

Discovery is a shipped rules table of filename patterns → `(format, roles)`,
same mechanism family as `entrypoints/rules.yml`. No user-extension flag yet.

### 4. Dependency model: declared + evidence-tagged binding

```
application.dependencies: List[PyDependency]
application.unresolved_imports: List[PyImportBinding]
```

`PyDependency`: `name` (PEP 503 normalized), `spec`, `kind`
(`runtime`|`dev`|`optional`|`build`), `extras`, `declared_in` (artifact id),
`locked_version` (optional), `provides_imports` (top-level import names),
`prov` (list).

`prov` vocabulary (same idiom as call-edge provenance):

- `declared` — read from a manifest
- `lockfile` — pinned version from a lock file
- `installed-metadata` — read from the venv's `.dist-info` (opt-in only)
- `heuristic` — name-match fallback for import binding

`unresolved_imports` is first-class output: every top-level import the symbol
table saw that no declared dependency accounts for, with any partial binding
and its `prov`. This is deliberately the interesting section — it surfaces
undeclared dependencies instead of silently omitting them.

Lock files never create dependency records; they only backfill
`locked_version` on declared ones. Transitive (lock-only) packages are
deliberately skipped — no transitive graph in this unit.

### 5. Determinism: deterministic default, probing opt-in

The default run reads only files in the repo — byte-identical output across
machines. A new flag `--resolve-installed` additionally probes the venv's
installed metadata for import→distribution mapping; those records carry
`prov ["installed-metadata"]`. The CI determinism gate runs the default.

### 6. Extraction targets (this unit)

| manifest | extracted | notes |
| --- | --- | --- |
| `requirements*.txt` | declared deps; `-r`/`-c` includes chased | `kind` from filename convention |
| `pyproject.toml` | PEP 621 `[project.dependencies]` + `optional-dependencies`; Poetry `[tool.poetry.*]`; `[build-system].requires` (`kind: build`) | one parser, three dialects |
| `setup.py` | `install_requires`/`extras_require` via **static AST only**; literals lifted, never executed | dynamic values → artifact `extraction: "partial"`; imports then surface via `unresolved_imports` |
| `setup.cfg` | `[options] install_requires`, `extras_require` | ini parse |
| `Pipfile` / `Pipfile.lock` | declared + pins | |
| `poetry.lock`, `uv.lock` | `locked_version` backfill | |
| `environment.yml` | conda deps incl. `pip:` sublist | |

### 7. Neo4j projection

Language-neutral subgraph gets language-neutral labels (no `Py` prefix), so
sibling analyzers MERGE onto the same nodes:

| label | merge key | properties |
| --- | --- | --- |
| `:Artifact` | `id` (`can://artifact/...`) | path, format, roles, sha256, size_bytes, source |
| `:Package` | `id` = purl (`pkg:pypi/<name>`) | ecosystem, name |

purl as package id is the cross-language join: `pkg:maven/...` and
`pkg:pypi/...` coexist uniformly.

Edges:

```
(:PyApplication)-[:HAS_ARTIFACT]->(:Artifact)
(:Artifact)-[:DECLARES_DEPENDENCY {spec, kind, extras, prov}]->(:Package)
(:Artifact)-[:LOCKS {version}]->(:Package)
(:Package)-[:PY_PROVIDES]->(:PyExternal)
(:PyApplication)-[:PY_UNRESOLVED_IMPORT {prov}]->(:PyExternal)
```

`PY_PROVIDES` targets the **existing** `:PyExternal` ghosts (same
`can://python/<app>/@external/<module>` ids the L2 call graph MERGEs on), so
dependencies join the call graph rather than sit beside it:

```cypher
MATCH (c:PyCallable)-[:PY_CALLS]->(:PyExternal)<-[:PY_PROVIDES]-(p:Package {id:"pkg:pypi/requests"})
RETURN c.id
```

Config-role artifacts get node + roles + source and zero extracted edges this
unit. New DDL: unique constraints on `Artifact.id` and `Package.id`. Neo4j
schema version moves additively within 2.x. Full-depth-always rule unchanged.

### 8. Pipeline and CLI

- Artifact scan is **L1 data**: runs at every level, output must not vary with
  `-a` (same posture as entrypoints). Monotonicity gate holds trivially.
- Runs after the symbol table (needs module import lists for
  `unresolved_imports`), before the call graph.
- New package `codeanalyzer/artifacts/`: `discovery.py` (walk + rules table),
  `parsers.py` (toml/yaml/ini/requirements/setup.py-AST readers),
  `dependencies.py` (records, lock backfill, import binding). Walk reuses the
  symbol table's ignore set, sorted order.
- CLI: exactly one new flag, `--resolve-installed`. Scan is default-on with no
  toggle.
- Not cached: scan cost is trivial; caching would add invalidation surface for
  nothing.

## Caveats

- `<app>` agreement is a precondition for cross-analyzer artifact joins (see §2).
- `setup.py` extraction is static-AST only; computed dependency lists are
  recorded as `extraction: "partial"`, never executed (determinism).
- Lock-only (transitive) packages are out of scope by decision, not omission.
- `source` is unbounded by decision; revisit only with measured payload numbers.
- Import→package binding without `--resolve-installed` relies on
  `declared` names + `heuristic` matching; `installed-metadata` precision is
  opt-in and machine-dependent by design.

## Decomposition and release plan

One work-item issue on codeanalyzer-python, closed by one PR; ships in the
next minor (1.3.0, additive). Sibling analyzers adopt the `can://artifact/`
namespace, neutral Neo4j labels, and purl ids when their own work starts — no
epic until a second repo does.

## Definition of done

- `application.artifacts` / `dependencies` / `unresolved_imports` emitted at
  every level with identical content; monotonicity gate green.
- All §6 formats parsed on a fixture project carrying every format; prov and
  purl ids asserted.
- Neo4j rows for §7 vocabulary via existing row tests; DDL constraints added.
- Default run byte-identical across two consecutive runs;
  `--resolve-installed` exercised in one gated test.
- Full suite green; schema decision recorded in `.claude/SCHEMA_DECISIONS.md`.
