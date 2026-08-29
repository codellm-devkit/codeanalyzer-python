# ConfigKey Family: Configuration Keys as First-Class Nodes

**Date:** 2026-08-28
**Status:** Approved (design dialogue in-session)
**Scope:** codeanalyzer-python, schema v2 additive; closes #152 (core); config_use deferred
**Builds on:** `2026-08-27-artifacts-and-dependencies-design.md` (the artifact substrate)

## Problem

Artifacts capture configuration *files* verbatim, but their *meaning* — the
keys a deployment defines, the values services wire together, the references
that stitch `.env` → compose → CI — is opaque text. #152's config_key family
extracts it. The definitions side lands here; the `config_use` edge (code
reading a key) is the recorded follow-up.

## Locked decisions

1. **Neutral vocabulary.** A yaml key is not a Python concept: graph label
   `ConfigKey`, edge `DEFINES_CONFIG` (Artifact→ConfigKey) — same
   nouns-neutral rule as `Artifact`/`Package`; the Python-specific claim
   arrives later as `PY_USES_CONFIG`. (Supersedes #152's `PyConfigKey` /
   `PY_DEFINES_CONFIG` naming per the reconciliation on #152.)
2. **Value gated on text capture.** `value: Optional[str]` populated only
   when `--artifact-text` is on (default). Keys, namespaces, spans, and
   references are always extracted — `--no-artifact-text` drops values and
   source together, so the secret off-switch actually switches everything off.
3. **V1 formats**: `env` (`.env`, `.env.*`, `.flaskenv`), `yaml`, `json`,
   `toml`, `ini`, `properties`, `dockerfile`. Extraction is format-driven over
   existing artifacts (pyproject.toml gets keys too; overlap with dependency
   records is harmless). `references[]` v1 recognizes three syntaxes, recorded
   as raw tokens: `${VAR}`/`$VAR`, `%(name)s`, `${{ ... }}`. Deployment-env
   namespaces (issue #165, a later extension of this same machinery): a
   `dockerfile`-format artifact's `ENV` directives mint namespace `env` (so
   `os.environ`/`os.getenv` reads bind to them) while its `ARG` directives
   mint namespace `dockerfile` (build-time only, not env-detector-bindable);
   a `yaml`-format artifact
   additionally dual-mints namespace `env` keys for recognized compose
   (`services.*.environment`) and k8s (`...env[].name`/`.value`) shapes,
   alongside the plain dotted-path `yaml` mint of the same leaves.
4. **Placement: nested.** `PyArtifact.config_keys: List[PyConfigKey]` —
   containment mirrors `DEFINES_CONFIG`; L1 data, identical at every level.
5. **Overlay posture.** Parse failure never drops the artifact node; it sets
   the artifact's `extraction: "partial"`. Extraction reads full on-disk text
   (cap-immune, same decoupling as dependency manifests).

## Model

`PyConfigKey` (model name follows `PyArtifact` precedent; label stays neutral):

| field | type | notes |
| --- | --- | --- |
| `id` | str | `<artifact-id>@key/<dotted.key>` |
| `key` | str | dotted path; numeric segments for arrays (`services.web.ports.0`) |
| `namespace` | str | `env` \| `yaml` \| `json` \| `toml` \| `ini` \| `properties` \| `dockerfile` |
| `value` | Optional[str] | only when text capture on |
| `span` | Span | into the artifact's source |
| `references` | List[str] | raw recognized tokens |

## Extraction

New `codeanalyzer/artifacts/config_keys.py`, invoked in `core.analyze()`
beside `build_dependency_view`: flatteners over tomllib/yaml/json/configparser
plus env/properties line parsers; deterministic (sorted keys); spans from the
source text; value extraction obeys `options.artifact_text`.

## Neo4j

`ConfigKey` label (merge key `id`; props key, namespace, value?, references,
start_line, end_line) + `DEFINES_CONFIG` rel; uniqueness constraint (DDL
asset inherits it); conformance fixture grows a config-bearing artifact.
`SCHEMA_VERSION` stays `2.0.0` (no consumers yet — reconciliation decision).

## Riders (bounded, same PR)

- `*.tf` discovery rule → new role `iac`, capture-only (no HCL extraction).
- `PyDependency.ecosystem: str = "pypi"` (SDK symmetry with purl).
- `skip_tests` deliberately NOT wired into the inventory — artifacts under
  test dirs are signal; decision recorded here rather than silently
  diverging from #152.

## Next unit in this train: config_use (#162)

`config_use` is a standard feature of the artifact layer landing next: `PY_USES_CONFIG` (body node → ConfigKey) needs `PyCallArgument.value`
(#152's own boundary). ConfigKey ids minted here are its resolution target.

## Caveats

- Multi-line/huge values (yaml block scalars) are captured as parsed; no
  per-value cap in v1 — revisit with payload evidence.
- `references[]` is recognition, not resolution: tokens are recorded raw;
  cross-artifact joins are the consumer's query (see analyses.md examples).
- env-family files are `format: "text"` with role `env`; namespace `env` is
  keyed off the basename rule, not the format.
- Literal dotted keys are indistinguishable from nesting once flattened into
  a dotted-path id (a top-level key literally named `"a.b"` and a nested
  `a: {b: ...}` both flatten to `a.b`); colliding forms coalesce last-wins
  by design, same as a plain duplicate key within one format.
- Strict configparser: a duplicate option within a single ini section causes
  configparser to raise ConfigParserError, downgrading the entire file to
  zero keys and extraction `"partial"` (unlike env/properties which use
  last-wins semantics).
- Empty collections (yaml/json/toml arrays or objects with no elements) yield
  no ConfigKey entries — a `logging.handlers: []` contributes no keys to the
  flattened result.

## Definition of done

- Per-format flattener tests (nesting, arrays, quoting, interpolation,
  escapes); reference extraction for all three syntaxes; value-gating test
  (`--no-artifact-text` → keys/spans/references identical, values absent);
  corrupted config → artifact kept, `extraction: "partial"`; span slices
  reproduce values from source.
- e2e fixture grows `.env`, `settings.yml`, `app.properties`; level-invariance
  and determinism gates extended; full suite green.
- Neo4j: label+rel in catalog, `--emit schema` reflects them, conformance
  fixture exercises them; skill vocabulary/analyses updated on the #161 line.
- One PR closing #152, stacked on `feat/issue-157-artifacts-dependencies`.
