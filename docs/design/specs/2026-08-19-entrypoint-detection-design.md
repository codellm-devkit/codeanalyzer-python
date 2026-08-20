# Spec: framework-independent entrypoint detection

Status: draft for review
Date: 2026-08-19
Scope: `codeanalyzer-python` — schema v2 fields, a new `codeanalyzer/entrypoints/` subsystem
Issue: #27

---

## 1. Summary

The analyzer emits a symbol table and a call graph with no identified roots. Nothing says which
callables a framework invokes from outside the application, so reachability, dead-code and
attack-surface analyses have nowhere to start, and every consumer re-derives it. Downstream, all
four `PythonAnalysis.get_entry_point_*` methods raise `NotImplementedError`
(`python-sdk/cldk/analysis/python/python_analysis.py:881-960`) purely because the backend supplies
no data, while Java has emitted `is_entrypoint` / `is_entrypoint_class` for some time
(`python-sdk/cldk/models/java/models.py:343,403`).

This adds a post-pass over the built symbol table that flags entrypoints from four independent
mechanisms — declared metadata, definition-site decorators, inheritance, and external routing
tables — driven by a user-extensible `rules.yml`.

### Prior art, corrected

The roadmap states the entrypoint vocabulary has been "coined three ways". Verified on
2026-08-19, that is not accurate: **Java has the only implementation** (boolean flags plus a
`JEntrypoint` Neo4j marker label). TypeScript *declares* `TSApplication.entrypoints:
Dict[str, List[TSEntrypoint]]` in its `SCHEMA_DECISIONS.md` invariant spine, but `TSEntrypoint`
appears nowhere in `codeanalyzer-typescript-v2/src/` — the only matches are comments about
bundler entrypoints. Python is therefore the **second** implementation, and the record shape
chosen here is what TypeScript would be asked to match.

## 2. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Node-level records, not a root collection | Java precedent; keeps the fact next to the node it describes |
| D2 | Routing pre-pass ships in v1; Django supported from the start | Django binds views in `urls.py`, not at the definition site. Detection that silently misses the most common enterprise framework is worse than none — a consumer cannot distinguish "no entrypoints" from "unsupported" |
| D3 | `urls.py` resolved by **constrained symbolic evaluation**, never execution | Literals, name lookups, list/tuple concatenation and comprehensions over literal iterables. No function calls, no conditionals, nothing imported and run. Executing the code under analysis would make CLDK unsafe to point at untrusted repositories |
| D4 | `entrypoints: List[PyEntrypoint]`, with `is_entrypoint: bool` derived | One callable is genuinely an entrypoint more than once — two `@app.route`s, or both a Celery task and a CLI command. A boolean or a single framework string silently collapses that. The boolean is denormalized for Java parity and a one-field SDK filter |
| D5 | No `kind` field | `framework` plus `route is not None` answers the queries `kind` would serve; a second vocabulary to align across analyzers is not worth it |
| D6 | Declarative `rules.yml` for decorator / inheritance / naming rules; named **engines** for routing, packaging readers and structural passes | Groups 2-3 are pattern lists that rot as frameworks change; adding Sanic should be a data edit. Routing is partial evaluation and cannot be expressed as data |
| D7 | Both the routed class and its dispatched methods are flagged; methods carry `via` | `urls.py` names the class, the framework calls the method. Class-only leaves handler methods unreachable, defeating the primary use case |

Also decided: rules carry stable ids so a user file can `disable:` them; user rules merge additively
with the shipped set, deduplicated on `(framework, evidence, route)`; a malformed **user** rules
file is a hard error before analysis begins; each record records which ruleset produced it. CLI:
`--entrypoint-rules <path>`, repeatable.

## 3. Data model

