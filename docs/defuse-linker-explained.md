# How we build the call graph — a plain-language tour

A **call graph** answers one question about a codebase: *who calls whom?*
Every arrow `A → B` means "somewhere inside function A, function B gets
called." It is the backbone for everything downstream — impact analysis
("what breaks if I change this?"), security tracing, dead-code hunting.

The hard part in Python is that the language fights you. Functions get
passed around like values, wrapped in decorators, stored in dicts, glued
onto classes at runtime. A naive reader misses most of it.

Our analyzer builds the graph in two layers.

## Layer 1: Jedi, the IDE brain

[Jedi](https://github.com/davidhalter/jedi) is the library that powers
autocomplete in many Python editors. When your editor knows that typing
`session.` should offer `get`, that is Jedi resolving what `session` is
and what methods it has.

We run Jedi over every file and ask, for every call it can see: *what
exactly is being called?* When it knows, we get a precise arrow —
`Session.request → PreparedRequest.prepare`. This is the **base graph**:
fast (a fraction of a second once files are parsed) and rarely wrong.

But Jedi is cautious by design. It shrugs at anything dynamic:

```python
f = handler
f(x)              # Jedi: "no idea what f is"

@lru_cache
def lookup(...):  # Jedi: "that's a... functools._lru_cache_wrapper?"
```

Every shrug is a missing arrow. On real code that is a lot of arrows.

## Layer 2: the defuse linker, the detective

For every call Jedi could not resolve, a second pass reads the code the
way a person would. "Defuse" = *definitions and uses*: find where the
name being called was **defined**, by walking backwards from where it is
**used**. It climbs a ladder of tricks, cheapest first:

1. **Follow the name.** `f = handler; f(x)` — walk the assignment chain
   back to `handler`. This respects Python's real scoping rules:
   parameters shadow outer names, class bodies are invisible to methods,
   inner functions see enclosing ones.
2. **Follow the import.** `from tools import parse` — jump into
   `tools.py` (even through relative imports) and point the arrow at the
   real `parse`.
3. **Climb the class tree.** `self.save()` — look on the class, then its
   parents, then mixin-style siblings (if a mixin calls `self.send()`
   and exactly one subclass defines `send`, that's the target), then
   imported base classes in other libraries.
4. **Know the builtins.** `super()`, `len()`, `sorted()` — if nothing in
   scope claims the name and it is a Python builtin, say so.
5. **Catch import-time work.** Code at the top of a module *runs* when
   the module loads — `logging.getLogger(__name__)`, decorators being
   applied. Those are real calls; we emit them, attributed to the module.
6. **Catch the calls Python makes for you.** An f-string `f"{x!r}"`
   secretly calls `repr`. A `for` loop secretly calls `__iter__`.
   `TypeError(msg).with_traceback(tb)` calls a method on a temporary.
   We emit those too, because runtime does.
7. **Do light type detective work.** One bounded round, no guessing
   loops:
   - `w = Widget()` → later `w.tick()` is `Widget.tick`;
   - if every caller passes a `CookieJar` as the second argument, the
     second parameter *is* a `CookieJar` (call sites "vote");
   - if a function only ever `return Widget()`, whatever holds its
     result is a `Widget`;
   - `self.jar = CookieJar()` in `__init__` types `self.jar` everywhere.
8. **Last resort: the phone book.** If the receiver is truly unknowable
   (`thing.write(...)` where nothing reveals `thing`), list every
   internal method named `write` as a *possible* target. Over-broad, but
   honest — "one of these" — and it only fires after every smarter tier
   has failed. (This is what Joern does for *all* untyped calls; we do
   it only for the leftovers.)

Every arrow carries a tag (`prov`) saying which layer produced it —
`jedi`, `defuse`, or both — so a consumer can always tell precise
resolution from careful reading from the phone book.

One deliberate rule: the linker never writes its answers back into the
cached analysis. Answers are returned separately, so a cached rerun can
never mislabel a linker arrow as a Jedi arrow.

## The showdown: Joern and Fraunhofer

Two respected open-source code-analysis platforms build Python call
graphs the same "fast base + CPG backfill" way: **Joern** and
**Fraunhofer AISEC's CPG**. We used them as the measuring stick — not by
reading their docs, but by *running them on the same code and diffing
the actual edges*, then chasing **every single edge they had and we
lacked** to its root cause. Each chase ended one of two ways: we fixed
something, or we proved their edge doesn't correspond to anything real.

That loop ran until we were a superset of everything real:

| corpus | Joern (their real edges) | Fraunhofer | us |
| --- | --- | --- | --- |
| requests (~30 files) | 211/212 covered | all real edges covered | 873 edges |
| flask (~80 files) | 182/190 covered | all real edges covered | ~1,200 edges |
| odoo (2,364 files) | 99.0% of 28,486 covered | **crashed — out of memory at 44 GB** | **7½ min, 760k edges** |

(For scale: our own previous engine, PyCG, ran **3 hours 19 minutes** on
that odoo corpus without finishing and produced zero edges. That is why
it was removed.)

### What about the last 1%?

Every uncovered edge was audited by hand. None survived scrutiny:

- **Edges from calls that don't exist.** Joern claims a function calls
  `append`; the function's source contains no `append`. We checked.
- **Edges to targets that don't exist.** Fraunhofer emits `None.read`
  and `object.object`, and invents methods on classes that never declare
  them (a method actually inherited from an external library — we point
  at the real one instead).
- **Different names for the same thing.** odoo does
  `guess_mimetype = _odoo_guess_mimetype`; they point at the alias, we
  point at the actual function. Their `RLock` is our
  `_dummy_threading._RLock` — the class it truly is.
- **Their internal bookkeeping nodes** — synthetic `<lambda>0`,
  `<metaClass...>`, `<redefined>` entries that are artifacts of their
  graph format, not calls.

### The best part: the chase fixed real bugs

Diffing against two independent tools is a brutal test, and it kept
catching *our* defects, not just theirs:

- Our symbol table silently **skipped any function defined inside an
  `if` or `try`** — on odoo, whole families of functions simply didn't
  exist in our output.
- Jedi sometimes "resolves" a call to nonsense — `typing.Callable` for a
  decorated function, `classmethod.__get__` for `cls.helper()`, the
  stdlib's `_warnings.warn` for odoo's own `self._warn`. Those junk
  answers used to block the detective from even trying.
- Every module-level call to a library function was being dropped by an
  over-eager filter.

## And it's repeatable

Run the analyzer twice on the same code and you get **byte-identical**
output on the test fixtures, and 99.91% identical on the 2,364-file odoo
corpus — the tiny remainder traces to a known probabilistic quirk inside
Jedi's inference (tracked as issue #146), not to anything we built. Same
input, same graph. That is what makes the output diffable, cacheable,
and trustworthy in CI.
