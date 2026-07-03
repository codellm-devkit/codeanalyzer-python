# Call Graph Improvements

> Analysis based on Odoo 18 — **Level 2, Jedi-shard strategy** (`--analysis-level 2 --pycg-shard
> --pycg-shard-strategy jedi --ray --eager --no-venv`, default ceiling=100, timeout=120).
> Numbers reference `output/odoo/jedi_shard_default/analysis.json`.
>
> Shard plan: **130 shards**, cut_ratio=0.217, max_shard_files=99, oversized=0.

## Baseline (Jedi-shard, post-P0)

| Metric | Value |
|--------|-------|
| Total edges | 173,258 |
| PyCG-only | 128,221 (74.0 %) |
| Jedi-only | 42,635 (24.6 %) |
| Both Jedi + PyCG | 2,402 (1.4 %) |
| Internal → internal | 33,042 (19.1 %) |
| Internal → external | 139,956 (80.8 %) |
| Symbol table modules | 6,086 |
| Internal symbols | 32,532 |
| Orphaned internal nodes | 3,532 |
| Connected components | 165 |

**Comparison vs. old package-shard baseline (753 shards, ceiling=120, 300s timeout):**

| Metric | Old (package-shard) | New (jedi-shard) | Change |
|--------|---------------------|------------------|--------|
| Total edges (pre-P0) | 107,953 | — | — |
| Phantom edges (P0 bug) | 38,326 (35.5 %) | 0 (fixed) | −38,326 |
| int→int edges | 25,941 | 33,042 | **+27.4 %** |
| Connected components (post-P0) | ~207 | 165 | **−20 %** |
| Orphaned internals (post-P0) | ~335 | 3,532 | +3,197 |

The higher orphan count in the jedi-shard run reflects that many Odoo methods are true
singletons (unreachable from a static call graph without dynamic dispatch resolved by C3–C6).
The dramatic reduction in connected components (165 vs 207) shows the jedi-planned sharding
keeps more cross-module call edges intact than the old package-boundary strategy.

The P0 fix (strip shard prefix from non-internal targets) is applied; the old phantom edges
(`addons.foo.odoo.fields.Char`-style) are gone. The improvements below address the residual
precision and coverage gaps.

### Jedi edge accuracy (jedi-shard)

| Category | Jedi edges | % |
|----------|-----------|---|
| → callable (correct) | ~1,800 | ~4.2 % (est.) |
| → class node (wrong) | ~14,800 | ~34.7 % (est.) |
| → Odoo module ghost | ~1,200 | ~2.8 % (est.) |
| → stdlib / third-party external | ~24,200 | ~56.8 % (est.) |
| → unknown ghost | ~635 | ~1.5 % (est.) |
| **Total** | **~42,635** | |

Estimates derived from the same classification rules as the old analysis; exact counts will
shift after J1 (attribute call column fix). **~37 % of Jedi edges still target wrong nodes.**
Fixing Jedi's resolution accuracy (Part I below) is higher leverage than adding new edge
types (Part II).

---

## Part I — Fix Existing Call Resolution (Jedi)

These improvements correct edges Jedi already attempts to produce but gets wrong or drops.

---

### J1. Fix Attribute Call Column Position *(P0 for Jedi)*

**Root cause.** `_call_sites` in `symbol_table_builder.py:591–593` passes `node.col_offset`
to `_infer_callee`. For a bare-name call `foo(arg)` that is correct. For an attribute call
`self.method(arg)`, `node.col_offset` is the column of `self`, not `method`:

```
        self._buffer.write(message)
        ^                            ← node.col_offset = 8  (passed to _infer_callee)
                         ^           ← attr_col = 21        (what we want)
```

Jedi at column 8 infers the receiver — returning the owning class — so `callee_signature`
becomes the class name instead of the method. This produces spurious `Caller → OwningClass`
edges for every `self.method()` call.

**Scope.** The bulk of the ~14,800 wrong class-target edges (the "→ class node (wrong)" row
above) follow this pattern.

