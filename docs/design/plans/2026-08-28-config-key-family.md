# ConfigKey Family Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract configuration keys as first-class `ConfigKey` nodes from the six v1 formats, plus three bounded riders (closes #152).

**Architecture:** New `codeanalyzer/artifacts/config_keys.py` flattens config formats into `PyConfigKey` records nested on `PyArtifact.config_keys`, wired into `core.analyze()` beside `build_dependency_view`; Neo4j projects neutral `ConfigKey` nodes via `DEFINES_CONFIG`.

**Tech Stack:** tomllib/tomli, yaml, json, configparser (all in-tree), hand-rolled env/properties line parsers, existing `Span`/`byte_offsets` helpers.

**Spec:** `docs/design/specs/2026-08-28-config-key-family-design.md`

## Global Constraints

- No AI attribution anywhere. Conventional Commits.
- Neutral graph vocabulary: label `ConfigKey`, edge `DEFINES_CONFIG` — never `PyConfigKey`/`PY_DEFINES_CONFIG` in the graph.
- `value` populated ONLY when `options.artifact_text` is true; keys/namespace/span/references always extracted, from full on-disk text (never the possibly-truncated stored `source`).
- Parse failure never drops an artifact; it sets `extraction: "partial"`.
- L1 data: identical at every `-a`; deterministic (sorted key order within each artifact).
- ConfigKey id = `<artifact-id>@key/<dotted.key>`; dotted paths use numeric segments for arrays.
- Namespaces exactly: env, yaml, json, toml, ini, properties.
- references[] recognizes exactly: `${VAR}`/`$VAR`, `%(name)s`, `${{ ... }}` — raw tokens, order of appearance, deduplicated.

---

### Task 1: Model + id helper

**Files:** Modify `codeanalyzer/schema/py_schema.py` (PyConfigKey near PyArtifact; `config_keys: List[PyConfigKey] = []` on PyArtifact), `codeanalyzer/schema/ids.py` (`config_key_id(artifact_id: str, dotted_key: str) -> str` returning `f"{artifact_id}@key/{dotted_key}"`). Test `test/test_config_key_models.py`.

Model fields per spec §Model: id, key, namespace, value: Optional[str] = None, span: Optional[Span] = None, references: List[str] = []. Round-trip test via compat helpers; default-empty on old payloads; id shape asserted.
Commit: `feat(schema): PyConfigKey model and config_key_id helper`

### Task 2: Flatteners + reference recognition

**Files:** Create `codeanalyzer/artifacts/config_keys.py`. Test `test/test_config_key_extraction.py`.

Public API: `extract_config_keys(artifact: PyArtifact, full_text: str, capture_value: bool) -> List[PyConfigKey]` dispatching on namespace (env by basename family `.env`/`.env.*`/`.flaskenv`; else by format for yaml/json/toml/ini/properties; other formats → []). Internals: `_flatten(obj, prefix)` producing dotted paths with numeric array segments; env parser (KEY=value, `#` comments, `export ` prefix, single/double quote stripping); properties parser (`key=value`/`key: value`, `\` continuations, `!`/`#` comments); ini via configparser with raw=True (preserve `%(x)s`); `_find_references(text) -> List[str]` with the three regexes (`\$\{\{[^}]*\}\}` FIRST, then `\$\{[A-Za-z_][A-Za-z0-9_]*\}`, then `\$[A-Za-z_][A-Za-z0-9_]*`; dedupe preserving order). Spans: line/col of the key's line (env/properties/ini exact line; yaml/json/toml best-effort via first occurrence search of the final key segment on its own line — record the chosen rule in a comment). Never raises: any exception → return partial list gathered so far and signal failure via return, or raise a single ConfigParseError the caller catches — pick one, test it.
Tests: nested yaml→dotted, arrays→numeric segments, env quoting/comments/export, properties continuations, ini interpolation preserved raw + reference recognized, toml tables, json nesting, all three reference syntaxes incl. dedupe/order, capture_value=False → value None everywhere else identical, malformed input per format → failure signaled without exception escaping.
Commit: `feat(artifacts): config-key flatteners with reference recognition`

### Task 3: Wiring + riders

**Files:** Modify `codeanalyzer/core.py` (call after build_dependency_view: iterate sorted artifacts, namespace-eligible ones get `art.config_keys = extract_config_keys(...)` reading full text via the same on-disk read used by dependencies `_full_text`; on failure set `extraction = "partial"`), `codeanalyzer/artifacts/discovery.py` (rule `("*.tf", "text", ["iac"])`), `codeanalyzer/schema/py_schema.py` (`PyDependency.ecosystem: str = "pypi"`), `codeanalyzer/artifacts/dependencies.py` (set ecosystem explicitly where records are built — both _emit and the lock-only transitive branch). Tests: extend `test/test_artifact_pipeline.py` (keys present at `-a 1` and `-a 2`, identical), new asserts in `test_artifact_discovery.py` (`main.tf` → role iac) and `test_dependency_view.py` (ecosystem == "pypi" on every record).
Commit: `feat(artifacts): wire config-key extraction into analyze; tf rule; ecosystem field`

### Task 4: Neo4j projection

**Files:** Modify `codeanalyzer/neo4j/schema.py` (NodeLabel ConfigKey merge key id, props: id, key, namespace, value string, references string[], start_line, end_line; RelType DEFINES_CONFIG Artifact→ConfigKey), `codeanalyzer/neo4j/project.py` (in `_project_artifacts`: per artifact, per config_key sorted by key — node + edge; value omitted from props when None), regenerate `schema.neo4j.json`, extend `test/sample_graph_app.py` (one artifact with a config key so the every-label test passes). Test `test/test_neo4j_config_keys.py`: node + edge rows projected, value-absent when model value None, catalog/constraint present.
Commit: `feat(neo4j): neutral ConfigKey nodes via DEFINES_CONFIG`

### Task 5: e2e + docs

**Files:** Fixture `test/fixtures/whole_applications/manifests_app/` gains `.env` (with a `${VAR}` reference + a SECRET-looking key), `config/settings.yml` (nested + array), `app.properties`; extend `test/test_artifacts_end_to_end.py` (exact key sets per file, namespace env on .env, references extracted, value-gating via a `--no-artifact-text`-equivalent options run, spans slice source to the value, artifact-set `==` assert updated); CHANGELOG Unreleased line; README Output-shape sentence. Full suite `uv run pytest test/ -q --no-cov` — record counts.
Commit: `test(artifacts): config-key e2e coverage; docs`
