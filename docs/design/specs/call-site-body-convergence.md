# Spec: converge `call_sites[]` into `body{}` — separating the IR from the wire format

Status: draft for review
Date: 2026-08-19
Scope: `codeanalyzer-python` schema v2, `analysis.json` + Neo4j projection
Related: #120 (converge `call_sites[]`/`accessed_symbols[]`/`local_variables[]` with `body{}`)

---

## 1. The finding

Every call site is emitted **twice**, under two unrelated identity schemes.
Verified on `main` (`6f02581`), fixture `return cls()` at line 34:

| representation | id | Neo4j |
| --- | --- | --- |
| `PyCallable.call_sites[]` | `app.py#34:15-34:22` | `:PyCallSite` ← `PY_HAS_CALLSITE` |
| `PyCallable.body{"34:15"}`, `kind:"call"` | `<callable-can-id>@34:15` | `:PyCFGNode` ← `PY_HAS_CFG_NODE` |

One call in source, two graph nodes, no edge between them. `PyCallSite`'s id
(`file#line:col-line:col`) belongs to neither identity tier the schema defines —
it is neither a durable `can://` id nor a `<callable-id>@<local>` ordinal id.

**They cannot disagree in content.** `schema/l1_body.py` derives one from the other
in the same pass:

```python
for cs in c.call_sites or []:
    key = f"{cs.start_line}:{cs.start_column}"
    c.body[key] = BodyNode(kind="call", span=span, callee=None)
```

So this is not a correctness bug. It is redundancy by construction.

## 2. Why it exists

`call_sites[]` is **not** v1 debris left lying around. It is the analyzer's internal
working record, and it is load-bearing for every level above L1:

| reader | uses |
| --- | --- |
| `semantic_analysis/call_graph.py:163-215` | `callee_signature`, `method_name`, `is_constructor_call` — builds the L2 call graph |
| `schema/l2_callees.py` | `callee_signature`, `start_line`, `start_column` — backfills `BodyNode.callee` |
| `dataflow/builder.py:368-420` | `callee_signature`, `is_constructor_call`, position — builds SDG call sites |
| `dataflow/summaries.py`, `dataflow/sdg.py` | consume the above |
| `neo4j/project.py:400` | projects `:PyCallSite` |

`body{}` is the v2 wire view *derived from* that record. The duplication is an
**internal IR leaking into the wire format** — not two competing encodings of equal
standing. That reframing is what makes the fix tractable: the wire format can lose
`call_sites[]` without the internal passes losing anything, provided the record
survives as an internal structure.

## 3. Design

**`body{}` is the single emitted representation of a call site.** Consumers obtain the
call-site set by filtering `body` on `kind == "call"`, which works from **L1** — verified:

```
L1  body{"34:15"}  kind=call  callee=null
L2  body{"34:15"}  kind=call  callee="can://…/@external/app.Account/__init__"
```

The Jedi-produced call record stays **internal**: it is the input to L2 resolution and
L3/L4 dataflow, and is not part of the contract. `PyCallsite` leaves the emitted schema.

### Field disposition

| field | disposition | why |
| --- | --- | --- |
| `start_line` / `start_column` / `end_*` | **becomes the node key + `span`** | already how `body{}` is keyed |
| `callee_signature` | **internal only** | it is the *input* to resolution; `callee` (a resolved `can://` id) is what the wire carries |
| `is_constructor_call` | **internal only** | read by `call_graph.py` + `builder.py`; recoverable on the wire from `callee` resolving to an `__init__` |
| `method_name` | **internal only** | read by `call_graph.py`; recoverable on the wire from `callee` or the `span` slice |
| `argument_types` | **deleted** | deprecated in 0.3.1 (#86) with "will be removed in schema v2"; no internal reader; removal overdue |
| `arguments` | **moves onto the call `BodyNode`** | no internal reader; output-only detail worth keeping. Encoding is OPEN — see § 5 |
| `receiver_expr` | **moves onto the call `BodyNode`** | no internal reader; Jedi inference with no other home |
| `receiver_type` | **moves onto the call `BodyNode`** | as above |
| `return_type` | **moves onto the call `BodyNode`** | as above |

### Resulting `BodyNode`

```python
class BodyNode(BaseModel):
    kind: str                            # statement | call | entry | exit | formal_* | actual_*
    span: Optional[Span] = None
    callee: Optional[str] = None         # call nodes; the sanctioned null→id slot at L2
    of: Optional[str] = None
    parent: Optional[str] = None
    # call-specific, Jedi inference, absent on every other kind
    receiver_expr: Optional[str] = None
    receiver_type: Optional[str] = None
    return_type: Optional[str] = None
```

### Neo4j consequences

- `:PyCallSite`, `PY_HAS_CALLSITE`, and the `file#line:col-line:col` id scheme are **removed**.
- One body-node label, keyed on the global ordinal id, carries every `body{}` entry.
- `PY_RESOLVES_TO` is re-sourced from `BodyNode.callee` instead of `callee_signature`.
- Merge groups drop from 9 to 8.
- **Rename the body label.** `PyCFGNode` names an L3 concept, but a `call` node exists from
  L1 and is deliberately **never** on the CFG spine — verified: at L3 the call `34:15` carries
  `parent="34:8"` and appears in no `cfg` edge, while `@entry`/`34:8`/`@exit` do. The current
  label asserts CFG membership for a node that has none. A level-neutral name (`PyBodyNode`,
  matching what `body{}` is called in the JSON) states what is true at every level.
- If `MATCH (:PyCallSite)` ergonomics are wanted, the writer already supports label layering
  (`rows.py:98` merges on `labels[0]` and unions the rest; `cypher.py:95-97` renders it), so a
  marker label costs no second node and no second id. Note `MARKER_LABELS` in `neo4j/schema.py`
  is declaration-only today — the writer never reads it, and no call site passes >1 label.

## 4. What this does not do

- Does not touch `accessed_symbols[]` or `local_variables[]`, the other two halves of #120.
- Does not change the call graph, Jedi resolution, or any dataflow analysis — only which
  representation is serialized.
- Does not settle the Neo4j merge-label strategy, the `can://` callable-signature grammar, or
  the decorator shape (#128). Those are separate decisions.

## 5. Open questions

- **`arguments` encoding.** Inline objects (`{ast_kind, inferred_type}`, what Python does now
  and what works at L1) or local-ids referencing argument body nodes. The latter requires
  materializing argument nodes at L1, which is a much larger change to the body model and the
  id space. Recommendation: keep inline.
- **Body label name.** `PyBodyNode` is the obvious candidate; anything level-neutral works.
- **Whether the internal record stays a Pydantic model** excluded from serialization, or becomes
  a plain dataclass in the analysis passes. Purely internal; no contract impact.

## 6. Caveats and risks

- **Breaking for anyone reading `call_sites[]`.** That is the point of the change, but it is the
  most visible field in the callable model and its removal should lead the release notes.
- **Three fields become wire-recoverable rather than wire-present** (`method_name`,
  `is_constructor_call`, `callee_signature`). Recovering `method_name` from a `span` slice is
  string work a consumer may not want to do. If that proves unpopular the honest fix is to put
  `method_name` back on the node, not to restore `call_sites[]`.
- **`callee` must actually resolve** for `is_constructor_call` and `method_name` to be
  recoverable. Where resolution fails, `callee` is null and both are lost. The size of that
  set is unmeasured and should be measured before the fields are dropped.
- **Test surface.** Every test asserting on `call_sites[]` changes. They should be rewritten
  against `body{}`, not deleted.