**Fix.** In `_call_sites`, split on call type before calling `_infer_callee`:

```python
func_expr = node.func
if isinstance(func_expr, ast.Attribute):
    attr_line = func_expr.end_lineno
    attr_col  = func_expr.end_col_offset - len(func_expr.attr)
    callee_signature, is_constructor = self._infer_callee(script, attr_line, attr_col)
else:
    callee_signature, is_constructor = self._infer_callee(script, node.lineno, node.col_offset)
```

`func_expr = node.func` is **already computed at line 588**, before the current
`_infer_callee` call at 591–593 (not "a few lines later") — so this block only needs to
reuse it, no re-derivation. The `isinstance(func_expr, ast.Attribute)` branch at line 598
that sets `receiver_type` / `method_name` can be merged with it.

Note on cursor position: `end_col_offset - len(attr)` points at the *start* of the
attribute name, which resolves correctly, but Jedi's canonical position is on/at the end of
the identifier. Pointing at `func_expr.end_col_offset` directly is more robust and drops the
`len()` arithmetic. Verified independently: inferring at the callee-*name* position returns
the definition (`function`/`class`), which is why J2's constructor rewrite already works.

**Example.**

| Call | Current | Fixed |
|------|---------|-------|
| `self._buffer.seek(0)` | `ExceptionLogger` | `io.BytesIO.seek` |
| `self.flush()` | `ExceptionLogger` | `ExceptionLogger.flush` |

**Effort:** ~10 lines. **Impact:** removes ~14,800 wrong edges, replaces with correct
callable links.

---

### J2. Improve `infer()` Result Filtering

**Motivation.** `infer()` is the right tool for call graph construction: it answers "what
callable object is invoked here?" rather than "where is this name bound?", which is what
`goto()` answers. The real issue is not *which* API to call but how to handle `infer()`'s
results correctly.

`goto()` is unsuitable for call graphs because it stops at the nearest *binding* site:

| Call pattern | goto() returns | infer() returns |
|---|---|---|
| `handler = process; handler()` | assignment line | `process` definition |
| `from utils import run; run()` | import line | `run` in utils |
| `obj.method()` (after J1 column fix) | needs type inference first | obj type → method def |

The current code already uses `infer()` correctly. The improvements are downstream:

**Fix A — follow aliases.** When `infer()` returns a `Name` whose type is a variable
(not a function/class), follow one level: re-invoke `infer()` on that definition site.
This handles `helper = self._do_work; helper()` correctly.

**Fix B — filter non-callable results.** `infer()` may return module objects, instances,
or decorator wrappers. Filter to `d.type in ("function", "class")` before using
`d.full_name` as the callee. (Dropping `"instance"` also drops callable instances — objects
with `__call__`, `functools.partial` results — which is an acceptable trade-off here but
worth noting; keep `"class"` so the existing constructor→`__init__` rewrite still fires.)

**Fix C — prefer stubs, don't pass `follow_imports`.** To follow stub-only packages (type
stubs without runtime `.py`), use `prefer_stubs=True`. **`infer()` does not accept
`follow_imports`** — that keyword exists only on `goto()`
(`jedi/api/__init__.py`: `infer(self, line, column, *, only_stubs=False, prefer_stubs=False)`),
so passing it raises `TypeError`. `infer()` already resolves through import chains during
type inference.

```python
# _infer_callee: prefer stubs and filter result types
definitions = script.infer(line=line, column=column, prefer_stubs=True)
definitions = [d for d in definitions if d.type in ("function", "class")]
```

**Effort:** 3–5 lines. **Impact:** reduces spurious `None` callee signatures for aliases
and improves resolution through import chains.

---

### J3. Emit All Definitions for Polymorphic Dispatch

**Motivation.** `_infer_callee` currently takes `definitions[0]`, silently dropping overriding
definitions. In a codebase with deep inheritance (Odoo has hundreds of classes overriding
`create`, `write`, `unlink`), this misses all overriding implementations.

