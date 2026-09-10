# JSON == Graph: the Neo4j projection carries the same facts as the payload

**Date:** 2026-09-10
**Status:** Approved (design dialogue in-session)
**Scope:** codeanalyzer-python, Neo4j projection only, schema v2 additive; closes #202 and #203
**Builds on:** `can://` prefix scoping and `_module` retirement (#173, `.github#50`);
call-site/body convergence (`call-site-body-convergence.md`, #120)
**Sibling halves:** codeanalyzer-java#255/#256, codeanalyzer-typescript#201/#202 — same
defect class, each on its own clock

## Problem

The two projections disagree about what they carry, and the graph is the one that
is wrong. `analysis.json` carries the module's whole-file `source`, byte offsets on
every span, decorator positions, call-site `callee_signature`, literal-evaluated
variable `value`, and import positions. The Neo4j projection drops each of them,
while keeping `code` on `:PyClass`/`:PyCallable` — a *derivation* of the source the
graph does not have.

Net effect: nothing narrower than a callable resolves to text on the graph backend.
`python-sdk` documents living with it (`cldk/analysis/python/neo4j/neo4j_backend.py`,
"Projection-lossy fields").

The invariant being restored: **re-expressing a JSON field as an edge or a flattened
property is fine — silently dropping one is not.** `arguments_json` and
`keyword_arguments_json` are the good precedent; a joined `docstring` string is the
bad one.

## Locked decisions

1. **`:PyModule` gains `source`**, the whole file, always present — an empty file
   yields `""`, so "not carried" is never confusable with "empty". The projector
   already holds the module source at this point (`_span_code`, `project.py:763`).

2. **`_SPAN` gains `start_column`, `end_column`, `start_byte`, `end_byte`** beside
   the existing line pair, spread on all six labels that use it (`:PyClass`,
   `:PyCallable`, `:PyAttribute`, `:PyVariable`, `:PyBodyNode`, `:ConfigKey`).
   **These four spellings are adopted verbatim** from codeanalyzer-java#255, which
   coined them; a term coined twice is permanently wrong under the parity clause.
   Java has not landed them yet (`start_byte` appears nowhere in that repo at time
   of writing) — adoption follows the decision, not the merge order.

3. **Byte offsets are computed at projection time where the JSON model has no
   `Span`.** `PyClassAttribute`, `PyVariableDeclaration` and `PyConfigKey` carry
   flat line/column only. `byte_offsets()` (`schema/py_schema.py:104`) already
   converts ast positions to utf-8 offsets and the module source is in hand, so all
   four properties are populated uniformly rather than present on some labels and
   pruned on others. `_SPAN` keeps meaning one thing.

4. **`callee_signature` reaches `:PyBodyNode` by a projection-time join** from
   `PyCallable.call_sites`, keyed on `(start_line, start_column)`. `BodyNode` does
   not carry the field in JSON — `PyCallsite` does — and neither issue's scope
   admits a JSON change. The join is asserted per call site by the conformance
   test, not by presence, because a positional join can silently miss.
   Traversal (`PY_RESOLVES_TO` → `:PyCallable.signature`) is not sufficient: 20-28%
   of call sites never resolve a callee (`py_schema.py:130`), which is exactly where
   the field carries information nothing else has.

5. **`argument_types` is not carried, by decision.** `py_schema.py:311` records it
   as the legacy field that "mixed these two vocabularies in one list", superseded
   by `PyCallArgument{ast_kind, inferred_type}` (#86) — which the graph already
   carries as `arguments_json`. Carrying it would re-import a vocabulary this repo
   deliberately split and store the same fact twice in two shapes. The goal in #203
   is satisfied by the successor field; recorded here so it is not rediscovered as
   a gap.

6. **`:PyVariable` gains `value_json`**, always JSON-encoded. `value` is
   `Optional[Any]` and Neo4j properties are scalars or scalar arrays, so a dict or
   list needs a serialization rather than a raw put. One shape for every value
   (`json.loads` to recover), following the `arguments_json` precedent. `initializer`
   stays the raw source text; `value_json` is the literal-evaluated result.

7. **The decorator's span rides on `PY_DECORATED_BY`, not on `:PyDecorator`.**
   Forced, not chosen: `:PyDecorator` is merged on the resolved `qualified_name` and
   carries no `_module`, so it is never pruned — anything application-specific on it
   accumulates across every project in the database (`project.py:712-724`). The span
   joins `expression` and the argument properties, which already ride the
   relationship for that reason. `PyDecorator.span` is a real `Span`, so all six
   properties are available.

8. **`PY_IMPORTS` gains `positions_json`, keyed by spelling.** The edge is
   pre-aggregated — one edge per `(module, target)`, many statements collapsed onto
   `spellings` — because the writer MERGEs on `(type, from, to)` and a second row
   would overwrite rather than add. Parallel position arrays are therefore unsafe:
   `spellings` is emitted `sorted()`, so index alignment is already lost and any
   future edit to the sort would break it silently. Keying on the spelling survives
   both aggregation and sorting.

9. **Neither version moves.** Payload `schema_version` and graph `SCHEMA_VERSION`
   both stay `2.0.0`, per the standing 2026-09-07 ruling (all three analyzers) that
   holds them until the 2.0.0 line leaves release-candidate. Consumers gate on the
   **analyzer version** — the python-sdk Neo4j backends carry an analyzer floor and
   refuse anything below it at attach — and that floor moves with this release.

   `neo4j/schema.py:27` promises "MINOR on an additive change (new label/rel/property)".
   This change is additive and does not bump, so that note is amended in the same PR
   to record the hold and its scope. A file must not document a rule it breaks.

   Both issues cite `.github#50` for this hold. That is a miscitation — #50 mandates
   the *opposite* ("MAJOR graph-contract bump: a property is removed") for the
   `_module` retirement, whose python leg is #173, already closed, and whose bump the
   same 2026-09-07 ruling waived. The hold is real; only its source was wrong.

10. **One snapshot, two copies, both regenerated.** `schema.neo4j.json` is
    regenerated by `canpy --emit schema` and asserted current by
    `test/test_neo4j_schema.py`. `docs/handoff/schema.neo4j.json` is a second copy
    that no test guards and that **already differs from the root snapshot before
    this change**. Both are regenerated and a test asserts they match, so the
    handoff bundle stays self-contained without drifting again.

## Excluded: comment parity

#203's comment goals are **dropped, not deferred.** codeanalyzer-java#257 closed
`NOT_PLANNED` on 2026-09-10: comments are globally ignored across the analyzers, the
ceiling is accepted rather than deferred, and `docstring` on the graph is where the
comment model stops. That issue's closing comment names this repo's #203 explicitly
as dropping its comment goals for the same reason.

Consequences, recorded so they are not rediscovered:

- The `JSON == Graph` invariant has exactly one carved-out exception, made
  explicitly rather than by omission.
- `python-sdk`'s `get_all_comments` / `get_comment_in_file` raising on the Neo4j
  backend is the permanent answer for that backend, not a workaround awaiting this
  work. The in-memory backend remains the way to reach comments.
- Module-level comments, non-docstring comments and comment positions stay absent
  from the graph. Reopen only if a consumer turns up that needs them.

## Scope boundary

Restores facts on this analyzer's Neo4j projection only.

Does **not** touch the JSON projection, which is already correct — decision 4 is a
projection-time join precisely to keep that true. Does **not** drop `PyCallable.code`
or `PyClass.code`, which become derivable once `source` lands but which `python-sdk`
reads; removing them is a separate breaking change. Does **not** change
`python-sdk`. Does **not** move either version (decision 9). Does **not** carry
comments (above).

## Tracking and release plan

- **One PR closes both #202 and #203.** Same three files (`neo4j/schema.py`,
  `neo4j/project.py`, `neo4j/rows.py`), one snapshot regeneration, one pass over the
  conformance test. Split across two PRs, the snapshot is regenerated twice and the
  same test file edited twice for one sweep. Granularity follows PR granularity, so
  the tracking record is the two existing issues — no epic.
- **#203's comment goals are struck on the issue** with the java#257 citation, and
  the `.github#50` miscitation is corrected on both.
- **Ships as 1.5.2, alone.** No lockstep, matching #50's own precedent that each
  analyzer changes its own writers and cuts its own release.
- **`python-sdk` follow-up is filed just-in-time** after 1.5.2 is on PyPI: its
  "projection-lossy fields" note becomes wrong for everything except comments, and
  its analyzer floor moves. Detection is by analyzer version, not schema version
  (decision 9).
- **Siblings adopt decisions 6 and 8 as new shared vocabulary** (`value_json`,
  `positions_json`); the four span names of decision 2 are already java's. Recorded
  as comments on codeanalyzer-java#256 and codeanalyzer-typescript#202 rather than
  coordinated in an epic, since no analyzer reads another's nodes.

## Definition of done

- Every module node carries `source`, and each one's hash matches the
  `content_hash` that same node declares — proof the value survived serialization
  rather than merely being present.
- `source[start_byte:end_byte]` equals the node's `code` property byte-for-byte for
  every callable where both exist, zero mismatches — proof the offsets are real and
  not merely declared.
- For every `_SPAN`-bearing label, all six properties are present on every node,
  and the byte pair slices the module source back to that node's own text.
- Every resolved call site's `callee_signature` on `:PyBodyNode` equals the value on
  the matching `PyCallsite`, asserted per call site.
- `:PyVariable.value_json` round-trips through `json.loads` to the JSON `value` for
  every variable that has one.
- `PY_DECORATED_BY` carries the span of the decorator application it represents;
  `PY_IMPORTS.positions_json` carries a position for every spelling on the edge.
- Existing `docstring` values are unchanged on every node that had one.
- Emitted schema matches both checked-in snapshots, and the two snapshots match
  each other.
- Existing suite green.