```python
class PyEntrypoint(BaseModel):
    framework: str                   # flask | django | celery | packaging | ...
    confidence: str                  # closed set: "declared" | "certain" | "heuristic"
                                     #   declared  - named in a manifest; cannot be wrong
                                     #   certain   - an unambiguous framework signal
                                     #   heuristic - a convention or a weak signal
    rule: str                        # stable id. Declarative rules use their `id:` from
                                     #   rules.yml ("flask.route"); engines use their engine
                                     #   name ("django_urls", "pyproject.scripts")
    ruleset: str                     # "shipped" | "user:<path>"
    evidence: Optional[str] = None   # binding site, e.g. "shop/urls.py:7"
    route: Optional[str] = None      # composed path, HTTP only
    http_methods: List[str] = []
    via: Optional[str] = None        # can:// id of the routed node dispatching here
```

Carried on `PyCallable` and `PyClass`:

```python
entrypoints: List[PyEntrypoint] = []
is_entrypoint: bool = False          # derived: len(entrypoints) > 0, never authored
```

For `path("products/", ProductList.as_view())` reached through `path("shop/", include("shop.urls"))`:

```
PyClass ProductList        route "/shop/products/"  via: null
ProductList.get            route "/shop/products/"  via: <ProductList can:// id>
ProductList.post           route "/shop/products/"  via: <ProductList can:// id>
```

The class states what is routed; the methods state what is invoked. `via` lets a consumer walk
from a reachability root back to the route reaching it. For decorator-site frameworks `via` is
null — the function is both routed and invoked.

`route` on a method is copied from the class, not independently derived. `confidence` is
per-record, so one callable may hold a `declared` record from `[project.scripts]` and a
`heuristic` one from a naming convention at the same time; consumers threshold on it rather than
inheriting this analyzer's judgement.

### Application-level report

```python
class PyEntrypointReport(BaseModel):
    frameworks_detected: List[str] = []
    rulesets: List[str] = []
    unresolved: Dict[str, int] = {}   # file -> patterns not resolved
    errors: List[str] = []
```

on `PyApplication`. D3's failure mode is silence; this is what makes it visible. A consumer seeing
`frameworks_detected: ["django"]`, zero entrypoints, and `unresolved: {"shop/urls.py": 12}` knows
exactly what happened.

## 4. Detection pipeline

Entrypoint detection is a **post-pass over the built symbol table**, not a pre-pass threaded
through the builder as #27 originally sketched. `urls.py` names `views.product_list`, and
resolving that requires the symbol table to exist. A post-pass makes every resolution a lookup
against ids that already exist and leaves `symbol_table_builder.py` untouched.

```
symbol table built (L1)
  Stage 0  framework detection — gates all later stages. A framework is detected when its
           package is imported by first-party source OR named in the dependency manifest
           (either is sufficient; a manifest entry with no import still gates in, since the
           import may be dynamic)
  Stage 1  declared readers      (no AST)
  Stage 2  routing pre-pass      (per project)
  Stage 3  per-node matching     (rules.yml: decorators, bases, dispatch)
  Stage 4  structural passes     (argument-position, __main__ walk)
  Stage 5  derive is_entrypoint + emit the report
```

Stages 1-4 **append**; multiplicity is the model, so two stages flagging one callable is correct,
not a conflict. Stage 0 gating means a project without Celery never pays for Celery rules and
cannot false-positive on a locally-defined `shared_task`.

### Declared readers

All reduce to one shape: a string naming `module:attr`. One engine, pluggable readers, one shared
resolver turning `pkg.cli:main` into a `can://` id.

`pyproject.toml` `[project.scripts]` / `[project.gui-scripts]` / `[project.entry-points."<group>"]`
(plugin systems such as `pytest11` are real entrypoints invoked by other tools), Poetry's
`[tool.poetry.scripts]`, `setup.cfg` `[options.entry_points]`, SAM / `serverless.yml` handlers,
and — via engines, since they are not structured data — `setup.py`, `Procfile`, `Dockerfile`.