**Fix.** Return all definitions and emit one `PyCallsite` per resolved target:

```python
@staticmethod
def _infer_callee_all(script, line, column):
    # Use infer() for consistency with J2 — goto() stops at the binding site
    # (import/assignment line) rather than the callable definition.
    definitions = script.infer(line=line, column=column)
    results = []
    for d in definitions:
        is_class = (d.type == "class")
        full = d.full_name
        if is_class and full:
            full = f"{full}.__init__"
        if full:
            results.append((full, is_class))
    return results if results else [(None, False)]
```

Apply a depth cap: if `len(definitions) > 5`, fall back to `definitions[0]` — an explosion
usually means Jedi has given up and returned all matching names.

**Effort:** refactor return type + loop in caller. **Impact:** moderate — polymorphic dispatch edges.

---

### J4. Receiver-Type Fallback for Unresolved Sites

**Motivation.** Even after J1–J3, some sites return `callee_signature = None`. But
`_call_sites` already captures `receiver_type` (Jedi's short name for the receiver) and
`method_name` (the attribute name). Together these can drive a symbol-table lookup.

**Fix.** After `_infer_callee` returns `None`, fall back:

```python
if callee_signature is None and receiver_type and method_name != "<unknown>":
    callee_signature = _resolve_by_receiver_type(receiver_type, method_name, class_index)
```

Where `_resolve_by_receiver_type` looks up classes by short name and checks for the method:

```python
def _resolve_by_receiver_type(receiver_type, method_name, by_name):
    candidates = by_name.get(receiver_type, [])
    if len(candidates) == 1:
        cls = candidates[0]
        if method_name in cls.methods:
            return f"{cls.signature}.{method_name}"
    return None  # ambiguous — don't guess
```

Only works for unambiguous class names (one class in the symbol table with that name).
Common names like `str`, `dict`, `BaseModel` map to many candidates and are skipped.

**Reuse existing code.** `resolve_unresolved_constructors` (`call_graph.py:191`) already
builds the exact `short_name -> [PyClass]` index this needs, plus a `scope_score` tiebreaker
that approximates LEGB scoping. Implement J4 as an extension of that pass (feeding it
non-constructor sites keyed on `receiver_type` + `method_name`) rather than a standalone
`_resolve_by_receiver_type` with a fresh single-candidate lookup — the scope tiebreaker also
lets it resolve some ambiguous names the sketch above skips.

**Example.**
```
# Jedi returns None; receiver_type = "AccountMove", method_name = "button_cancel"
# Fallback: one AccountMove in symbol table → addons.account.models.account_move.AccountMove.button_cancel
```

**Effort:** medium (needs index build). **Impact:** low–moderate for large codebases.

---

### J5. Jedi Project Path Configuration

**Motivation.** Jedi's resolution quality depends on being able to follow imports. For a
monorepo like Odoo, imports like `from odoo import models` only work if Jedi can find the
`odoo` package. When the analysis virtualenv does not have `odoo` installed, Jedi falls back
to partial resolution — this is the primary reason 1,267 Jedi edges resolve only to
`odoo.fields`, `odoo.tools`, etc. (module-level ghost nodes) rather than specific functions.

**Changes.**

a) Verify virtualenv setup in `Codeanalyzer.__enter__` with a quick Jedi probe:
```python
script = jedi.Script("import odoo; odoo.fields.Char", project=self.jedi_project)
if not script.goto(1, 14):
    logger.warning("Jedi: 'import odoo' does not resolve — cross-package resolution incomplete.")
```

b) Add project root to `added_sys_path` for projects that don't install themselves:
```python
self.jedi_project = jedi.Project(
    path=self.project_dir,
    environment_path=Path(virtualenv) / "bin" / "python",
    added_sys_path=[str(self.project_dir)],
)
```

c) Expose a `--jedi-sys-path` CLI option for non-standard import roots.

**Effort:** low (config change). **Impact:** depends on project; large for monorepos.

---

## Part II — Add Missing Edges (Combined Graph)

