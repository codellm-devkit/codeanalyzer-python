// ── constraints & indexes ──
CREATE CONSTRAINT pyapplication_id IF NOT EXISTS FOR (x:PyApplication) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pymodule_id IF NOT EXISTS FOR (x:PyModule) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pysymbol_id IF NOT EXISTS FOR (x:PySymbol) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pypackage_name IF NOT EXISTS FOR (x:PyPackage) REQUIRE x.name IS UNIQUE;
CREATE CONSTRAINT pydecorator_name IF NOT EXISTS FOR (x:PyDecorator) REQUIRE x.name IS UNIQUE;
CREATE CONSTRAINT pyattribute_id IF NOT EXISTS FOR (x:PyAttribute) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pyvariable_id IF NOT EXISTS FOR (x:PyVariable) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT pybodynode_id IF NOT EXISTS FOR (x:PyBodyNode) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT artifact_id IF NOT EXISTS FOR (x:Artifact) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT package_id IF NOT EXISTS FOR (x:Package) REQUIRE x.id IS UNIQUE;
CREATE CONSTRAINT configkey_id IF NOT EXISTS FOR (x:ConfigKey) REQUIRE x.id IS UNIQUE;
CREATE INDEX py_callable_name IF NOT EXISTS FOR (c:PyCallable) ON (c.name);
CREATE INDEX py_class_name IF NOT EXISTS FOR (c:PyClass) ON (c.name);
CREATE FULLTEXT INDEX py_code_fts IF NOT EXISTS FOR (c:PyCallable) ON EACH [c.code, c.docstring];
CREATE INDEX py_can_node_id IF NOT EXISTS FOR (n:PyCanNode) ON (n.id);

// ── wipe this project's prior subgraph (externals/packages/decorators are shared) ──
MATCH (x:PyCanNode) WHERE x.id = 'can://sample-app/sample-app' OR x.id STARTS WITH 'can://sample-app/sample-app/'
CALL { WITH x DETACH DELETE x } IN TRANSACTIONS OF 1000 ROWS;