`setup.py` is D3's problem in miniature: `entry_points` can be computed. Same posture — match the
literal dict, record what cannot be resolved, never execute.

A declared entry proves the package *declares* an entrypoint, not that the target lives in this
repo. The resolver drops records whose id is absent from the symbol table and counts them, under
the same no-dangling-endpoints rule the call graph enforces.

## 5. `rules.yml`

Three blocks: `declared:` (readers), `frameworks:` (decorators, bases, dispatch), and engines
named by key. Matching is on `qualified_name`, which #128 made available — so `@route` under
`from flask import route` matches the same rule as `@app.route`.

```yaml
version: 1

declared:
  - id: pyproject.scripts
    file: pyproject.toml
    format: toml
    path: [project, scripts]
  - id: setup.py
    file: setup.py
    engine: setup_py

frameworks:
  flask:
    detect: [flask]
    decorators:
      - id: flask.route
        match: "flask.Flask.route"
        route: {from: positional, index: 0}
        methods: {from: keyword, name: methods, default: [GET]}
    bases:
      - id: flask.methodview
        match: "flask.views.MethodView"
        transitive: true
        dispatch: [get, post, put, delete, patch]

  django:
    detect: [django]
    bases:
      - id: django.cbv
        match: "django.views.generic.*"
        transitive: true
        dispatch: [get, post, put, patch, delete, head, options]
    routing: django_urls
```

`confidence` defaults to `certain` and is written only when it is not. `dispatch:` is what makes
D7 declarative. `path:` is capped at key names plus `*` (one level) and `**` (recursive) — the
moment a reader needs a predicate or a transform it becomes an `engine:` instead. Without that
line `rules.yml` becomes a programming language with no debugger.

## 6. Django routing engine

Input: the built symbol table. Output: `{dotted_view_name -> [Route]}` plus an unresolved count
per file.

```
1. Roots       ROOT_URLCONF from settings if literal; else every urls.py
2. Env         module-level literals, lists/tuples, imported names resolved via the symbol table
3. Walk        path/re_path/url(literal, target, ...)
                 Name | Attribute            -> view reference
                 Call to *.as_view()         -> the class
                 include("app.urls")         -> recurse, compose prefix
                 include((module, namespace)) -> same, keep namespace
                 router.urls                 -> from registered routers
4. Routers     router.register(prefix, ViewSet) binds that ViewSet at that prefix
5. Otherwise   record unresolved, with file:line
```

Dispatched methods come from the `dispatch:` list of the matching `bases:` rule, **intersected
with the methods the class actually defines** — a `ListView` defining only `get` gets no phantom
`post` entrypoint.

Prefix composition is the part most likely to be subtly wrong (trailing slashes, regex prefixes,
empty prefixes). `route` is therefore best-effort presentation; the flag and `via` are the
load-bearing facts, and a consumer computing reachability must not depend on the string being
exact.

## 7. Error handling

Entrypoint detection is additive metadata: its failure degrades to "no entrypoints", never to "no
analysis". The post-pass is wrapped; a finder crash loses flags, not the symbol table — and is
recorded in `PyEntrypointReport.errors` rather than only logged.

| Failure | Behaviour |
| --- | --- |
| Malformed **user** rules file | Hard error before analysis starts — never silently skipped |
| Malformed **shipped** rules file | Schema-validated in CI; cannot occur at runtime |
| No `pyproject.toml` / `setup.cfg` | Reader skips; not an error |
| Declared target absent from the symbol table | Record dropped and counted (no dangling endpoints) |
| `urls.py` fails to parse | File skipped, counted unresolved, walk continues |
| `include()` cycle | Visited-set; recorded as unresolved |
| Jedi cannot resolve a base class | Rule does not match — under-approximate, never guess |

## 8. Testing

The repo has **no Django fixture**; `whole_applications/` holds `flask`, `requests` and `xarray`,
which are those libraries' own source, not applications using them. Fixtures are real work.