These improvements add coverage for call patterns that neither Jedi nor PyCG currently
captures at all.

> **Shared prerequisite (P1).** Every proposal below emits edges tagged with a new
> `provenance` value (`field_ref`, `orm_registry`, `inherit_delegation`, `inherit_super`,
> `framework_trigger`, `super_heuristic`). The schema currently constrains this field:
>
> ```python
> # py_schema.py:358
> provenance: List[Literal["jedi", "pycg", "joern"]] = []
> ```
>
> Constructing a `PyCallEdge` with any other string raises a Pydantic `ValidationError`.
> **Before implementing any Part II item, extend this `Literal`** with the new tags and
> update any serializer/consumer that switches on provenance (Neo4j writer, JSON backend).

> **Framework-specificity.** C2–C6 target Odoo patterns (`fields.*`, `env['model']`,
> `_inherit`, `@api.*`). Implement them as framework profiles gated behind detection (an
> `odoo` import or a `__manifest__.py`) behind a shared plugin seam. The language-level parts
> — Python MRO in C6, C7's constructor rewrite — stay in the core unconditionally. Keep
> provenance tags framework-neutral (`string_ref`, `orm_registry`, `inherit_delegation`,
> `framework_trigger`), not `odoo_*`.

---

### C1. Sub-Shard Retry for Timed-Out PyCG Shards — *done, removed*

Already shipped as *iterative decomposition of runaway shards* in `pycg_analysis.py`
(`_PYCG_DECOMP_FLOOR`, `_PYCG_MAX_DECOMP_ROUNDS`, `_build_sharded`). Label retained so
`C2`–`C8` references stay stable; see the Implementation Order for the one unbuilt refinement
(depth-aligned splitting).

---

### C2. String-Referenced Callables (framework-specific)

Many frameworks reference methods by *string name* instead of calling them, so neither Jedi
nor PyCG ever sees a call site and the referenced method looks like an unreachable leaf. Odoo
field declarations are one instance:

```python
amount_total = fields.Monetary(
    compute='_compute_amount',          # string → no edge ever created
    inverse='_inverse_amount_currency',
)
```

but the pattern is generic — Django (`clean_<field>`, admin `actions`, `ordering`),
marshmallow, Celery task names, signal handlers, pytest fixtures, etc. all do the same.

**Approach.** A config-/plugin-driven rule registry of `(callee-pattern,
method-valued-kwargs)` tuples, with the Odoo rules as one built-in profile gated behind
framework detection (an `odoo` import or a `__manifest__.py`). An AST post-pass matches calls
against registered callee patterns and extracts string-*constant* method-valued kwargs, using
a framework-neutral `string_ref` provenance tag:

```python
# Odoo profile (loaded only when Odoo is detected)
STRING_REF_RULES = [
    # callee-pattern (attr chain suffix), method-valued kwargs
    ("fields.*", {"compute", "inverse", "search"}),
]

for kw in call.keywords:
    if kw.arg in rule.method_kwargs and isinstance(kw.value, ast.Constant) \
            and isinstance(kw.value.value, str):
        emit_edge(f"{cls.signature}.__field_init__", f"{cls.signature}.{kw.value.value}",
                  provenance=["string_ref"])
```

**Note — `default` is not method-valued.** Earlier drafts included `default` in the kwarg
set, but in Odoo a *string* `default=` is a **literal default value** (`default='draft'`),
not a method name — only a callable/lambda default references code. Including it emits edges
to non-existent methods (ghost nodes) or coincidentally-named ones. Restrict to
`compute`/`inverse`/`search`.

**Effort:** low for the Odoo profile; medium once generalized into the rule registry +
framework detection. **Impact:** hundreds of `compute`/`inverse` edges in ORM-heavy
codebases; extensible to other frameworks.

---

### C3. ORM `env['model.name']` Registry Resolution

The dominant cross-model call pattern in Odoo is invisible to static analysis:

```python
moves = self.env['account.move'].search([...])
moves.button_cancel()
```

