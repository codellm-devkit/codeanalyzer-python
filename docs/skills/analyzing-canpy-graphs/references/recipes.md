# Query recipes

Every recipe states its minimum `-a` level. Cypher assumes the Neo4j projection
(`--emit neo4j`); the JSON variant reads `analysis.json`.

## Entrypoints (L1+)

```cypher
MATCH (c:PyCallable {is_entrypoint: true})
RETURN c.id, c.entrypoint_frameworks
// class-based entrypoints (Django CBVs etc.): same two properties on :PyClass
```

Decorator evidence: `(c)-[r:PY_DECORATED_BY]->(d:PyDecorator)` — `r.expression`
carries the route string. JSON: walk callables, keep `is_entrypoint`; coverage and
failures in `application.entrypoint_report`.

## Call graph (L2+)

```cypher
// direct callers
MATCH (c:PyCallable)-[:PY_CALLS]->(t:PyCallable {name: "process_payment"}) RETURN c.id
// everything reaching a library (bounded transitive)
MATCH (c:PyCallable)-[:PY_CALLS*1..6]->(e:PyExternal) WHERE e.module = "subprocess"
RETURN DISTINCT c.id
```

JSON: `application.call_graph` (`src`/`dst`/`prov`/`weight`); externals resolve in
`application.external_symbols`.

## Taint-style reachability (L4)

Sources: `formal_in` vertices of entrypoint callables. Propagation:
`PY_DDG ∪ PY_PARAM_IN ∪ PY_PARAM_OUT ∪ PY_SUMMARY` (all forward). Sinks: your
choice — e.g. call nodes resolving to a dangerous external.

**Always bound the walk** — unbounded `*1..` enumerates paths and dies on real
corpora. Two safe shapes:

```cypher
// bounded depth, distinct targets
MATCH (e:PyCallable {is_entrypoint: true})-[:PY_HAS_BODY_NODE]->(src:PyBodyNode {kind: "formal_in"})
MATCH (src)-[:PY_DDG|PY_PARAM_IN|PY_PARAM_OUT|PY_SUMMARY*1..12]->(s:PyBodyNode)
WHERE s.kind IN ["statement","branch","loop","return","raise","call"]
RETURN DISTINCT e.id, src.var, s.id

// source-to-sink existence: shortestPath terminates where enumeration cannot
MATCH (src:PyBodyNode {kind: "formal_in"})<-[:PY_HAS_BODY_NODE]-(e:PyCallable {is_entrypoint: true})
MATCH (sink:PyBodyNode {kind: "call"})-[:PY_RESOLVES_TO]->(x:PyExternal)
WHERE x.module = "subprocess"
MATCH p = shortestPath((src)-[:PY_DDG|PY_PARAM_IN|PY_PARAM_OUT|PY_SUMMARY*..40]->(sink))
RETURN e.id, x.name, [n IN nodes(p) | n.id] AS witness
```

Syntactic-only variant: filter each DDG hop on `"ssa" IN r.prov` (drops the
alias-widened `points-to` edges). JSON variant: BFS over per-callable `ddg` +
application `param_in`/`param_out`, translating LOCAL ↔ GLOBAL ids via
`"<callable-id>@<local>"`; keep a visited set — this is a graph, not a tree.

## Exit points of a class, method granularity (L3; alias-complete at L4)

```cypher
MATCH (k:PyClass)-[:PY_HAS_METHOD]->(m:PyCallable)
WHERE k.signature ENDS WITH ".Billing"   // signatures are module-path-derived; src/ layouts carry a src. prefix
OPTIONAL MATCH (m)-[:PY_HAS_BODY_NODE]->(ret:PyBodyNode {kind: "return"})
OPTIONAL MATCH (m)-[:PY_CALLS]->(x:PyExternal)
OPTIONAL MATCH (m)-[:PY_HAS_BODY_NODE]->(:PyBodyNode)-[d:PY_DDG]->() WHERE d.var STARTS WITH "self."
RETURN m.id,
       count(DISTINCT ret) AS return_sites,
       collect(DISTINCT x.module + "." + x.name) AS external_calls,
       collect(DISTINCT d.var) AS caller_visible_writes
```

Channel/level map: external_calls L2+; return_sites and writes L3+; L4 widens
writes with `points-to` ddg and adds `@formal_out` vertices + `summary` edges
(the transitive in→out relation per callsite).

JSON walk (statement granularity, exact text):

```python
mod = app["symbol_table"]["myapp/services.py"]
cls = mod["types"]["myapp.services.Billing"]        # dotted-signature key!
for name, m in cls["callables"].items():            # bare-name key
    returns   = [nid for nid, n in m["body"].items() if n["kind"] == "return"]
    externals = [n for n in m["body"].values()
                 if n["kind"] == "call" and (n.get("callee") or "") in app["external_symbols"]]
    writes    = sorted({e["var"] for e in m.get("ddg") or [] if e["var"].startswith("self.")})
    lo, hi = m["body"][returns[0]]["span"]["bytes"]
    text = mod["source"].encode("utf-8")[lo:hi].decode("utf-8")   # bytes, not str slice
```

## Backward slice from a statement (L3+)

```cypher
MATCH (s:PyBodyNode {id: $global_id})<-[:PY_DDG|PY_CDG*1..10]-(dep:PyBodyNode)
RETURN DISTINCT dep.id, dep.start_line
```

Interprocedural slice (L4): add `PY_PARAM_IN|PY_PARAM_OUT|PY_SUMMARY` to the
union, keep the bound.
