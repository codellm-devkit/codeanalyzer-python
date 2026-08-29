# config_use: The PY_USES_CONFIG Edge

**Date:** 2026-08-28
**Status:** Approved (design dialogue in-session)
**Scope:** codeanalyzer-python, schema v2 additive; closes #162
**Builds on:** ConfigKey family (`2026-08-28-config-key-family-design.md`, PR #163)

## Problem

The artifact layer has both shores: config files define keys
(`(:Artifact)-[:DEFINES_CONFIG]->(:ConfigKey)`) and code calls out through
externals. The bridge — *which statement reads which key* — is this unit:

```
(:PyBodyNode)-[:PY_USES_CONFIG]->(:ConfigKey)
```

Py-prefixed by the settled rule: reading a key is this analyzer's claim about
Python code; the key node stays neutral. Sibling analyzers mint their own
(`TS_USES_CONFIG`, …) — recorded on epic codellm-devkit/.github#45 as a
standard feature of the layer.

## Locked decisions

1. **`PyCallArgument.value: Optional[str]`** — any `ast.Constant`
   **JSON-encoded** (`'"DB_HOST"'`, `'3'`, `'true'`, `'null'`); non-constant
   arguments stay `None`. L1 body data, additive, emitted for every call
   argument. Decoding rule documented on the field: `json.loads` yields the
   Python constant (str/int/float/bool/None). `PyCallArgument.name:
   Optional[str]` ships alongside `value` — the bare identifier of an
   `ast.Name` argument (`None` otherwise), added because the dataflow tier
   has no other join point back to the variable's own definitions
   (controller ruling; amends this decision).
2. **Detection is level-graded, all in scope** (per the amended #162):
   - **Literal tier** (`-a 2`+, `prov: ["literal"]`): the call node's callee
     resolves to a detector-listed external and the key argument is a string
     literal (`value` decodes to `str`).
   - **Dataflow tier intra** (`-a 3`+, `prov: ["dataflow"]`): non-literal key
     whose DDG def-use chain closes over exactly one string-literal
     definition — constant propagation over the analyzer's own deterministic
     DDG.
   - **Dataflow tier interprocedural** (`-a 4`, same `prov`): the chain
     extends through `param_in` — a formal whose every reaching actual is
     the same string literal resolves reads inside the callee.
   - Chains not closing on exactly one literal stay **unresolved** — never
     guessed. Superset-monotonic across levels (literal ⊆ +intra ⊆
     +interproc), same additive contract as the DDG's prov widening.
3. **Detector ruleset is a shipped table**, `artifacts/config_use_rules.yml`
   (same mechanism family as `entrypoints/rules.yml`): entries declare the
   external target (module + callable name), which argument is the key
   (position or kwarg name), and the **namespace preference order** for
   resolution. Shipped v1 set:
   - `os.environ.__getitem__` / `os.environ.get` / `os.getenv` → arg 0,
     namespaces `[env]`
   - `dotenv.get_key` → arg 1 (key), `[env]`; `dotenv.dotenv_values` — no key
     arg, not an edge source
   - `configparser` `get`/`getint`/`getfloat`/`getboolean` → kwarg/pos
     `option` (arg 1), namespaces `[ini, properties]`
   No user-extension flag yet (matches artifact rules posture).
4. **Resolution**: literal key text → ConfigKey nodes matched by `key ==
   literal` (env namespace: exact; ini: match the `section.option` suffix —
   the option name matches the last dotted segment) within the detector's
   namespace preference order; first namespace with ≥1 match wins; ALL
   matching keys in that namespace get edges (a key defined in `.env` and
   `.env.prod` yields two edges — both true).
5. **Unmatched reads are first-class**: `application.config_reads_unresolved:
   List[PyConfigRead]` — every detector hit whose key is statically unknown
   (no literal, chain didn't close) or whose literal matches no ConfigKey
   (reads config nobody defines — the hygiene signal). Fields: `site`
   (GLOBAL ordinal id), `callee` (external id), `key` (Optional — present
   when literal known but undefined), `reason` (`"non-literal"` \|
   `"undefined-key"`), `prov`.
6. **JSON placement**: `application.config_uses: List[PyConfigUseEdge]`
   (`src` GLOBAL ordinal, `dst` ConfigKey id, `prov`) — application scope,
   mirroring `param_in` (endpoints span callables/artifacts).

## Neo4j

`PY_USES_CONFIG` RelType (PyBodyNode→ConfigKey, props `prov: string[]`);
`config_reads_unresolved` projected as `PY_READS_CONFIG_UNRESOLVED`
(PyApplication→PyExternal ghost of the callee, props `key`, `reason`,
`prov`) — mirrors `PY_UNRESOLVED_IMPORT`'s shape. `SCHEMA_VERSION` stays
2.0.0.

## Level gating

`PyCallArgument.value` emitted at L1 (body build). Edges: literal tier
computed in the L2 block (needs resolved callees); dataflow-intra in the L3
block (needs DDG); interproc in the L4 block (needs param_in). Monotonicity
gate extended to `config_uses`. `config_reads_unresolved` is not itself
monotonic -- it deliberately shrinks as levels rise (a record a higher tier
resolves migrates into `config_uses` and drops out of the unresolved list);
the monotonicity contract binds the edge set, and the unresolved list is
its complement, in the same sanctioned-refinement family as `callee:
null→id`.

## Caveats

- `os.environ["X"]` is subscript, not a call — the literal tier reads it via
  the body node's call lowering only if the symbol table lowers
  `__getitem__`; if it does not, v1 detects `.get`/`getenv` forms and the
  subscript form is a recorded gap (verify during implementation; if absent,
  add a targeted subscript detector on the same table entry rather than a
  new mechanism).
- Multi-literal closure (a chain closing on two *identical* literals) counts
  as closed; differing literals = unresolved.
- configparser section context is not tracked (which `[section]` the option
  belongs to at the read site is dynamic); suffix matching may over-match
  across sections — recorded, acceptable v1 (prov marks the tier, consumers
  can threshold).
- Kwarg-passed keys (`cp.get(section, option="x")`) land in non-literal
  unresolved: `BodyNode.arguments` only ever holds positional arguments
  (`symbol_table_builder.py` walks `node.args`, never `node.keywords`), so a
  kwarg-only call has no substrate to read the key from.

## Definition of done

- [ ] `PyCallArgument.value` JSON-encoded constants at L1, round-trip tested.
- [ ] Literal-tier edges at `-a 2`; dataflow-intra at `-a 3`; interproc at
  `-a 4`; superset-monotonic (gate test).
- [ ] Fixture: direct literal, variable-closing-to-literal, param-passed
  literal, multi-def unresolved, loop-carried unresolved, undefined-key read.
- [ ] `config_reads_unresolved` populated with reasons; both projections;
  determinism (two identical runs); full suite green.
- [ ] Skill vocabulary/analyses updated on the #161 line (bridge queries).
