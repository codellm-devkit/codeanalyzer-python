# config_use Edge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mint `PY_USES_CONFIG` edges (three deterministic tiers) plus first-class unresolved config reads (closes #162).

**Architecture:** `PyCallArgument` gains literal capture (`value`) and bare-name capture (`name`); a shipped detector table (`codeanalyzer/artifacts/config_use_rules.yml`) identifies config-reading call sites; `codeanalyzer/artifacts/config_use.py` resolves keys per tier (literal at L2, DDG single-literal closure at L3, cross-procedure closure at L4) against `PyArtifact.config_keys`, emitting `application.config_uses` + `application.config_reads_unresolved`; Neo4j projects `PY_USES_CONFIG` and `PY_READS_CONFIG_UNRESOLVED`.

**Tech Stack:** existing symbol-table/DDG/call-graph substrates; yaml (in-tree); json for value encoding.

**Spec:** `docs/design/specs/2026-08-28-config-use-edge-design.md`

## Global Constraints

- No AI attribution. Conventional Commits. Pydantic v1 compat (no v2-only idioms outside compat helpers).
- Determinism: no tier ever guesses. A chain not closing on exactly one string literal (identical duplicates count as one) is unresolved. Sorted iteration everywhere; two identical runs byte-identical.
- Superset-monotonicity: `config_uses` at `-a 2` ⊆ `-a 3` ⊆ `-a 4`; CI-gate test required. `PyCallArgument.value`/`name` are L1 data.
- prov vocabulary exactly: `literal`, `dataflow`. `reason` vocabulary exactly: `non-literal`, `undefined-key`.
- `value` is JSON-encoded (`json.dumps`) for any `ast.Constant`; `name` is the bare identifier for `ast.Name` args; both `None` otherwise.
- Namespace preference per detector entry; first namespace with ≥1 match wins; all matches in that namespace get edges.
- Neutral ConfigKey untouched; the new edge and unresolved rel are `PY_`-prefixed (this analyzer's claim).

---

### Task 1: Argument capture (L1)

**Files:** Modify `codeanalyzer/schema/py_schema.py` (PyCallArgument: `value: Optional[str] = None`, `name: Optional[str] = None`, field comments documenting json.loads decode + bare-Name capture), `codeanalyzer/syntactic_analysis/symbol_table_builder.py:786` (populate both: `value=json.dumps(arg.value)` when `isinstance(arg, ast.Constant)` and the value is str/int/float/bool/None; `name=arg.id` when `isinstance(arg, ast.Name)`). Spec amendment: add one sentence to the spec's Locked decision 1 noting `name` (bare-identifier capture) ships alongside `value` — the dataflow tier's join point (controller ruling, recorded in ledger). Test `test/test_call_argument_capture.py`: `f("DB_HOST")` → value `'"DB_HOST"'`; `f(3)` → `'3'`; `f(True)`/`f(None)` → `'true'`/`'null'`; `f(KEY)` → value None, name `"KEY"`; `f(obj.attr)` → both None; L1 round-trip through compat helpers; old payloads default None.
Commit: `feat(schema): literal and name capture on call arguments`

### Task 2: Detector table + literal tier + unresolved records

**Files:** Create `codeanalyzer/artifacts/config_use_rules.yml` (entries: `module`, `callable`, `key_arg` int position, `kwarg` optional name, `namespaces` ordered list — v1 set per spec §3 incl. os.environ.get/os.getenv arg 0 [env]; dotenv.get_key arg 1 [env]; configparser get/getint/getfloat/getboolean arg 1 kwarg "option" [ini, properties]), `codeanalyzer/artifacts/config_use.py` (loader with schema validation; `detect_config_reads(app) -> List[_Read]` scanning body call nodes whose `callee` matches a rule's external id suffix `@external/<module>/<callable>` — also match dotted receiver forms the linker emits; `_Read` = site global id, callee id, rule, key literal via value-decode, key name via name field; `resolve_uses(reads, app, tier_fns) -> (List[PyConfigUseEdge], List[PyConfigRead])` — literal tier: decoded str key → ConfigKey match per namespace preference (env exact `key ==`; ini/properties suffix `key.endswith("." + literal) or key == literal`); build edges prov ["literal"]; non-literal or no-match → PyConfigRead with reason), models in `codeanalyzer/schema/py_schema.py` (`PyConfigUseEdge {src, dst, prov}`, `PyConfigRead {site, callee, key: Optional, reason, prov}`, application fields `config_uses`/`config_reads_unresolved` default []), core wiring in the `>= 2` block after callee backfill. Subscript caveat: verify whether `os.environ["X"]` produces a call body node; document the finding in the module docstring; if not lowered, note as recorded gap (no new mechanism).
Test `test/test_config_use_literal.py`: fixture-in-tmp project with `.env` (DB_HOST=x) + code `os.getenv("DB_HOST")` → one edge, prov ["literal"]; `os.getenv("MISSING")` → unresolved reason "undefined-key"; `os.getenv(kvar)` → reason "non-literal" at -a 2; namespace preference (ini option matching section.option suffix); determinism two-runs.
Commit: `feat(artifacts): config-use detector table and literal tier`

### Task 3: Dataflow tiers

**Files:** Extend `codeanalyzer/artifacts/config_use.py` (+ wiring in core L3/L4 blocks). Intra (L3): for each unresolved read with `name` set, find its callable's DDG edges into the call node's local id with var == name; for each def endpoint, slice the module source at the def node's span and `ast.parse` the statement — accept exactly `Name = <str Constant>` single-target Assign; all reaching defs must yield one identical literal → resolve (prov ["dataflow"]), else stays unresolved. Interproc (L4): unresolved read whose `name` is a parameter of its enclosing callable → all call sites targeting that callable (via call_graph + body callee ids): each site's PyCallArgument at the param's position/kwarg must be the same str literal (value field), OR itself close intra-procedurally in ITS caller (one recursion level, visited-set guarded); all sites agree → resolve, else unresolved. Reads resolved at a lower tier are never recomputed (additive; monotonicity by construction — L3 run = literal + intra; L4 run = literal + intra + interproc).
Test `test/test_config_use_dataflow.py`: `KEY = "DB_HOST"; os.getenv(KEY)` resolves at -a 3 not -a 2; two differing defs → unresolved at all levels; loop-carried (`for k in [...]: os.getenv(k)`) unresolved; `def read(name): return os.getenv(name)` + single call `read("DB_HOST")` resolves at -a 4 not -a 3; two call sites with different literals → unresolved; monotonicity assert `-a 2 ⊆ -a 3 ⊆ -a 4` on edge sets.
Commit: `feat(artifacts): dataflow tiers for config-use resolution`

### Task 4: Neo4j projection

**Files:** `codeanalyzer/neo4j/schema.py` (RelTypes: `PY_USES_CONFIG` [PyBodyNode]→[ConfigKey] props `{prov: "string[]"}`; `PY_READS_CONFIG_UNRESOLVED` [PyApplication]→[PyExternal] props `{key: "string", reason: "string", prov: "string[]"}`), `codeanalyzer/neo4j/project.py` (emit both from app.config_uses/config_reads_unresolved — src PyBodyNode by global id, dst ConfigKey by id; unresolved: callee external ghost merged like PY_UNRESOLVED_IMPORT does), regenerate `schema.neo4j.json`, extend `test/sample_graph_app.py` so both rels are exercised (its pyproject config keys + an os.getenv literal read in the sample source). Test `test/test_neo4j_config_use.py` mirroring test_neo4j_config_keys.py idioms.
Commit: `feat(neo4j): PY_USES_CONFIG and unresolved-read projection`

### Task 5: e2e + skill + docs

**Files:** manifests_app fixture: add `pkg/config_reader.py` covering all five DoD shapes (direct literal hitting `.env`'s DB-ish key, variable-closing, param-passed via helper, multi-def unresolved, undefined-key read); extend `test/test_artifacts_end_to_end.py` (edge set exact at -a 4; unresolved reasons exact; determinism); CHANGELOG Unreleased line; README Output-shape sentence; on the design/skill-neo4j-exhaustive branch DO NOTHING (skill update tracked there per DoD — instead append a line to the ledger). Full suite, exact counts.
Commit: `test(artifacts): config-use e2e coverage; docs`
