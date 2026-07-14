// ── constraints & indexes ──
CREATE CONSTRAINT pyapplication_name IF NOT EXISTS FOR (x:PyApplication) REQUIRE x.name IS UNIQUE;
CREATE CONSTRAINT pymodule_id IF NOT EXISTS FOR (x:PyModule) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pysymbol_id IF NOT EXISTS FOR (x:PySymbol) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pysymbol_signature IF NOT EXISTS FOR (x:PySymbol) REQUIRE x.signature IS UNIQUE;
CREATE CONSTRAINT pypackage_name IF NOT EXISTS FOR (x:PyPackage) REQUIRE x.name IS UNIQUE;
CREATE CONSTRAINT pydecorator_name IF NOT EXISTS FOR (x:PyDecorator) REQUIRE x.name IS UNIQUE;
CREATE CONSTRAINT pycallsite_id IF NOT EXISTS FOR (x:PyCallSite) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pyattribute_id IF NOT EXISTS FOR (x:PyAttribute) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pyvariable_id IF NOT EXISTS FOR (x:PyVariable) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pycfgnode_id IF NOT EXISTS FOR (x:PyCFGNode) REQUIRE x.id IS UNIQUE;
CREATE INDEX py_callable_name IF NOT EXISTS FOR (c:PyCallable) ON (c.name);
CREATE INDEX py_class_name IF NOT EXISTS FOR (c:PyClass) ON (c.name);
CREATE FULLTEXT INDEX py_code_fts IF NOT EXISTS FOR (c:PyCallable) ON EACH [c.code, c.docstring];

// ── wipe this project's prior subgraph (externals/packages/decorators are shared) ──
MATCH (a:PyApplication {name: 'sample_proj'})
OPTIONAL MATCH (a)-[:PY_HAS_MODULE]->(m:PyModule)
OPTIONAL MATCH (m)-[:PY_DECLARES|PY_HAS_METHOD|PY_HAS_ATTRIBUTE|PY_DECLARES_VAR|PY_HAS_CALLSITE*1..]->(x)
DETACH DELETE x, m, a;

