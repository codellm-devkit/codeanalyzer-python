# Neo4j vocabulary (authoritative: `schema.neo4j.json`; regenerate with `uv run canpy --emit schema`)

## Node labels

| label | merge key | properties |
| --- | --- | --- |
| `PyApplication` | `name` | analyzer_name, analyzer_version, name, repo_dirty, repo_uri, schema_version, source_revision, entrypoint_frameworks, entrypoint_report_json |
| `PyModule` | `id` | _module, content_hash, file_key, file_size, id, last_modified, module_name |
| `PyClass` | `id` | _module, base_classes, code, decorators, docstring, end_line, entrypoint_frameworks, id, is_entrypoint, name, signature, start_line |
| `PyCallable` | `id` | _module, accessed_symbols_json, code, code_start_line, cyclomatic_complexity, decorators, docstring, end_line, entrypoint_frameworks, id, is_entrypoint, modifiers, name, parameters_json, path, return_type |
| `PyExternal` | `id` | id, module, name — ghosts; member-level (`…/@external/<module>/<name>`) or module-level (`…/@external/<module>`) |
| `PyPackage` | `name` | name (Python package dirs, not PyPI packages) |
| `PyDecorator` | `name` | name, qualified_name |
| `PyAttribute` | `id` | _module, docstring, end_line, id, initializer, name, start_line, type |
| `PyVariable` | `id` | _module, end_line, id, initializer, name, scope, start_line, type |
| `PyBodyNode` | `id` (GLOBAL ordinal) | _module, arguments_json, call_node, end_line, id, is_constructor_call, kind, method_name, receiver_expr, receiver_type, return_type, start_line, var |
| `Artifact` | `id` (`can://artifact/…`) | extraction, format, id, path, roles, sha256, size_bytes, source — **language-neutral, no Py prefix by design**; every non-`.py` file is inventoried (never-drop), binaries with empty source. `source` is the WHOLE file or `""` (binary, or `--no-artifact-text`) — never a prefix, so it needs no companion flag to be trusted |
| `Package` | `id` (purl `pkg:pypi/<name>`, `<name>` PEP 503 normalized: lowercase, `[-_.]+` → `-`, so always `pkg:pypi/pyyaml`, never `pkg:pypi/PyYAML`) | ecosystem, id, name — language-neutral |

`PyBodyNode.kind`: `entry`, `exit`, `statement`, `branch`, `loop`, `return`,
`raise`, `handler`, `call`, `formal_in`, `formal_out`, `actual_in`, `actual_out`.

`Artifact.format`: `toml` | `yaml` | `json` | `ini` | `requirements` | `dockerfile` | `text` | `binary`.
`Artifact.roles` (list): `dependency-manifest`, `service-topology`,
`container-image`, `ci`, `env`, `tool-config`, `packaging`, `script`, `docs`,
`legal`, `unknown`. `Artifact.extraction`: `none` | `partial` | `full`.

## Relationships

| type | from → to | properties | notes |
| --- | --- | --- | --- |
| `PY_HAS_MODULE` | application → module | — | |
| `PY_DECLARES` | module → class/function | — | |
| `PY_HAS_METHOD` | class → callable | — | |
| `PY_HAS_ATTRIBUTE` | class → attribute | — | |
| `PY_DECLARES_VAR` | scope → variable | — | |
| `PY_RESOLVES_TO` | call body node → callable/external | — | per-callsite resolution (L2) |
| `PY_CALLS` | callable/external → callable/external | weight, prov[] | condensed call graph; prov ⊆ {jedi, defuse} |
| `PY_EXTENDS` | class → class/external | — | inheritance |
| `PY_IMPORTS` | module → module/external | spellings[], imported_names[], aliases[] | |
| `PY_DECORATED_BY` | callable/class → decorator | expression, positional_arguments[], keyword_arguments_json | |
| `PY_HAS_BODY_NODE` | callable → body node | — | |
| `PY_CFG_NEXT` | body → body | kind, `_k` | control flow; one edge per kind |
| `PY_CDG` | body → body | — | control dependence |
| `PY_DDG` | body → body | var, prov[], `_k` | one edge per (var, prov); prov ⊆ {ssa, reaching-defs, points-to} |
| `PY_PARAM_IN` | caller `actual_in` → callee `formal_in` | var | L4 |
| `PY_PARAM_OUT` | callee `formal_out` → caller `actual_out` | var | L4 |
| `PY_SUMMARY` | `actual_in` → `actual_out` (same callsite) | — | L4 transitive shortcut |
| `HAS_ARTIFACT` | application → artifact | — | L1 |
| `DECLARES_DEPENDENCY` | artifact → package | spec, kind, extras[], prov[], direct, `_k` | one edge per kind; `direct: false` = lockfile-only transitive |
| `LOCKS` | lock artifact → package | version | locks never create packages alone |
| `PY_PROVIDES` | package → external (module-level ghost) | — | joins dependencies into the call graph |
| `PY_UNRESOLVED_IMPORT` | application → external (module-level ghost) | prov[] | undeclared-import hygiene |
| `PY_USES_CONFIG` | body node → ConfigKey | prov[] | which statement reads which key; prov ⊆ {literal, dataflow}; superset-monotonic `-a 2 ⊆ 3 ⊆ 4` |
| `PY_READS_CONFIG_UNRESOLVED` | application → external (callee ghost) | key, reason, prov[], `_k` | config reads that never resolved; reason ∈ {non-literal, undefined-key}; the unresolved list is the edge set's sanctioned complement (shrinks as levels rise) |

All dataflow relationships are stored src→dst in the forward direction.

Call arguments carry literal evidence: `PyCallArgument.value` (JSON-encoded
constant — `json.loads` it) and `.name` (bare identifier) back the config_use
tiers and are queryable via `PyBodyNode.arguments_json`.
Dependency `prov` vocabulary: `declared`, `lockfile`, `installed-metadata`
(only with `--resolve-installed`), `heuristic`.
