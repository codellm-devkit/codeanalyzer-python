# The analysis catalogue (Cypher)

Every recipe names its minimum `-a`. Bound every transitive walk (trap #1).

## 1. Structure & inventory (L1)

```cypher
// modules with their classes and callables, sizes
MATCH (m:PyModule)
OPTIONAL MATCH (m)-[:PY_DECLARES]->(k:PyClass)
OPTIONAL MATCH (m)-[:PY_DECLARES]->(f:PyCallable)
RETURN m.file_key, count(DISTINCT k) AS classes, count(DISTINCT f) AS functions, m.file_size
ORDER BY m.file_size DESC LIMIT 25

// a callable's exact source text and location
MATCH (c:PyCallable {name: "process_payment"})
RETURN c.path, c.code_start_line, c.end_line, c.code
```

## 2. Call graph (L2)

```cypher
// direct callers / callees
MATCH (caller:PyCallable)-[:PY_CALLS]->(t:PyCallable {name: "authorize"}) RETURN caller.id
MATCH (t:PyCallable {name: "authorize"})-[:PY_CALLS]->(callee) RETURN labels(callee), callee.id

// bounded transitive reachability (who can reach the DB layer?)
MATCH (c:PyCallable)-[:PY_CALLS*1..8]->(t:PyCallable)
WHERE t._module STARTS WITH "myapp.db"
RETURN DISTINCT c.id

// fan-in / fan-out hotspots
MATCH (c:PyCallable)
OPTIONAL MATCH (c)<-[i:PY_CALLS]-() WITH c, count(i) AS fan_in
OPTIONAL MATCH (c)-[o:PY_CALLS]->() RETURN c.id, fan_in, count(o) AS fan_out
ORDER BY fan_in + count(o) DESC LIMIT 20

// provenance split: edges only one resolver found
MATCH ()-[e:PY_CALLS]->() WHERE e.prov = ["defuse"] RETURN count(e)

// per-callsite resolution (which statement calls what)
MATCH (c:PyCallable)-[:PY_HAS_BODY_NODE]->(s:PyBodyNode {kind: "call"})-[:PY_RESOLVES_TO]->(t)
RETURN c.id, s.start_line, labels(t), t.id LIMIT 50
```

**Dead code** — callables no entrypoint reaches (L2; treat with judgment —
dynamic dispatch, framework callbacks, and reflection are invisible):

```cypher
MATCH (c:PyCallable) WHERE NOT c.is_entrypoint
  AND NOT EXISTS {
    MATCH (e:PyCallable {is_entrypoint: true})-[:PY_CALLS*1..12]->(c)
  }
RETURN c.id, c.path, c.code_start_line
```

**Recursion cycles** (L2):

```cypher
// self-recursion
MATCH (c:PyCallable)-[:PY_CALLS]->(c) RETURN c.id
// mutual recursion up to length 6, one row per cycle instance
MATCH p = (c:PyCallable)-[:PY_CALLS*2..6]->(c)
WHERE ALL(n IN nodes(p)[1..] WHERE n.id >= c.id)   // canonical start, dedups rotations
RETURN [n IN nodes(p) | n.id] AS cycle LIMIT 50
```

## 3. Entrypoints (L1)

```cypher
MATCH (c:PyCallable {is_entrypoint: true})
OPTIONAL MATCH (c)-[r:PY_DECORATED_BY]->(d:PyDecorator)
RETURN c.id, c.entrypoint_frameworks,
       collect({decorator: d.qualified_name, expression: r.expression}) AS evidence
// class-based entrypoints: same properties on :PyClass
// entrypoint surface per framework
MATCH (c:PyCallable {is_entrypoint: true})
UNWIND c.entrypoint_frameworks AS fw RETURN fw, count(*) ORDER BY count(*) DESC
```

## 4. Inheritance (L1)

```cypher
// hierarchy under a base (bounded)
MATCH (base:PyClass {name: "Model"})<-[:PY_EXTENDS*1..6]-(sub:PyClass) RETURN sub.signature
// overrides: subclass redefines a superclass method
MATCH (sub:PyClass)-[:PY_EXTENDS]->(sup:PyClass),
      (sub)-[:PY_HAS_METHOD]->(m:PyCallable),
      (sup)-[:PY_HAS_METHOD]->(base:PyCallable {name: m.name})
RETURN sub.signature, m.name, base.id AS overrides
// external bases (framework classes)
MATCH (k:PyClass)-[:PY_EXTENDS]->(x:PyExternal) RETURN x.module, x.name, count(k) ORDER BY count(k) DESC
```

## 5. Import graph (L1)

```cypher
// module dependency edges with spellings
MATCH (m:PyModule)-[i:PY_IMPORTS]->(t) RETURN m.file_key, labels(t), i.imported_names LIMIT 50
// import cycles between modules (bounded)
MATCH p = (m:PyModule)-[:PY_IMPORTS*2..6]->(m)
WHERE ALL(n IN nodes(p)[1..] WHERE n.id >= m.id)
RETURN [n IN nodes(p) | n.file_key] AS cycle LIMIT 25
```

## 6. Control flow & control dependence (L3)

```cypher
// a callable's CFG in order
MATCH (c:PyCallable {name: "reconcile"})-[:PY_HAS_BODY_NODE]->(s:PyBodyNode)
OPTIONAL MATCH (s)-[n:PY_CFG_NEXT]->(t:PyBodyNode)
RETURN s.id, s.kind, s.start_line, collect({to: t.id, kind: n.kind}) ORDER BY s.start_line

// unreachable statements (no CFG path from @entry)
MATCH (c:PyCallable)-[:PY_HAS_BODY_NODE]->(entry:PyBodyNode {kind: "entry"})
MATCH (c)-[:PY_HAS_BODY_NODE]->(s:PyBodyNode)
WHERE s.kind IN ["statement","branch","loop","return","raise","call"]
  AND NOT EXISTS { MATCH (entry)-[:PY_CFG_NEXT*1..64]->(s) }
RETURN c.id, s.id, s.start_line

// which condition guards this statement (control dependence)
MATCH (s:PyBodyNode {id: $stmt})<-[:PY_CDG]-(guard:PyBodyNode) RETURN guard.id, guard.kind, guard.start_line

// complexity hotspots (precomputed)
MATCH (c:PyCallable) RETURN c.id, c.cyclomatic_complexity ORDER BY c.cyclomatic_complexity DESC LIMIT 20
```

## 7. Data dependence (L3; alias-widened at L4)

```cypher
// def-use chain of one variable inside a callable
MATCH (c:PyCallable {name: "apply_discount"})-[:PY_HAS_BODY_NODE]->(a:PyBodyNode)
MATCH (a)-[d:PY_DDG {var: "total"}]->(b:PyBodyNode)
RETURN a.start_line, b.start_line, d.prov

// syntactic-only view (drop alias-derived edges)
MATCH (a)-[d:PY_DDG]->(b) WHERE "ssa" IN d.prov RETURN count(d)

// statements influencing a return value
MATCH (r:PyBodyNode {kind: "return"})<-[:PY_DDG*1..10]-(src:PyBodyNode)
WHERE r.id STARTS WITH $callable_id
RETURN DISTINCT src.id, src.start_line
```

## 8. Slicing (L3 intra; L4 interprocedural)

```cypher
// backward slice from a statement (intra)
MATCH (s:PyBodyNode {id: $global_id})<-[:PY_DDG|PY_CDG*1..10]-(dep:PyBodyNode)
RETURN DISTINCT dep.id, dep.start_line
// forward slice: reverse the arrow
// interprocedural: add PY_PARAM_IN|PY_PARAM_OUT|PY_SUMMARY to the union (L4), keep the bound
```

## 9. Taint-style reachability (L4)

```cypher
// everything entrypoint parameters can influence
MATCH (e:PyCallable {is_entrypoint: true})-[:PY_HAS_BODY_NODE]->(src:PyBodyNode {kind: "formal_in"})
MATCH (src)-[:PY_DDG|PY_PARAM_IN|PY_PARAM_OUT|PY_SUMMARY*1..12]->(s:PyBodyNode)
WHERE s.kind IN ["statement","branch","loop","return","raise","call"]
RETURN DISTINCT e.id, src.var, s.id

// source→sink existence with witness path (shortestPath terminates where enumeration cannot)
MATCH (src:PyBodyNode {kind: "formal_in"})<-[:PY_HAS_BODY_NODE]-(e:PyCallable {is_entrypoint: true})
MATCH (sink:PyBodyNode {kind: "call"})-[:PY_RESOLVES_TO]->(x:PyExternal) WHERE x.module = "subprocess"
MATCH p = shortestPath((src)-[:PY_DDG|PY_PARAM_IN|PY_PARAM_OUT|PY_SUMMARY*..40]->(sink))
RETURN e.id, x.name, [n IN nodes(p) | n.id] AS witness
```

## 10. Exit points (L2 externals; L3 returns/writes; L4 alias-complete)

```cypher
MATCH (k:PyClass)-[:PY_HAS_METHOD]->(m:PyCallable)
WHERE k.signature ENDS WITH ".Billing"    // signatures are module-path derived (src. prefixes exist)
OPTIONAL MATCH (m)-[:PY_HAS_BODY_NODE]->(ret:PyBodyNode {kind: "return"})
OPTIONAL MATCH (m)-[:PY_CALLS]->(x:PyExternal)
OPTIONAL MATCH (m)-[:PY_HAS_BODY_NODE]->(:PyBodyNode)-[d:PY_DDG]->() WHERE d.var STARTS WITH "self."
RETURN m.id, count(DISTINCT ret) AS returns,
       collect(DISTINCT x.module + "." + x.name) AS external_calls,
       collect(DISTINCT d.var) AS caller_visible_writes
```

## 11. Dependencies, SBOM & configuration (L1)

```cypher
// SBOM: every declared package, spec, pin, and declaring manifest
MATCH (f:Artifact)-[d:DECLARES_DEPENDENCY]->(p:Package)
OPTIONAL MATCH (lf:Artifact)-[l:LOCKS]->(p)
RETURN p.name, d.kind, d.spec, l.version AS locked, f.path AS declared_in, d.prov
ORDER BY p.name

// undeclared imports (dependency hygiene)
MATCH (a:PyApplication)-[u:PY_UNRESOLVED_IMPORT]->(e:PyExternal)
RETURN e.module, u.prov

// which callables reach code from a package (dependency blast radius)
MATCH (p:Package {id: "pkg:pypi/requests"})-[:PY_PROVIDES]->(g:PyExternal)
MATCH (x:PyExternal) WHERE x.module = g.module        // member-level ghosts share `module`
MATCH (c:PyCallable)-[:PY_CALLS*1..6]->(x)
RETURN DISTINCT c.id

// declared but never imported (candidate dead dependency; heuristic — extras, plugins invisible)
MATCH (:Artifact)-[:DECLARES_DEPENDENCY]->(p:Package)
WHERE NOT (p)-[:PY_PROVIDES]->() RETURN p.name

// spec-vs-lock drift
MATCH (f:Artifact)-[d:DECLARES_DEPENDENCY]->(p:Package)<-[l:LOCKS]-(:Artifact)
WHERE d.spec <> "" AND NOT l.version STARTS WITH replace(split(d.spec, ",")[0], "==", "")
RETURN p.name, d.spec, l.version

// configuration inventory + raw content (grep configs in Cypher)
MATCH (:PyApplication)-[:HAS_ARTIFACT]->(f:Artifact)
WHERE any(r IN f.roles WHERE r IN ["service-topology", "container-image"])
RETURN f.path, f.format, f.source
MATCH (f:Artifact) WHERE f.source CONTAINS "POSTGRES_PASSWORD" RETURN f.path

// cross-language SBOM join point: purl ids are shared, e.g.
MATCH (p:Package) WHERE p.id STARTS WITH "pkg:" RETURN p.ecosystem, count(*)
```

## 12. Health metrics (any level)

```cypher
// external surface per module
MATCH (m:PyModule)-[:PY_DECLARES]->(:PyCallable)-[:PY_CALLS]->(x:PyExternal)
RETURN m.file_key, count(DISTINCT x.module) AS external_modules ORDER BY external_modules DESC LIMIT 20
// orphan ghosts (referenced by nothing after filtering)
MATCH (x:PyExternal) WHERE NOT ()-[]->(x) RETURN count(x)
// biggest callables by span
MATCH (c:PyCallable) RETURN c.id, c.end_line - c.code_start_line AS lines ORDER BY lines DESC LIMIT 20
```