// ── nodes ──
UNWIND [
  {k: 'sample_proj', p: {schema_version: '2.0.0'}}
] AS row
MERGE (n:PyApplication {name: row.k})
SET n += row.p;
UNWIND [
  {k: 'can://python/sample_proj/service.py/Service/announce(self,flag)@10:18', p: {kind: 'call', start_line: 10, end_line: 10, _module: 'service.py'}},
  {k: 'can://python/sample_proj/service.py/run(flag)@22:10', p: {kind: 'call', start_line: 22, end_line: 22, _module: 'service.py'}},
  {k: 'can://python/sample_proj/service.py/run(flag)@23:11', p: {kind: 'call', start_line: 23, end_line: 23, _module: 'service.py'}}
] AS row
MERGE (n:PyCFGNode {id: row.k})
SET n += row.p;
UNWIND [
  {k: 'service.py#10:18-10:29', p: {id: 'service.py#10:18-10:29', method_name: 'build', argument_types: ['Name'], return_type: 'build', callee_signature: 'service.build', is_constructor_call: false, start_line: 10, start_column: 18, end_line: 10, end_column: 29, _module: 'service.py'}},
  {k: 'service.py#22:10-22:19', p: {id: 'service.py#22:10-22:19', method_name: 'Service', argument_types: [], return_type: 'Service', callee_signature: 'service.Service.__init__', is_constructor_call: true, start_line: 22, start_column: 10, end_line: 22, end_column: 19, _module: 'service.py'}},
  {k: 'service.py#23:11-23:29', p: {id: 'service.py#23:11-23:29', method_name: 'announce', receiver_expr: 'svc', receiver_type: 'Service', argument_types: ['Name'], return_type: 'Service', callee_signature: 'service.Service', is_constructor_call: false, start_line: 23, start_column: 11, end_line: 23, end_column: 29, _module: 'service.py'}}
] AS row
MERGE (n:PyCallSite {id: row.k})
SET n += row.p;
UNWIND [
  {k: 'can://python/sample_proj/service.py', p: {id: 'can://python/sample_proj/service.py', file_key: 'service.py', module_name: 'service', content_hash: '2f3e79bcf69502b60a39cecd31206d6c5e3c6b3419dc2add4de955f2fe87ede2', last_modified: 1784029827.2876227, file_size: 373, _module: 'service.py'}}
] AS row
MERGE (n:PyModule {id: row.k})
SET n += row.p;
UNWIND [
  {k: 'can://python/sample_proj/service.py/BaseService', p: {id: 'can://python/sample_proj/service.py/BaseService', signature: 'service.BaseService', name: 'BaseService', base_classes: [], start_line: 4, end_line: 5, _module: 'service.py'}}
] AS row
MERGE (n:PySymbol {id: row.k})
SET n += row.p, n:PyClass;
UNWIND [
  {k: 'can://python/sample_proj/service.py/Service', p: {id: 'can://python/sample_proj/service.py/Service', signature: 'service.Service', name: 'py/Service', base_classes: ['BaseService'], start_line: 8, end_line: 13, _module: 'service.py'}}
] AS row
MERGE (n:PySymbol {id: row.k})
SET n += row.p, n:PyClass:PyExternal;
UNWIND [
  {k: 'can://python/sample_proj/service.py/Service/announce(self,flag)', p: {id: 'can://python/sample_proj/service.py/Service/announce(self,flag)', signature: 'service.Service.announce', name: 'py/Service/announce(self,flag)', path: '/path/to/sample_proj/service.py', cyclomatic_complexity: 3, code_start_line: 10, start_line: 9, end_line: 13, decorators: [], parameters_json: '[{"default_value": null, "end_column": 21, "end_line": 9, "name": "self", "start_column": 17, "start_line": 9, "type": "Service"}, {"default_value": null, "end_column": 27, "end_line": 9, "name": "flag", "start_column": 23, "start_line": 9, "type": null}]', accessed_symbols_json: '[{"col_offset": 11, "is_builtin": false, "kind": "variable", "lineno": 11, "name": "flag", "qualified_name": null, "scope": "local", "type": null}, {"col_offset": 15, "is_builtin": false, "kind": "variable", "lineno": 13, "name": "message", "qualified_name": "builtins.str", "scope": "local", "type": "str"}, {"col_offset": 18, "is_builtin": false, "kind": "function", "lineno": 10, "name": "build", "qualified_name": "service.build", "scope": "local", "type": "build"}, {"col_offset": 24, "is_builtin": false, "kind": "variable", "lineno": 10, "name": "flag", "qualified_name": null, "scope": "local", "type": null}, {"col_offset": 22, "is_builtin": false, "kind": "variable", "lineno": 12, "name": "message", "qualified_name": null, "scope": "local", "type": null}]', _module: 'service.py'}},
  {k: 'can://python/sample_proj/service.py/build(x)', p: {id: 'can://python/sample_proj/service.py/build(x)', signature: 'service.build', name: 'py/build(x)', path: '/path/to/sample_proj/service.py', cyclomatic_complexity: 2, code_start_line: 17, start_line: 16, end_line: 18, decorators: [], parameters_json: '[{"default_value": null, "end_column": 11, "end_line": 16, "name": "x", "start_column": 10, "start_line": 16, "type": null}]', accessed_symbols_json: '[{"col_offset": 8, "is_builtin": false, "kind": "variable", "lineno": 17, "name": "x", "qualified_name": null, "scope": "local", "type": null}, {"col_offset": 11, "is_builtin": false, "kind": "variable", "lineno": 18, "name": "y", "qualified_name": null, "scope": "local", "type": null}]', _module: 'service.py'}},
  {k: 'can://python/sample_proj/service.py/run(flag)', p: {id: 'can://python/sample_proj/service.py/run(flag)', signature: 'service.run', name: 'py/run(flag)', path: '/path/to/sample_proj/service.py', cyclomatic_complexity: 2, code_start_line: 22, start_line: 21, end_line: 23, decorators: [], parameters_json: '[{"default_value": null, "end_column": 12, "end_line": 21, "name": "flag", "start_column": 8, "start_line": 21, "type": null}]', accessed_symbols_json: '[{"col_offset": 10, "is_builtin": false, "kind": "class", "lineno": 22, "name": "Service", "qualified_name": "service.Service", "scope": "local", "type": "Service"}, {"col_offset": 24, "is_builtin": false, "kind": "variable", "lineno": 23, "name": "flag", "qualified_name": null, "scope": "local", "type": null}, {"col_offset": 11, "is_builtin": false, "kind": "variable", "lineno": 23, "name": "svc", "qualified_name": "service.Service", "scope": "local", "type": "Service"}]', _module: 'service.py'}}
] AS row
MERGE (n:PySymbol {id: row.k})
SET n += row.p, n:PyCallable:PyExternal;
UNWIND [
  {k: 'service.Service.__init__', p: {name: '__init__', module: 'service.Service'}}
] AS row
MERGE (n:PySymbol {signature: row.k})
SET n += row.p, n:PyExternal;
UNWIND [
  {k: 'service.Service.announce#message@10', p: {id: 'service.Service.announce#message@10', name: 'message', initializer: 'build(flag)', scope: 'function', start_line: 10, end_line: 10, _module: 'service.py'}},
  {k: 'service.Service.announce#message@12', p: {id: 'service.Service.announce#message@12', name: 'message', type: 'str', initializer: 'message + \'!\'', scope: 'function', start_line: 12, end_line: 12, _module: 'service.py'}},
  {k: 'service.build#y@17', p: {id: 'service.build#y@17', name: 'y', initializer: 'x', scope: 'function', start_line: 17, end_line: 17, _module: 'service.py'}},
  {k: 'service.run#svc@22', p: {id: 'service.run#svc@22', name: 'svc', type: 'Service', initializer: 'Service()', scope: 'function', start_line: 22, end_line: 22, _module: 'service.py'}}
] AS row
MERGE (n:PyVariable {id: row.k})
SET n += row.p;