// ── nodes ──
UNWIND [
  {k: 'can://sample-app/sample-app/@external/service.Service/__init__', p: {name: '__init__', module: 'service.Service'}}
] AS row
MERGE (n:PySymbol {id: row.k})
SET n += row.p, n:PyExternal:PyCanNode;
UNWIND [
  {k: 'can://sample-app/sample-app', p: {id: 'can://sample-app/sample-app', name: 'sample-app', application_name: 'sample-app', identity_namespace: 'sample-app', identity_version: 2, schema_version: '2.0.0', analyzer_name: 'codeanalyzer-python', analyzer_version: '1.6.2', entrypoint_frameworks: [], entrypoint_report_json: '{"errors": [], "frameworks_detected": [], "rulesets": ["shipped"], "unresolved": {}}'}}
] AS row
MERGE (n:PyApplication {id: row.k})
SET n += row.p, n:PyCanNode;
UNWIND [
  {k: 'can://sample-app/sample-app/python/service.py', p: {id: 'can://sample-app/sample-app/python/service.py', file_key: 'service.py', module_name: 'service', source: 'class BaseService:\n    pass\n\nclass Service(BaseService):\n    def announce(self, flag):\n        message = build(flag)\n        if flag:\n            message = message + "!"\n        return message\n\ndef build(x):\n    y = x\n    return y\n\ndef run(flag):\n    svc = Service()\n    return svc.announce(flag)\n', content_hash: '7097c745b302b502071549264a3eab63d8ba4db7c11174aca96a06d023db62e9', last_modified: 1793382888.634782, file_size: 297}}
] AS row
MERGE (n:PyModule {id: row.k})
SET n += row.p, n:PyCanNode;
UNWIND [
  {k: 'can://sample-app/sample-app/python/service.py/build(x)', p: {id: 'can://sample-app/sample-app/python/service.py/build(x)', signature: 'service.build', name: 'build', path: '/path/to/sample-app/service.py', cyclomatic_complexity: 2, code: 'def build(x):\n    y = x\n    return y', code_start_line: 12, start_line: 11, start_column: 0, end_line: 13, end_column: 12, start_byte: 194, end_byte: 230, decorators: [], modifiers: [], parameters_json: '[{"decorators": [], "default_value": null, "end_column": 11, "end_line": 11, "id": "can://sample-app/sample-app/python/service.py/build(x)@formal_in:0", "name": "x", "start_column": 10, "start_line": 11, "type": null}]', accessed_symbols_json: '[{"col_offset": 8, "is_builtin": false, "kind": "variable", "lineno": 12, "name": "x", "qualified_name": null, "scope": "local", "type": null}, {"col_offset": 11, "is_builtin": false, "kind": "variable", "lineno": 13, "name": "y", "qualified_name": null, "scope": "local", "type": null}]', is_entrypoint: false, entrypoint_frameworks: []}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)', p: {id: 'can://sample-app/sample-app/python/service.py/run(flag)', signature: 'service.run', name: 'run', path: '/path/to/sample-app/service.py', cyclomatic_complexity: 2, code: 'def run(flag):\n    svc = Service()\n    return svc.announce(flag)', code_start_line: 16, start_line: 15, start_column: 0, end_line: 17, end_column: 29, start_byte: 232, end_byte: 296, decorators: [], modifiers: [], parameters_json: '[{"decorators": [], "default_value": null, "end_column": 12, "end_line": 15, "id": "can://sample-app/sample-app/python/service.py/run(flag)@formal_in:0", "name": "flag", "start_column": 8, "start_line": 15, "type": null}]', accessed_symbols_json: '[{"col_offset": 10, "is_builtin": false, "kind": "class", "lineno": 16, "name": "Service", "qualified_name": "service.Service", "scope": "local", "type": "Service"}, {"col_offset": 24, "is_builtin": false, "kind": "variable", "lineno": 17, "name": "flag", "qualified_name": null, "scope": "local", "type": null}, {"col_offset": 11, "is_builtin": false, "kind": "variable", "lineno": 17, "name": "svc", "qualified_name": "service.Service", "scope": "local", "type": "Service"}]', is_entrypoint: false, entrypoint_frameworks: []}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', p: {id: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', signature: 'service.Service.announce', name: 'announce', path: '/path/to/sample-app/service.py', cyclomatic_complexity: 3, code: 'def announce(self, flag):\n        message = build(flag)\n        if flag:\n            message = message + "!"\n        return message', code_start_line: 6, start_line: 5, start_column: 4, end_line: 9, end_column: 22, start_byte: 61, end_byte: 192, decorators: [], modifiers: [], parameters_json: '[{"decorators": [], "default_value": null, "end_column": 21, "end_line": 5, "id": "can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:0", "name": "self", "start_column": 17, "start_line": 5, "type": "Service"}, {"decorators": [], "default_value": null, "end_column": 27, "end_line": 5, "id": "can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:1", "name": "flag", "start_column": 23, "start_line": 5, "type": null}]', accessed_symbols_json: '[{"col_offset": 11, "is_builtin": false, "kind": "variable", "lineno": 7, "name": "flag", "qualified_name": null, "scope": "local", "type": null}, {"col_offset": 15, "is_builtin": false, "kind": "variable", "lineno": 9, "name": "message", "qualified_name": "builtins.str", "scope": "local", "type": "str"}, {"col_offset": 18, "is_builtin": false, "kind": "function", "lineno": 6, "name": "build", "qualified_name": "service.build", "scope": "local", "type": "build"}, {"col_offset": 24, "is_builtin": false, "kind": "variable", "lineno": 6, "name": "flag", "qualified_name": null, "scope": "local", "type": null}, {"col_offset": 22, "is_builtin": false, "kind": "variable", "lineno": 8, "name": "message", "qualified_name": null, "scope": "local", "type": null}]', is_entrypoint: false, entrypoint_frameworks: []}}
] AS row
MERGE (n:PySymbol {id: row.k})
SET n += row.p, n:PyCallable:PyCanNode;
UNWIND [
  {k: 'can://sample-app/sample-app/python/service.py/build(x)/y@12', p: {id: 'can://sample-app/sample-app/python/service.py/build(x)/y@12', name: 'y', initializer: 'x', scope: 'function', start_line: 12, start_column: 4, end_line: 12, end_column: 5, start_byte: 212, end_byte: 213}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)/svc@16', p: {id: 'can://sample-app/sample-app/python/service.py/run(flag)/svc@16', name: 'svc', type: 'Service', initializer: 'Service()', scope: 'function', start_line: 16, start_column: 4, end_line: 16, end_column: 7, start_byte: 251, end_byte: 254}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)/message@6', p: {id: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)/message@6', name: 'message', initializer: 'build(flag)', scope: 'function', start_line: 6, start_column: 8, end_line: 6, end_column: 15, start_byte: 95, end_byte: 102}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)/message@8', p: {id: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)/message@8', name: 'message', type: 'str', initializer: 'message + \'!\'', scope: 'function', start_line: 8, start_column: 12, end_line: 8, end_column: 19, start_byte: 146, end_byte: 153}}
] AS row
MERGE (n:PyVariable {id: row.k})
SET n += row.p, n:PyCanNode;
UNWIND [
  {k: 'can://sample-app/sample-app/python/service.py/BaseService', p: {id: 'can://sample-app/sample-app/python/service.py/BaseService', signature: 'service.BaseService', name: 'BaseService', code: 'class BaseService:\n    pass', base_classes: [], decorators: [], start_line: 1, start_column: 0, end_line: 2, end_column: 8, start_byte: 0, end_byte: 27, is_entrypoint: false, entrypoint_frameworks: []}},
  {k: 'can://sample-app/sample-app/python/service.py/Service', p: {id: 'can://sample-app/sample-app/python/service.py/Service', signature: 'service.Service', name: 'Service', code: 'class Service(BaseService):\n    def announce(self, flag):\n        message = build(flag)\n        if flag:\n            message = message + "!"\n        return message', base_classes: ['BaseService'], decorators: [], start_line: 4, start_column: 0, end_line: 9, end_column: 22, start_byte: 29, end_byte: 192, is_entrypoint: false, entrypoint_frameworks: []}}
] AS row
MERGE (n:PySymbol {id: row.k})
SET n += row.p, n:PyClass:PyCanNode;
UNWIND [
  {k: 'can://sample-app/sample-app/python/service.py/build(x)@entry', p: {kind: 'entry'}},
  {k: 'can://sample-app/sample-app/python/service.py/build(x)@12:4', p: {kind: 'statement', start_line: 12, start_column: 4, end_line: 12, end_column: 9, start_byte: 212, end_byte: 217}},
  {k: 'can://sample-app/sample-app/python/service.py/build(x)@13:4', p: {kind: 'return', start_line: 13, start_column: 4, end_line: 13, end_column: 12, start_byte: 222, end_byte: 230}},
  {k: 'can://sample-app/sample-app/python/service.py/build(x)@exit', p: {kind: 'exit'}},
  {k: 'can://sample-app/sample-app/python/service.py/build(x)@formal_in:0', p: {kind: 'formal_in', var: 'x'}},
  {k: 'can://sample-app/sample-app/python/service.py/build(x)@formal_out', p: {kind: 'formal_out', var: '<return>'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@16:10', p: {kind: 'call', start_line: 16, start_column: 10, end_line: 16, end_column: 19, start_byte: 257, end_byte: 266, callee_signature: 'service.Service.__init__', call_node: '16:4', method_name: 'Service', return_type: 'Service', is_constructor_call: true}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@17:11', p: {kind: 'call', start_line: 17, start_column: 11, end_line: 17, end_column: 29, start_byte: 278, end_byte: 296, callee_signature: 'service.Service.announce', call_node: '17:4', method_name: 'announce', receiver_expr: 'svc', receiver_type: 'Service', return_type: 'str', is_constructor_call: false, arguments_json: '[{"ast_kind": "Name", "inferred_type": null, "name": "flag", "value": null}]'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@entry', p: {kind: 'entry'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', p: {kind: 'statement', start_line: 16, start_column: 4, end_line: 16, end_column: 19, start_byte: 251, end_byte: 266}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', p: {kind: 'return', start_line: 17, start_column: 4, end_line: 17, end_column: 29, start_byte: 271, end_byte: 296}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@exit', p: {kind: 'exit'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:0', p: {kind: 'formal_in', var: 'flag'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:1', p: {kind: 'formal_in', var: '<global>:service::Service'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:2', p: {kind: 'formal_in', var: '<global>:service::build'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_out:0', p: {kind: 'formal_out', var: '<return>'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_out:1', p: {kind: 'formal_out', var: 'flag'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:0', p: {kind: 'actual_in', var: 'self', call_node: '17:4'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:1', p: {kind: 'actual_in', var: 'flag', call_node: '17:4'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:2', p: {kind: 'actual_in', var: '<global>:service::build', call_node: '17:4'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:0', p: {kind: 'actual_out', var: '<return>', call_node: '17:4'}},
  {k: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:1', p: {kind: 'actual_out', var: 'flag', call_node: '17:4'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:18', p: {kind: 'call', start_line: 6, start_column: 18, end_line: 6, end_column: 29, start_byte: 105, end_byte: 116, callee_signature: 'service.build', call_node: '6:8', method_name: 'build', is_constructor_call: false, arguments_json: '[{"ast_kind": "Name", "inferred_type": null, "name": "flag", "value": null}]'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@entry', p: {kind: 'entry'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', p: {kind: 'statement', start_line: 6, start_column: 8, end_line: 6, end_column: 29, start_byte: 95, end_byte: 116}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', p: {kind: 'branch', start_line: 7, start_column: 11, end_line: 7, end_column: 15, start_byte: 128, end_byte: 132}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@8:12', p: {kind: 'statement', start_line: 8, start_column: 12, end_line: 8, end_column: 35, start_byte: 146, end_byte: 169}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', p: {kind: 'return', start_line: 9, start_column: 8, end_line: 9, end_column: 22, start_byte: 178, end_byte: 192}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@exit', p: {kind: 'exit'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:0', p: {kind: 'formal_in', var: 'self'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:1', p: {kind: 'formal_in', var: 'flag'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:2', p: {kind: 'formal_in', var: '<global>:service::build'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_out:0', p: {kind: 'formal_out', var: '<return>'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_out:1', p: {kind: 'formal_out', var: 'flag'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_in:0', p: {kind: 'actual_in', var: 'x', call_node: '6:8'}},
  {k: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_out', p: {kind: 'actual_out', var: '<return>', call_node: '6:8'}}
] AS row
MERGE (n:PyBodyNode {id: row.k})
SET n += row.p, n:PyCanNode;
UNWIND [
  {k: 'can://sample-app/sample-app/@external/service.Service/__init__', p: {name: '__init__', module: 'service.Service'}}
] AS row
MERGE (n:PySymbol {id: row.k})
SET n += row.p, n:PyExternal:PyCanNode;

// ── relationships ──
UNWIND [
  {f: 'can://sample-app/sample-app', t: 'can://sample-app/sample-app/python/service.py', p: {}}
] AS row
MATCH (a:PyApplication {id: row.f})
MATCH (b:PyModule {id: row.t})
MERGE (a)-[r:PY_HAS_MODULE]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py', t: 'can://sample-app/sample-app/python/service.py/build(x)', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py', t: 'can://sample-app/sample-app/python/service.py/run(flag)', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py', t: 'can://sample-app/sample-app/python/service.py/BaseService', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py', t: 'can://sample-app/sample-app/python/service.py/Service', p: {}}
] AS row
MATCH (a:PyModule {id: row.f})
MATCH (b:PySymbol {id: row.t})
MERGE (a)-[r:PY_DECLARES]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/build(x)', t: 'can://sample-app/sample-app/python/service.py/build(x)/y@12', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)/svc@16', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)/message@6', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)/message@8', p: {}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PyVariable {id: row.t})
MERGE (a)-[r:PY_DECLARES_VAR]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/Service', t: 'can://sample-app/sample-app/python/service.py/BaseService', p: {}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PySymbol {id: row.t})
MERGE (a)-[r:PY_EXTENDS]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/Service', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', p: {}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PySymbol {id: row.t})
MERGE (a)-[r:PY_HAS_METHOD]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/build(x)', p: {weight: 1, prov: ['jedi']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/@external/service.Service/__init__', p: {weight: 1, prov: ['jedi']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', p: {weight: 1, prov: ['jedi']}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PySymbol {id: row.t})
MERGE (a)-[r:PY_CALLS]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/build(x)', t: 'can://sample-app/sample-app/python/service.py/build(x)@entry', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)', t: 'can://sample-app/sample-app/python/service.py/build(x)@12:4', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)', t: 'can://sample-app/sample-app/python/service.py/build(x)@13:4', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)', t: 'can://sample-app/sample-app/python/service.py/build(x)@exit', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)', t: 'can://sample-app/sample-app/python/service.py/build(x)@formal_in:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)', t: 'can://sample-app/sample-app/python/service.py/build(x)@formal_out', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@16:10', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:11', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@entry', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@exit', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:1', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:2', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_out:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_out:1', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:1', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:2', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:1', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:18', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@entry', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@8:12', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@exit', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:1', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:2', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_out:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_out:1', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_in:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_out', p: {}}
] AS row
MATCH (a:PySymbol {id: row.f})
MATCH (b:PyBodyNode {id: row.t})
MERGE (a)-[r:PY_HAS_BODY_NODE]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@entry', t: 'can://sample-app/sample-app/python/service.py/build(x)@12:4', k: 'fallthrough', p: {kind: 'fallthrough'}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@12:4', t: 'can://sample-app/sample-app/python/service.py/build(x)@13:4', k: 'fallthrough', p: {kind: 'fallthrough'}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@13:4', t: 'can://sample-app/sample-app/python/service.py/build(x)@exit', k: 'return', p: {kind: 'return'}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@entry', t: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', k: 'fallthrough', p: {kind: 'fallthrough'}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', k: 'fallthrough', p: {kind: 'fallthrough'}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@exit', k: 'exception', p: {kind: 'exception'}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@exit', k: 'exception', p: {kind: 'exception'}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@exit', k: 'return', p: {kind: 'return'}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@entry', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', k: 'fallthrough', p: {kind: 'fallthrough'}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', k: 'fallthrough', p: {kind: 'fallthrough'}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@exit', k: 'exception', p: {kind: 'exception'}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@8:12', k: 'true', p: {kind: 'true'}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', k: 'false', p: {kind: 'false'}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@8:12', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', k: 'fallthrough', p: {kind: 'fallthrough'}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@exit', k: 'return', p: {kind: 'return'}}
] AS row
MATCH (a:PyBodyNode {id: row.f})
MATCH (b:PyBodyNode {id: row.t})
MERGE (a)-[r:PY_CFG_NEXT {_k: row.k}]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@entry', t: 'can://sample-app/sample-app/python/service.py/build(x)@12:4', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@entry', t: 'can://sample-app/sample-app/python/service.py/build(x)@13:4', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@entry', t: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@entry', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@8:12', p: {}}
] AS row
MATCH (a:PyBodyNode {id: row.f})
MATCH (b:PyBodyNode {id: row.t})
MERGE (a)-[r:PY_CDG]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@entry', t: 'can://sample-app/sample-app/python/service.py/build(x)@12:4', k: 'x|ssa', p: {var: 'x', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@12:4', t: 'can://sample-app/sample-app/python/service.py/build(x)@13:4', k: 'y|ssa', p: {var: 'y', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@13:4', t: 'can://sample-app/sample-app/python/service.py/build(x)@formal_out', k: '<return>|reaching-defs', p: {var: '<return>', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@formal_in:0', t: 'can://sample-app/sample-app/python/service.py/build(x)@12:4', k: 'x|reaching-defs', p: {var: 'x', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@entry', t: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', k: 'service::Service|ssa', p: {var: 'service::Service', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@entry', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', k: 'flag|ssa', p: {var: 'flag', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', k: 'svc|ssa', p: {var: 'svc', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', k: 'svc.announce|ssa', p: {var: 'svc.announce', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:0', k: 'svc|reaching-defs', p: {var: 'svc', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:0', k: 'svc.announce|reaching-defs', p: {var: 'svc.announce', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_out:0', k: '<return>|reaching-defs', p: {var: '<return>', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', t: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_out:1', k: 'flag.*|reaching-defs', p: {var: 'flag.*', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:0', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', k: '<return>|reaching-defs', p: {var: '<return>', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:1', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', k: 'flag|reaching-defs', p: {var: 'flag', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@entry', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:0', k: 'svc.announce|reaching-defs', p: {var: 'svc.announce', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:0', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', k: 'flag|reaching-defs', p: {var: 'flag', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:0', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:1', k: 'flag|reaching-defs', p: {var: 'flag', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@formal_in:1', t: 'can://sample-app/sample-app/python/service.py/run(flag)@16:4', k: 'service::Service|reaching-defs', p: {var: 'service::Service', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@entry', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4', k: 'svc.announce|points-to', p: {var: 'svc.announce', prov: ['points-to']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@entry', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', k: 'flag|ssa', p: {var: 'flag', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@entry', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', k: 'service::build|ssa', p: {var: 'service::build', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@entry', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', k: 'flag|ssa', p: {var: 'flag', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', k: 'flag|ssa', p: {var: 'flag', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@8:12', k: 'message|ssa', p: {var: 'message', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', k: 'message|ssa', p: {var: 'message', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@8:12', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', k: 'message|ssa', p: {var: 'message', prov: ['ssa']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_out:1', k: 'flag.*|reaching-defs', p: {var: 'flag.*', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_out', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', k: '<return>|reaching-defs', p: {var: '<return>', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@9:8', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_out:0', k: '<return>|reaching-defs', p: {var: '<return>', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:1', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', k: 'flag|reaching-defs', p: {var: 'flag', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:1', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_in:0', k: 'flag|reaching-defs', p: {var: 'flag', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:1', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@7:11', k: 'flag|reaching-defs', p: {var: 'flag', prov: ['reaching-defs']}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:2', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8', k: 'service::build|reaching-defs', p: {var: 'service::build', prov: ['reaching-defs']}}
] AS row
MATCH (a:PyBodyNode {id: row.f})
MATCH (b:PyBodyNode {id: row.t})
MERGE (a)-[r:PY_DDG {_k: row.k}]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@16:10', t: 'can://sample-app/sample-app/@external/service.Service/__init__', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:11', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:18', t: 'can://sample-app/sample-app/python/service.py/build(x)', p: {}}
] AS row
MATCH (a:PyBodyNode {id: row.f})
MATCH (b:PySymbol {id: row.t})
MERGE (a)-[r:PY_RESOLVES_TO]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:1', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:1', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:1', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:2', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:0', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:2', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:1', p: {}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_in:0', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_out', p: {}}
] AS row
MATCH (a:PyBodyNode {id: row.f})
MATCH (b:PyBodyNode {id: row.t})
MERGE (a)-[r:PY_SUMMARY]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_in:0', t: 'can://sample-app/sample-app/python/service.py/build(x)@formal_in:0', p: {var: 'x'}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:0', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:0', p: {var: 'self'}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:1', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:1', p: {var: 'flag'}},
  {f: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_in:2', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_in:2', p: {var: '<global>:service::build'}}
] AS row
MATCH (a:PyBodyNode {id: row.f})
MATCH (b:PyBodyNode {id: row.t})
MERGE (a)-[r:PY_PARAM_IN]->(b)
SET r += row.p;
UNWIND [
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_out:0', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:0', p: {var: '<return>'}},
  {f: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@formal_out:1', t: 'can://sample-app/sample-app/python/service.py/run(flag)@17:4/actual_out:1', p: {var: 'flag'}},
  {f: 'can://sample-app/sample-app/python/service.py/build(x)@formal_out', t: 'can://sample-app/sample-app/python/service.py/Service/announce(self,flag)@6:8/actual_out', p: {var: '<return>'}}
] AS row
MATCH (a:PyBodyNode {id: row.f})
MATCH (b:PyBodyNode {id: row.t})
MERGE (a)-[r:PY_PARAM_OUT]->(b)
SET r += row.p;