Jedi infers `self.env['account.move']` as returning `BaseModel`, losing all model-specific
methods. `button_cancel` has no caller from this site.

**Approach.** Two-phase:

**Phase A** — build a model registry from `_name` / `_inherit` declarations:
```python
# For every class: extract _name = 'account.move' → map to class signature
model_registry: dict[str, str] = {}  # 'account.move' → full PyClass.signature
```

**Phase B** — resolve the subscript pattern in `_call_sites`:
```
ast.Call where func.value is ast.Subscript
  and func.value.value is ast.Attribute with attr == 'env'
  and func.value.slice is ast.Constant (string)
```

Emit: `callee_signature = f"{class_sig}.{method_name}"` with `provenance=["orm_registry"]`.

**Example.**
```
# Before: button_cancel has no caller from action_cancel
# After:
addons.account.models.account_payment.AccountPayment.action_cancel
  → addons.account.models.account_move.AccountMove.button_cancel   [orm_registry]
```

**Effort:** high. **Impact:** all cross-model ORM calls.

---

### C4. `_inherit` / `_inherits` Delegation Edges

Odoo's `_inherit` patches an existing model — two Python classes, same Odoo model name,
different files. The `super()` call in the patching class points into the void:

```python
class AccountMove(models.Model):  # addons/account_edi — patches account.move
    _inherit = 'account.move'
    def _post(self):
        super()._post()            # Jedi: callee_signature = None
```

**Approach.**

1. Build an `_inherit` chain from the model registry (shares Phase A from C3).
2. For unresolved `super()` calls, walk the chain to find the next class defining the method.
3. For unoverridden methods, emit delegation edges: `X → child.m` where `child` has no `m`
   → redirect to `parent.m` with `provenance=["inherit_delegation"]`.

**Example.**
```
# Before: super()._post() unresolved
# After:
addons.account_edi.models.account_move.AccountMove._post
  → addons.account.models.account_move.AccountMove._post   [inherit_super]
```

**Effort:** medium. **Impact:** full Odoo class-patch chain resolved.

---

### C5. Decorator-Implied Entry Edges

Odoo triggers compute/constraint/onchange methods from the framework — no static call site
exists. These methods appear as orphaned roots in the graph.

**Approach.** Post-processing pass over `PyCallable.decorators` strings:

```python
TRIGGER_DECORATORS = {
    "api.depends", "api.constrains", "api.onchange",
    "api.model", "api.model_create_multi", "http.route",
}

for callable in iter_callables(symbol_table):
    for dec in callable.decorators:
        if any(t in dec for t in TRIGGER_DECORATORS):
            emit_edge(f"odoo.{t}", callable.signature, provenance=["framework_trigger"])
```

No AST re-parsing needed — `decorators` are already captured as `ast.unparse` strings.

**Example.**
```
# Before: action_open_step_bank_account has 0 in-edges
# After:
odoo.api.model
  → addons.account.models.onboarding_onboarding_step.OnboardingOnboardingStep.action_open_step_bank_account
  [framework_trigger]
```

**Effort:** low. **Impact:** all `@api.*` and `@http.route` decorated methods become reachable.

---

### C6. `super()` MRO Resolution

When Jedi cannot follow `super().method()` (parent in a timed-out shard, or Odoo `_inherit`
chain), call sites have `callee_signature=None` and are dropped.

**Approach.** After `resolve_unresolved_constructors`, add `resolve_super_calls`:

For unresolved call sites where `receiver_expr == "super()"`:
1. Check real Python MRO — find the caller's `PyClass`, search bases in MRO order.
2. Fall back to `_inherit` chain (shares C4 registry).
3. Emit with `provenance=["super_heuristic"]`.

Shares the model registry with C3 and C4 — build once, use in all three.

**Effort:** medium. **Impact:** inheritance call chains that Jedi drops.

---

### C7. Constructor Target Precision: `Class` → `Class.__init__`