// ── relationships ──
UNWIND [
  {f: 'can://python/sample_proj/service.py/Service/announce(self,flag)', t: 'can://python/sample_proj/service.py/build(x)', p: {weight: 1, provenance: ['jedi']}},
  {f: 'can://python/sample_proj/service.py/run(flag)', t: 'can://python/sample_proj/service.py/Service', p: {weight: 1, provenance: ['jedi']}},
  {f: 'can://python/sample_proj/service.py/run(flag)', t: 'service.Service.__init__', p: {weight: 1, provenance: ['jedi']}}
] AS row
MATCH (a:PySymbol {signature: row.f})
MATCH (b:PySymbol {signature: row.t})
MERGE (a)-[r:PY_CALLS]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://python/sample_proj/service.py', t: 'can://python/sample_proj/service.py/BaseService', p: {}},
  {f: 'can://python/sample_proj/service.py', t: 'can://python/sample_proj/service.py/Service', p: {}},
  {f: 'can://python/sample_proj/service.py', t: 'can://python/sample_proj/service.py/build(x)', p: {}},
  {f: 'can://python/sample_proj/service.py', t: 'can://python/sample_proj/service.py/run(flag)', p: {}}
] AS row
MATCH (a:PyModule {id: row.f})
MATCH (b:PySymbol {id: row.t})
MERGE (a)-[r:PY_DECLARES]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://python/sample_proj/service.py/Service/announce(self,flag)', t: 'service.Service.announce#message@10', p: {}},
  {f: 'can://python/sample_proj/service.py/Service/announce(self,flag)', t: 'service.Service.announce#message@12', p: {}},
  {f: 'can://python/sample_proj/service.py/build(x)', t: 'service.build#y@17', p: {}},
  {f: 'can://python/sample_proj/service.py/run(flag)', t: 'service.run#svc@22', p: {}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PyVariable {id: row.t})
MERGE (a)-[r:PY_DECLARES_VAR]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://python/sample_proj/service.py/Service/announce(self,flag)', t: 'service.py#10:18-10:29', p: {}},
  {f: 'can://python/sample_proj/service.py/run(flag)', t: 'service.py#22:10-22:19', p: {}},
  {f: 'can://python/sample_proj/service.py/run(flag)', t: 'service.py#23:11-23:29', p: {}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PyCallSite {id: row.t})
MERGE (a)-[r:PY_HAS_CALLSITE]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://python/sample_proj/service.py/Service/announce(self,flag)', t: 'can://python/sample_proj/service.py/Service/announce(self,flag)@10:18', p: {}},
  {f: 'can://python/sample_proj/service.py/run(flag)', t: 'can://python/sample_proj/service.py/run(flag)@22:10', p: {}},
  {f: 'can://python/sample_proj/service.py/run(flag)', t: 'can://python/sample_proj/service.py/run(flag)@23:11', p: {}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PyCFGNode {id: row.t})
MERGE (a)-[r:PY_HAS_CFG_NODE]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://python/sample_proj/service.py/Service', t: 'can://python/sample_proj/service.py/Service/announce(self,flag)', p: {}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PySymbol {id: row.t})
MERGE (a)-[r:PY_HAS_METHOD]->(b)
SET r += row.p;
UNWIND [
  {f: 'sample_proj', t: 'can://python/sample_proj/service.py', p: {}}
] AS row
MATCH (a:PyApplication {name: row.f})
MATCH (b:PyModule {id: row.t})
MERGE (a)-[r:PY_HAS_MODULE]->(b)
SET r += row.p;
UNWIND [
  {f: 'service.py#10:18-10:29', t: 'can://python/sample_proj/service.py/build(x)', p: {}},
  {f: 'service.py#23:11-23:29', t: 'can://python/sample_proj/service.py/Service', p: {}}
] AS row
MATCH (a:PyCallSite {id: row.f})
MATCH (b:PySymbol {id: row.t})
MERGE (a)-[r:PY_RESOLVES_TO]->(b)
SET r += row.p;
UNWIND [
  {f: 'service.py#22:10-22:19', t: 'service.Service.__init__', p: {}}
] AS row
MATCH (a:PyCallSite {id: row.f})
MATCH (b:PySymbol {signature: row.t})
MERGE (a)-[r:PY_RESOLVES_TO]->(b)
SET r += row.p;
