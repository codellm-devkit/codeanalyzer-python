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

**Scope.** 93 % of the 15,083 wrong class-target edges follow this pattern.

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

`func_expr` is already computed a few lines later (line 598) — merge both blocks to share
the `attr_line`/`attr_col` computation.

**Example.**

| Call | Current | Fixed |
|------|---------|-------|
| `self._buffer.seek(0)` | `ExceptionLogger` | `io.BytesIO.seek` |
| `self.flush()` | `ExceptionLogger` | `ExceptionLogger.flush` |

**Effort:** ~10 lines. **Impact:** removes ~15,083 wrong edges, replaces with correct callable links.

---

### J2. Use `goto()` Instead of `infer()` for Callee Definitions

**Motivation.** `script.infer()` returns the *runtime type* of the expression. `script.goto()`
returns the *definition site* — what we actually want for a call graph.

The difference surfaces for:
- **Decorated functions** — `@property`, `@staticmethod`. `infer()` returns the descriptor;
  `goto()` returns the `def` site.
- **Aliases** — `helper = self._do_work`. `goto()` follows to `_do_work`'s definition.
- **Stub-only packages** — `goto()` goes to the stub `def`; `infer()` returns the return annotation.

**Fix.**

```python
# _infer_callee: replace infer() with goto(), fall back for builtins
definitions = script.goto(line=line, column=column)
if not definitions:
    definitions = script.infer(line=line, column=column)  # fallback for builtins/C-extensions
```

**Effort:** 2 lines. **Impact:** precision improvement for decorated functions and aliases.

---

### J3. Emit All Definitions for Polymorphic Dispatch

**Motivation.** `_infer_callee` currently takes `definitions[0]`, silently dropping overriding
definitions. In a codebase with deep inheritance (Odoo has hundreds of classes overriding
`create`, `write`, `unlink`), this misses all overriding implementations.

**Fix.** Return all definitions and emit one `PyCallsite` per resolved target:

```python
@staticmethod
def _infer_callee_all(script, line, column):
    definitions = script.goto(line=line, column=column) or script.infer(line=line, column=column)
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

---

### C1. Sub-Shard Retry for Timed-Out PyCG Shards

Seven shards exceeded the 300 s budget: `addons/mail`, `addons/mrp`, `addons/account`,
`addons/stock`, `odoo/tools`, `odoo/orm`, `odoo/addons/base`. Their internal topology
is Jedi-only and often orphaned.

**Approach.** When a shard times out, retry at the next directory depth:

```
addons/account/           ← timed out (120 files)
├── models/               ← ~40 files — retry PyCG here
├── report/               ← ~15 files — retry PyCG here
└── wizard/               ← ~20 files — retry PyCG here
```

```python
# In _build_sharded after catching TimeoutError:
sub_shards = _split_to_subdirs(pkg_root, files, project_dir)
if sub_shards and retry_depth > 0:
    for sub_root, sub_files in sub_shards.items():
        retry_queue[sub_root] = sub_files
```

**Example.** `addons/account/report/ReportAccountReport_Invoice._get_report_values` is an
isolated 2-node stub. After retry, PyCG runs on `addons/account/report` (~15 files) and
`_get_report_values` gains outgoing edges that merge it into the main component.

**Effort:** medium. **Impact:** ~160 orphaned nodes reconnected.

---

### C2. String-Referenced Field Methods (`compute=`, `inverse=`, `search=`)

Odoo field declarations name their compute/inverse methods as string constants:

```python
amount_total = fields.Monetary(
    compute='_compute_amount',          # string → no edge ever created
    inverse='_inverse_amount_currency',
)
```

Neither Jedi nor PyCG sees these as call sites. The methods appear as unreachable leaf nodes.

**Approach.** Add an AST post-pass walking `fields.*` constructor calls and extracting
string-valued keyword arguments for `compute`, `inverse`, `search`, `default`:

```python
FIELD_STRING_KWARGS = {"compute", "inverse", "search", "default"}

for kw in field_call.keywords:
    if kw.arg in FIELD_STRING_KWARGS and isinstance(kw.value, ast.Constant):
        emit_edge(f"{cls.signature}.__field_init__", f"{cls.signature}.{kw.value.value}",
                  provenance=["field_ref"])
```

**Effort:** low. **Impact:** hundreds of `compute`/`inverse` edges in ORM-heavy codebases.

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

Jedi already does this rewrite in `_infer_callee` — this pass applies the same treatment
to PyCG-originated edges.

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

P1      J1  Attribute call column fix         — ~10 lines, removes ~37 % wrong Jedi edges
P1      C7  Constructor target rewrite        — ~5 lines post-pass, zero risk

P2      J2  goto() over infer()               — 2 lines, precision improvement
P2      J3  All definitions (polymorphic)     — refactor return type
P2      C2  Field string references           — AST pass, high recall for ORM code
P2      C5  Decorator entry edges             — post-processing on existing decorator data

P3      *   Build model registry              — shared prerequisite for C3, C4, C6
P3      C4  _inherit chain + delegation       — needs model registry
P3      C6  super() MRO resolution            — needs model registry
P3      C3  ORM env[] registry resolution     — needs model registry

P4      J4  Receiver-type fallback            — medium effort, lower marginal gain after J1–J3
P4      C1  Sub-shard retry for timeouts      — pure PyCG infrastructure
P4      J5  Jedi project path config          — config/env change
P4      C8  getattr heuristics                — lower precision, framework-specific
```

Improvements C3, C4, and C6 share the **model registry** (Odoo `_name`/`_inherit` → class
signature map). Build it once in `core.py` and pass it to all three.
