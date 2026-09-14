# Co-positioned call sites: one body node per `ast.Call`

**Date:** 2026-09-14
**Status:** Approved (design dialogue in-session)
**Scope:** codeanalyzer-python; LOCAL ordinal id grammar, L1 `body`, and the Neo4j
call-site join. Schema v2, no version move; closes #215
**Builds on:** call-site/body convergence (`call-site-body-convergence.md`, #120);
JSON == Graph parity (`2026-09-10-neo4j-json-parity.md`, #202/#203)
**Sibling status:** codeanalyzer-typescript already ships this spelling
(`src/schema/l1Body.ts`, `callBodyKeys`); codeanalyzer-java is structurally immune —
see *Cross-language position* below

## Problem

`emit_l1_body` keys a callable's `body` dict on the call site's start position
(`codeanalyzer/schema/l1_body.py:9`):

```python
key = f"{cs.start_line}:{cs.start_column}"
```

Two nested `ast.Call` nodes can begin at the same position. `getattr(self, x)(y)` is
that shape exactly: the outer application and the inner `getattr` both start at the
`g`. `_iter_calls_in_scope` yields both (`symbol_table_builder.py:693`), so
`call_sites` carries both, and the dict then keeps one — the later write, which is
the inner `getattr`. The dynamic invocation is destroyed.

`IdentityMap.for_function` uses the same key format (`dataflow/identity.py:36`), so
L3 and L4 inherit the collision, and the Neo4j projection iterates `c.body`
(`neo4j/project.py:194`) so it emits one `:PyBodyNode` for the position — resolving
to `builtins.getattr`. Measured on a merged Odoo graph: 193 `getattr` call sites,
all 193 carrying a `PY_RESOLVES_TO` edge to the builtin, and no node anywhere for
the invocation that follows. `getattr(self, x)` and `getattr(self, x)(y)` are
indistinguishable in the graph. One reads an attribute; the other performs a
dynamic call.

A second defect sits at the same site. `_callee_anchor` returns
`node.lineno, node.col_offset` whenever the callee is not an `ast.Attribute`
(`symbol_table_builder.py:147`). The outer call's `func` is an `ast.Call`, so the
anchor lands on `getattr` and Jedi infers `builtins.getattr`: the outer call is
labelled as a call to `getattr`, when it calls whatever `getattr` returned.

## Contract-impact triage

| Question | Answer |
| --- | --- |
| Changes schema v2 output? | **Yes.** The LOCAL ordinal id grammar gains a disambiguator, and `body` gains a node that has never existed — which flows into `cfg`/`cdg`/`ddg` endpoints and the Neo4j `PyBodyNode` merge keys. |
| Analyzers affected | `codeanalyzer-python` only. `codeanalyzer-typescript` already emits the target grammar; `codeanalyzer-java` cannot hit the collision. |
| SDKs affected | `python-sdk`: **verified, no code change** — see *Consumer impact*. |
| Docs affected | This repo's `CLAUDE.md` identity section and `.claude/SCHEMA_DECISIONS.md`. |
| Schema version | Stays `2.0.0`. The 2.0.0 line has not left RC, and the payload shape (fields, node kinds, edge kinds) does not move — only the key space below the callable. |

## Cross-language position

The three analyzers anchor a call node's local key differently, and it is worth
stating plainly because it looks like drift and is not:

- **python** (`l1_body.py`) and **typescript** (`l1Body.ts`) key a call at the **call
  expression's start**. Nested calls that begin at the same column therefore collide,
  which is the bug this spec fixes.
- **java** (`BodyNodeBuilder.anchorOfStatement`) keys a call at the **invoked name
  token** — `MethodCallExpr.getName()` for a call, the type for a `new`. `a.b().c()`
  yields two distinct anchors, so java has no collision to disambiguate.

Re-anchoring python onto the java rule was considered and rejected: it would move
**every** existing call key, and it does not even solve the motivating case
(`getattr(self, x)(y)`'s outer callee is a `Call`, which has no name token, so the
anchor falls back to the expression start and the collision returns).

## Locked decisions

1. **The disambiguator is adopted verbatim from codeanalyzer-typescript:
   `line:col`, then `/2`, `/3`, … for each subsequent call site sharing a start
   position.** `callBodyKeys` (`src/schema/l1Body.ts:24`) already ships exactly this,
   with the comment "disambiguated `/2`, `/3`, … when chained calls share a start
   position". Under the parity clause a term coined twice is permanently wrong, so
   python ports the spelling rather than inventing `#1` or an end-position key. The
   `/` delimiter is already in the grammar (`<callsite-local>/actual_in:0`), and the
   two never collide: a param-vertex segment always begins `actual_`.

2. **The bare `line:col` goes to the first call site recorded, which is the
   outermost call.** `_iter_calls_in_scope` yields a `Call` before descending into it
   (`symbol_table_builder.py:711`), exactly as the TypeScript walker calls
   `h.onCall(node)` before `forEachChild` (`builders.ts:454`). Pre-order is
   deterministic on unchanged source, so no separate "rank by width" rule is needed —
   but the implementation asserts the property (widest span first among
   co-positioned siblings) rather than leaving it implicit in traversal order.

   This is the one meaning change: for a colliding position, `11:18` today resolves
   to the inner `getattr` and afterwards resolves to the outer invocation. Keys that
   do not collide are untouched, which is the great majority of the corpus.

3. **`IdentityMap` needs no new format.** CFG nodes are statements, one per position,
   so `for_function` keeps minting `line:col`. The existing L1/L3 merge
   (`dataflow/builder.py:288`) therefore lands the CFG statement on the **outer**
   call's key, which is the correct pairing — the statement and the outermost call
   are the same region of code. The inner call keeps its own `/2` node with no CFG
   contact, reached through the `parent` anchoring #115 already built.

4. **The graph joins `callee_signature` per call site, not per position.**
   `sig_by_pos` (`neo4j/project.py:186`) is keyed `(start_line, start_column)` and
   silently keeps one of two co-positioned sites. It becomes a per-body-key map built
   from the same key sequence L1 used, so each `:PyBodyNode` gets its own call site's
   signature or none.

5. **`_callee_anchor` gives a call-of-call no callee.** When `node.func` is itself an
   `ast.Call`, the call site carries `callee_signature=None` and `method_name`
   stays `<unknown>`, so the site is identifiable as a dynamic invocation instead of
   masquerading as a call to the inner callee. No `PY_RESOLVES_TO` edge is emitted
   for it.

6. **Nothing resolves the dynamic target.** No string-value analysis over
   `'_index_%s' % ftype`, no new `PY_CALLS` edge to `_index_pdf`, no inference from
   the receiver. Recording *that* a dynamic call happens is the fact being restored;
   guessing *where it goes* is a different, unsound change.

## What the payload looks like after

For the reproducer in #215 (`buf = getattr(self, '_index_%s' % ftype)(bin_data)` on
line 11, column 18):

```
body (2 keys):
  '11:18'    kind='call'  method_name='<unknown>'  callee=None  callee_signature=None
  '11:18/2'  kind='call'  method_name='getattr'    callee=None  callee_signature='builtins.getattr'
```

At L3 the enclosing statement merges onto `11:18`; at L4 any actual vertices for the
outer call parent as `11:18/actual_in:0`. On the graph, both nodes exist as
`:PyBodyNode` under `<callable-id>@11:18` and `<callable-id>@11:18/2`, and only the
second carries a `PY_RESOLVES_TO` edge to `builtins.getattr`.

`f()()()` yields three keys — `L:C`, `L:C/2`, `L:C/3` — outermost first, stable
across runs.

## Consumer impact

- **python-sdk: no change required, verified against the source.**
  `body_key_column` (`cldk/analysis/commons/keys.py:183`) does
  `key.split("/", 1)[0].partition(":")`, so `"11:18/2"` already yields column `18` —
  the same value the suffix-free key yields, which is what its tie-break wants. The
  rank tuples in `codeanalyzer.py:380` and `neo4j_backend.py:2191` are
  `(line width, -column, key)`, so two co-positioned nodes tie on the first two
  components and break on the key string, putting `"11:18"` (the outer call) ahead of
  `"11:18/2"`. That is the right order for a "which node is this position" query.
- **Existing graphs do not gain the node.** The fix applies to new runs; a consumer
  reading an older graph still sees one node per position and must tolerate its
  absence.
- **Anything that treated a body key as `line:col` verbatim** (splitting on `:` and
  taking two fields) now sees a third form. The SDK's helper is the reference parser;
  it already handles it.

## Test plan (the issue's DoD, made executable)

- The #215 reproducer yields exactly the two keys above; the test names the expected
  key set, not a count.
- The invocation node has `method_name='<unknown>'`, `callee=None`,
  `callee_signature=None`, and no `PY_RESOLVES_TO` edge; the `getattr` node keeps
  `method_name='getattr'` and `callee_signature='builtins.getattr'`.
- Every body key in the existing fixtures is unchanged for callables with no
  co-positioned calls — asserted against the current key sets, not their sizes.
- `IdentityMap.global_id` and the Neo4j `_global_ordinal` produce the same string for
  both new nodes.
- `f()()()` yields three distinct keys, and two runs on one source yield the same
  three.
- The five analyzer gates, plus the regenerated graph schema snapshot if any declared
  property moves.

## Release plan

One PR on `codeanalyzer-python` closing #215, carried by the **next patch train**,
alongside the already-merged #207 fix (`5f5734f`). Analyzer-only: no SDK pin moves,
no lockstep with another repo, and `schema_version` does not change. Docs land in the
same PR — the identity paragraph in `CLAUDE.md` and an entry in
`.claude/SCHEMA_DECISIONS.md` recording that a local id may carry a `/N`
disambiguator and why the outermost call holds the bare key.

## Deferred, deliberately

- **Argument text.** `arguments_json` keeps `value` only for a JSON-safe `Constant`
  and `name` only for a bare `Name` (`symbol_table_builder.py:786`), so
  `'_index_%s' % ftype` still reaches a consumer as
  `{"ast_kind": "BinOp", "name": null, "value": null}`. Carrying `ast.unparse(arg)`
  for other shapes is its own issue.
- **Dynamic target resolution** — see locked decision 6.
- **Sibling work.** None falls out: typescript already emits this grammar, java
  cannot collide.
