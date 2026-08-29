# Schema v2 vocabulary (authoritative: `schema.neo4j.json`, `codeanalyzer/schema/py_schema.py`)

Regenerate the Neo4j half with `uv run canpy --emit schema` if this ever disagrees.

## Neo4j node labels

| label | merge key | properties |
| --- | --- | --- |
| `PyApplication` | `name` | analyzer_name, analyzer_version, name, repo_dirty, repo_uri, schema_version, source_revision |
| `PyModule` | `id` | _module, content_hash, file_key, file_size, id, last_modified, module_name |
| `PyClass` | `id` | _module, base_classes, code, decorators, docstring, end_line, entrypoint_frameworks, id, is_entrypoint, name, signature, start_line |
| `PyCallable` | `id` | _module, accessed_symbols_json, code, code_start_line, cyclomatic_complexity, decorators, docstring, end_line, entrypoint_frameworks, id, is_entrypoint, modifiers, name, parameters_json, path, return_type |
| `PyExternal` | `id` | id, module, name |
| `PyPackage` | `name` | name |
| `PyDecorator` | `name` | name, qualified_name |
| `PyAttribute` | `id` | _module, docstring, end_line, id, initializer, name, start_line, type |
| `PyVariable` | `id` | _module, end_line, id, initializer, name, scope, start_line, type |
| `PyBodyNode` | `id` (GLOBAL ordinal) | _module, arguments_json, call_node, end_line, id, is_constructor_call, kind, method_name, receiver_expr, receiver_type, return_type, start_line, var |

`PyBodyNode.kind` values: `entry`, `exit`, `statement`, `branch`, `loop`, `return`,
`raise`, `handler`, `call`, `formal_in`, `formal_out`, `actual_in`, `actual_out`.

## Neo4j relationships

| type | properties | meaning |
| --- | --- | --- |
| `PY_HAS_MODULE` | — | application → module |
| `PY_DECLARES` | — | module → class/function |
| `PY_HAS_METHOD` | — | class → callable |
| `PY_HAS_ATTRIBUTE` | — | class → attribute |
| `PY_DECLARES_VAR` | — | scope → variable |
| `PY_RESOLVES_TO` | — | call body node → resolved `PyCallable`/`PyExternal` |
| `PY_CALLS` | weight, prov[] | condensed call graph; prov ⊆ {jedi, defuse} |
| `PY_EXTENDS` | — | class → base |
| `PY_IMPORTS` | spellings[], imported_names[], aliases[] | module → module/external |
| `PY_DECORATED_BY` | expression, positional_arguments[], keyword_arguments_json | callable/class → decorator |
| `PY_HAS_BODY_NODE` | — | callable → body node |
| `PY_CFG_NEXT` | kind, _k | control flow; merges per kind |
| `PY_CDG` | — | control dependence |
| `PY_DDG` | var, prov[], _k | data dependence; **one edge per (var, prov)**; prov ⊆ {ssa, reaching-defs, points-to} |
| `PY_PARAM_IN` | var | caller `actual_in` → callee `formal_in` (L4) |
| `PY_PARAM_OUT` | var | callee `formal_out` → caller `actual_out` (L4) |
| `PY_SUMMARY` | — | same-callsite `actual_in` → `actual_out` transitive shortcut (L4) |

All dataflow relationships are stored src→dst in the forward direction.

## analysis.json sections (root: `Analysis` envelope → `application`)

| path | shape | keyed by |
| --- | --- | --- |
| `symbol_table` | Dict[file, PyModule] | repo-relative POSIX path |
| `…module.types` | Dict → PyClass | **dotted signature** (`src.pkg.mod.Class`) |
| `…class.callables` | Dict → PyCallable | **bare method name** |
| `…module.functions` | Dict → PyCallable | **bare function name** |
| `…callable.body` | Dict → BodyNode | LOCAL id |
| `…callable.cfg/cdg/ddg/summary` | edge lists | LOCAL id endpoints |
| `call_graph` | List[PyCallEdge] | src/dst are `can://` ids |
| `external_symbols` | Dict → PyExternalSymbol | `can://…/@external/<module>/<name>` |
| `param_in` / `param_out` | List[ParamEdge] | GLOBAL ids (`<callable-id>@<local>`) |
| `entrypoint_report` | coverage/failures for the entrypoint pass | — |

`PyModule.source` holds the file text once; every node's text is
`source.encode("utf-8")[span.bytes[0]:span.bytes[1]].decode("utf-8")`.
`span.start`/`span.end` are `[line, col]`.