PyCG emits constructor edges targeting the class node directly (e.g., `AccountMove`).
The symbol table has `AccountMove` as a `PyClass`, not a `PyCallable`, so inbound edges
pile up on the class node and `__init__` appears unreachable.

**Approach.** Post-processing pass in `filter_external_edges` or `core.py`:

```python
def rewrite_constructor_targets(edges, symbol_table):
    class_sigs = {cls.signature for cls in iter_classes_in_symbol_table(symbol_table)}
    return [
        e.model_copy(update={"target": f"{e.target}.__init__"}) if e.target in class_sigs else e
        for e in edges
    ]
```

Jedi already does this rewrite in `_infer_callee` (`symbol_table_builder.py:94–95`) — this
pass applies the same treatment to PyCG-originated edges. Verified: `filter_external_edges`
(`call_graph.py:262`) adds class signatures to the app-symbol set, so these class-targeted
edges survive filtering but land on non-callable nodes (they become *ghost* nodes in
`to_digraph`, whose node index is callables-only). Caveat: if the class defines no explicit
`__init__`, the rewritten `Class.__init__` target is still a ghost — acceptable, but it means
C7 doesn't fully "eliminate" ambiguity for implicit-constructor classes.

**Before:** `StockPutInPack.action_put_in_pack → StockPutInPack` (class ghost node)
**After:** `StockPutInPack.action_put_in_pack → StockPutInPack.__init__` (callable node)

**Effort:** ~5 lines. **Impact:** eliminates all class-node ambiguity in PyCG edges.

---

### C8. `getattr` and Higher-Order Call Heuristics

Dynamic call patterns invisible to both Jedi and PyCG:

```python
method = getattr(self, '_compute_amount')   # literal — feasible
validator = getattr(self, f'_validate_{t}') # template — unresolvable
handler = functools.partial(self._handle_error, code=400)
```

**Approach (literal `getattr` only).** Extend `_call_sites` to detect:
- `ast.Call` where `func` is `getattr` (name or attribute)
- Second argument is `ast.Constant` with string value

Emit a call site with `method_name = constant`, resolve `receiver_type.method_name` from
the symbol table.

Skip template strings and `functools.partial` for the initial scope — false positives
outweigh gains. Improvements C3 and C4 cover the dominant dynamic patterns in Odoo.

**Effort:** high. **Impact:** lower priority; most dynamic dispatch in framework internals.

---

## Implementation Order

```
P0  ✅  PyCG shard prefix canonicalization    (done — pycg_analysis.py)
P0  ✅  Shard planner ZeroDivisionError guard  (done — shard_planner.py)
--  ✅  C1  Sub-shard retry for timeouts      (done — iterative decomposition, pycg_analysis.py;
                                              optional: depth-aligned vs budget-halving split — measure first)

P1      J1  Attribute call column fix         — ~10 lines, removes ~37 % wrong Jedi edges
P1      C7  Constructor target rewrite        — ~5 lines post-pass, zero risk
P1      *   Extend provenance Literal         — prerequisite for ALL Part II edges (C2–C8)

P2      J2  infer() result filtering          — precision (alias/type filter, prefer_stubs)
P2      J3  All definitions (polymorphic)     — refactor return type; use infer(), not goto()
P2      C2  Field string references           — AST pass, high recall for ORM code
P2      C5  Decorator entry edges             — post-processing on existing decorator data

P3      *   Build model registry              — shared prerequisite for C3, C4, C6
P3      C4  _inherit chain + delegation       — needs model registry
P3      C6  super() MRO resolution            — needs model registry
P3      C3  ORM env[] registry resolution     — needs model registry

P4      J4  Receiver-type fallback            — extend resolve_unresolved_constructors; low marginal gain after J1–J3
P4      J5  Jedi project path config          — config/env change
P4      C8  getattr heuristics                — lower precision, framework-specific
```

Improvements C3, C4, and C6 share the **model registry** (Odoo `_name`/`_inherit` → class
signature map). Build it once in `core.py` and pass it to all three.