`single_functionalities/django_routing/` exercises: prefix composition through `include()`; a CBV
via `as_view()` with the dispatch split; a plain function view; `urlpatterns = base + extra`; a
deliberately unresolvable `path(COMPUTED, ...)`; a DRF `router.register`; a `ListView` defining
only `get`; and a helper that must not be flagged.

Smaller fixtures for Flask, FastAPI, Celery and Click, plus a `packaging/` fixture with
`[project.scripts]`, a plugin `entry-points` group, and one entry pointing at a dependency that
must be dropped.

Assertions are **exact sets** of `(node, framework, rule, route, via)`, hand-written and compared
— the only way to catch over-flagging. The tests that matter most are negative and gating: the
helper is not flagged; a local `shared_task` in a project without Celery is not flagged; the
`ListView` yields exactly one method entrypoint; the unresolvable pattern increments `unresolved`
rather than vanishing.

Plus unit tests for rules loading (merge, `disable:` by id, malformed user file raising before
analysis, `path:` wildcards) and a monotonicity check that entrypoints are identical at `-a 1`
through `-a 4`, since this is L1 data.

## 9. Out of scope

CRUD detection, specced separately. Taint sources and reachability computation — the analyzer
emits substrate; those are SDK queries under the provider/client boundary. The argument-position
rule (a first-party callable passed to a call resolving outside the project — which would catch
`add_url_rule(view_func=h)`, `scheduler.add_job(run)` and any framework with no finder) is
**deferred**: `PyCallArgument` carries only `ast_kind` and `inferred_type`, with no resolved
identity, so it needs the same id-space work #128 did for decorators. It is the rule that
generalizes furthest and should be the first follow-up.

## 10. Decomposition

This is not one pull request. The units below land independently and in this order; each is
closed by its own PR and its own work item, filed when picked up:

1. **Schema + pipeline skeleton** — `PyEntrypoint`, `PyEntrypointReport`, the carriers, the
   derived boolean, and the wrapped post-pass that currently finds nothing. Establishes the
   contract and the failure posture with no detection logic to argue about.
2. **`rules.yml` loader** — parsing, schema validation, shipped/user merge, `disable:`, the CLI
   flag. Testable with no framework involved.
3. **Declarative matching (Stage 3)** — decorator and inheritance rules, the `dispatch:` split.
   Delivers Flask, FastAPI, Celery, Click and DRF decorators; the first user-visible result.
4. **Declared readers (Stage 1)** — packaging metadata and manifests. Independent of 3; could
   swap order.
5. **Django routing engine (Stage 2)** — the largest and riskiest unit, and the one needing a
   fixture built from scratch.
6. **Structural passes (Stage 4)** — `__main__` walk. The argument-position rule stays deferred.

Unit 1 gates everything. Units 3 and 4 are parallel. Unit 5 should not start before 1-3 are
merged, since it depends on both the record shape and the `dispatch:` mechanism.

## 11. Risks

- **Option B's coverage against real projects is unmeasured.** The fixtures test the rules we
  wrote; they cannot say what fraction of real `urls.py` files resolve. Before anyone depends on
  these flags for security work, run the pre-pass over several open-source Django projects and
  report the resolved ratio.
- **`rules.yml` is a model pack in the analyzer**, while this repo's provider/client split puts
  model packs in the SDK. The distinction being set deliberately: *policy* packs (what is a taint
  source) stay in the SDK; *detection* packs requiring the AST live in the analyzer, because the
  SDK sees only serialized output.
- **Shipped rules go stale** as frameworks change. Mitigated by user extensibility and by
  `frameworks_detected` making a coverage gap visible, not by promising to keep up.
- **`via` is new cross-language vocabulary.** If TypeScript later implements its paper
  `entrypoints` collection, the per-entrypoint record is the shared part and only placement
  differs — but the name should be agreed before it ships twice.
