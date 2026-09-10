################################################################################
# Copyright IBM Corporation 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""
The declarative Neo4j schema — the single in-repo source of truth for the graph contract: node
labels with their keys and typed properties, relationship types and their endpoints, and the
Cypher DDL (uniqueness constraints + indexes). The constraints are DERIVED from the node labels
(one per distinct mergeLabel/key) so a new label brings its own constraint — there is no second
list to keep in sync. `--emit schema` serializes all of this to a machine-readable schema.json,
and the conformance test (``test/test_neo4j_schema.py``) asserts the real emitter never produces a
label / relationship / property that isn't declared here — so this file cannot silently drift
from :mod:`codeanalyzer.neo4j.project`.

SCHEMA_VERSION is the contract version: bump MAJOR on a breaking change (renamed/removed label,
relationship or key), MINOR on an additive change (new label/rel/property). It is stamped onto
the :PyApplication node of every emitted graph so any consumer can detect a producer/consumer
mismatch at runtime.

**The additive-MINOR rule is suspended for the 2.0.0 line.** Per the 2026-09-07 ruling (all three
analyzers), payload ``schema_version`` and this version both hold at ``2.0.0`` until the 2.0.0 line
leaves release-candidate, so the additive properties of #202/#203 ship without a bump and are
detectable only by presence. Consumers gate on the **analyzer version** instead -- the python-sdk
Neo4j backends carry an analyzer floor and refuse anything below it at attach. The rule above
resumes at the coordinated re-baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

SCHEMA_VERSION = "2.0.0"

# PropType ∈ {"string", "integer", "float", "boolean", "string[]", "integer[]"}.


@dataclass
class NodeLabel:
    label: str  # the specific label (also the catalog key)
    merge_label: str  # the label the uniqueness constraint / MERGE is on
    key: str
    properties: Dict[str, str]


@dataclass
class RelType:
    type: str
    from_labels: List[str]
    to_labels: List[str]
    properties: Dict[str, str] = field(default_factory=dict)


# Labels layered onto a node in addition to its primary/specific label.
# ``PyCanNode`` (#173) rides every node keyed by a ``can://`` id — an index
# anchor for the prefix-scoped destructive statements (see ``rows.CAN_NODE``).
MARKER_LABELS: List[str] = ["PyCanNode"]

# The flattened span. ``start_column``/``end_column``/``start_byte``/``end_byte``
# are adopted **verbatim** from codeanalyzer-java#255, which coined them: a term
# coined twice is permanently wrong under the cross-language parity clause. The
# byte pair makes every span sliceable out of :PyModule.source -- for the labels
# whose JSON model carries flat ast positions and no ``Span`` (:PyAttribute,
# :PyVariable) the projector computes it, so this dict means one thing on every
# label that spreads it (#202).
_SPAN = {
    "start_line": "integer", "end_line": "integer",
    "start_column": "integer", "end_column": "integer",
    "start_byte": "integer", "end_byte": "integer",
}


NODE_LABELS: List[NodeLabel] = [
    NodeLabel(
        "PyApplication",
        "PyApplication",
        "id",
        {
            "id": "string",
            "name": "string",
            "schema_version": "string",
            "analyzer_name": "string",
            "analyzer_version": "string",
            "repo_uri": "string",
            "source_revision": "string",
            "repo_dirty": "boolean",
            "entrypoint_frameworks": "string[]",
            "entrypoint_report_json": "string",
        },
    ),
    NodeLabel(
        "PyModule",
        "PyModule",
        "id",
        {
            "id": "string",
            "file_key": "string",
            "module_name": "string",
            # The primary text: schema v2 stores source once per module and every
            # narrower node's text is a byte slice of it. Always present -- an empty
            # file yields "", so "not carried" is never confusable with "empty" (#202).
            "source": "string",
            "content_hash": "string",
            "last_modified": "float",
            "file_size": "integer",
        },
    ),
    NodeLabel(
        "PyClass",
        "PySymbol",
        "id",
        {
            "id": "string",
            "signature": "string",
            "name": "string",
            "code": "string",
            "base_classes": "string[]",
            "decorators": "string[]",
            "docstring": "string",
            **_SPAN,
            "is_entrypoint": "boolean",
            "entrypoint_frameworks": "string[]",
        },
    ),
    NodeLabel(
        "PyCallable",
        "PySymbol",
        "id",
        {
            "id": "string",
            "signature": "string",
            "name": "string",
            "path": "string",
            "return_type": "string",
            "cyclomatic_complexity": "integer",
            "code": "string",
            "code_start_line": "integer",
            **_SPAN,
            "docstring": "string",
            "decorators": "string[]",
            "modifiers": "string[]",
            "parameters_json": "string",
            "accessed_symbols_json": "string",
            "is_entrypoint": "boolean",
            "entrypoint_frameworks": "string[]",
        },
    ),
    NodeLabel(
        "PyExternal",
        "PySymbol",
        "id",
        {"id": "string", "name": "string", "module": "string"},
    ),
    NodeLabel("PyPackage", "PyPackage", "name", {"name": "string"}),
    NodeLabel(
        "PyDecorator",
        "PyDecorator",
        "name",
        {"name": "string", "qualified_name": "string"},
    ),
    NodeLabel(
        "PyAttribute",
        "PyAttribute",
        "id",
        {
            "id": "string",
            "name": "string",
            "type": "string",
            "initializer": "string",
            "docstring": "string",
            # The one _SPAN exception: ``PyClassAttribute`` carries start/end LINE only
            # -- no columns, hence no derivable byte offsets. Emitting a fabricated
            # column 0 would make the span unsliceable while claiming otherwise, so the
            # line pair is declared honestly instead. Filed for the JSON model to gain
            # columns; until then this label is line-granular (#203).
            "start_line": "integer",
            "end_line": "integer",
        },
    ),
    NodeLabel(
        "PyVariable",
        "PyVariable",
        "id",
        {
            "id": "string",
            "name": "string",
            "type": "string",
            "initializer": "string",
            # ``PyVariableDeclaration.value`` -- the literal-evaluated result, always
            # JSON-encoded because it is ``Optional[Any]`` and a Neo4j property is a
            # scalar or an array of scalars (the ``arguments_json`` precedent, #203).
            # ``initializer`` stays the raw source text.
            "value_json": "string",
            "scope": "string",
            **_SPAN,
        },
    ),
    # Level-3 CPG overlay (present only at -a 3). The dataflow vocabulary is
    # shared cross-language in *shape* (same suffixes, props, semantics) but
    # namespaced per language like every other row family — a multi-language
    # Neo4j database must never mingle one analyzer's dependence edges with
    # another's. `id` = "<signature>#<node_id>"; parameter-passing nodes
    # (formal/actual in/out) ride the same label with `var`/`call_node`.
    NodeLabel(
        "PyBodyNode",
        "PyBodyNode",
        "id",
        {
            "id": "string",
            "kind": "string",
            "var": "string",
            "call_node": "string",
            # Call-site detail (#120): the graph emits one node per call site,
            # matching analysis.json, instead of a separate :PyCallSite.
            "method_name": "string",
            "receiver_expr": "string",
            "receiver_type": "string",
            "return_type": "string",
            "is_constructor_call": "boolean",
            "arguments_json": "string",
            # What distinguishes overload targets at a resolved call site. Joined
            # from ``PyCallable.call_sites`` at projection time, since ``BodyNode``
            # does not carry it in JSON (#203). ``argument_types`` is deliberately
            # NOT here: it is the legacy field #86 split into ``PyCallArgument``,
            # already carried as ``arguments_json``.
            "callee_signature": "string",
            **_SPAN,
        },
    ),
    # Neutral artifact/dependency subgraph (spec 2026-08-27, Task 6). No `Py`
    # prefix -- deliberate: `Artifact`/`Package` are cross-language merge
    # targets, so a sibling-language analyzer over the same repo lands on the
    # same nodes instead of a per-language duplicate. `PY_PROVIDES` /
    # `PY_UNRESOLVED_IMPORT` stay PY_-namespaced (this analyzer's own claim
    # about what an import resolves to) and target `:PyExternal`.
    NodeLabel("Artifact", "Artifact", "id", {
        "id": "string", "path": "string", "format": "string",
        "roles": "string[]", "size_bytes": "integer", "sha256": "string",
        "source": "string", "extraction": "string",
    }),
    NodeLabel("Package", "Package", "id", {
        "id": "string", "ecosystem": "string", "name": "string",
    }),
    # A configuration key flattened out of a config-bearing Artifact (#152).
    # Neutral vocabulary like Artifact/Package -- a yaml/env/ini key is not a
    # Python concept. `value` is omitted (not null) when the source model's
    # value is None (--no-artifact-text, or a namespace with no value at that
    # path); `references` is always present, possibly empty.
    NodeLabel("ConfigKey", "ConfigKey", "id", {
        "id": "string", "key": "string", "namespace": "string",
        "value": "string", "references": "string[]",
        **_SPAN,
    }),
]

_DECL_TARGETS = ["PyClass", "PyCallable"]


REL_TYPES: List[RelType] = [
    RelType("PY_HAS_MODULE", ["PyApplication"], ["PyModule"]),
    RelType("PY_DECLARES", ["PyModule", "PyClass", "PyCallable"], _DECL_TARGETS),
    RelType("PY_HAS_METHOD", ["PyClass"], ["PyCallable"]),
    RelType("PY_HAS_ATTRIBUTE", ["PyClass"], ["PyAttribute"]),
    RelType("PY_DECLARES_VAR", ["PyModule", "PyCallable"], ["PyVariable"]),
    RelType("PY_RESOLVES_TO", ["PyBodyNode"], ["PyCallable", "PyExternal"]),
    RelType(
        "PY_CALLS",
        ["PyCallable", "PyExternal"],
        ["PyCallable", "PyExternal"],
        {"weight": "integer", "prov": "string[]"},
    ),
    RelType("PY_EXTENDS", ["PyClass"], ["PyClass"]),
    RelType(
        "PY_IMPORTS",
        ["PyModule"],
        ["PyModule", "PyPackage"],
        # ``positions_json`` keys on the spelling, not on an index: this edge
        # pre-aggregates per (module, target) and emits ``spellings`` sorted, so
        # parallel position arrays have already lost their alignment (#203).
        {"spellings": "string[]", "imported_names": "string[]", "aliases": "string[]",
         "positions_json": "string"},
    ),
    RelType(
        "PY_DECORATED_BY",
        ["PyCallable", "PyClass"],
        ["PyDecorator"],
        # The span rides here, not on :PyDecorator: that node is merged on the
        # resolved ``qualified_name`` and carries no ``_module``, so it is never
        # pruned and any per-application fact on it would accumulate across every
        # project in the database -- the same reason ``expression`` and the
        # arguments already ride the relationship (#203).
        {
            "expression": "string",
            "positional_arguments": "string[]",
            "keyword_arguments_json": "string",
            **_SPAN,
        },
    ),
    # Level-3 CPG overlay (-a 3 only): the cross-language dataflow vocabulary,
    # PY_-namespaced so per-language SDK backends can scope their queries.
    RelType("PY_HAS_BODY_NODE", ["PyCallable"], ["PyBodyNode"]),
    # ``_k`` is the relationship-identity discriminant (internal, underscore-
    # prefixed like ``_module``): PY_CFG_NEXT merges per ``kind`` (a conditional's
    # true/false pair), PY_DDG per ``(var, prov)`` (one dependence per variable,
    # and the ssa/points-to split) — a plain endpoint-pair MERGE would collapse
    # legitimately-distinct edges.
    RelType("PY_CFG_NEXT", ["PyBodyNode"], ["PyBodyNode"], {"kind": "string", "_k": "string"}),
    RelType("PY_CDG", ["PyBodyNode"], ["PyBodyNode"]),
    RelType("PY_DDG", ["PyBodyNode"], ["PyBodyNode"], {"var": "string", "prov": "string[]", "_k": "string"}),
    RelType("PY_PARAM_IN", ["PyBodyNode"], ["PyBodyNode"], {"var": "string"}),
    RelType("PY_PARAM_OUT", ["PyBodyNode"], ["PyBodyNode"], {"var": "string"}),
    RelType("PY_SUMMARY", ["PyBodyNode"], ["PyBodyNode"]),
    # Neutral artifact/dependency subgraph (Task 6).
    RelType("HAS_ARTIFACT", ["PyApplication"], ["Artifact"]),
    # A config key nests under exactly one owning artifact (its id is
    # `<artifact-id>@key/<dotted.key>`) -- a plain containment edge, no
    # per-edge properties or discriminant needed (#152).
    RelType("DEFINES_CONFIG", ["Artifact"], ["ConfigKey"]),
    # ``_k`` (merges per ``kind``): the same manifest may declare one package
    # twice under different kinds (e.g. a runtime dep re-listed under an
    # optional extra) -- same endpoint pair, so without the discriminant the
    # plain MERGE collapses the two declarations into one row.
    RelType("DECLARES_DEPENDENCY", ["Artifact"], ["Package"], {
        "spec": "string", "kind": "string", "extras": "string[]", "prov": "string[]",
        "direct": "boolean", "_k": "string",
    }),
    RelType("LOCKS", ["Artifact"], ["Package"], {"version": "string"}),
    RelType("PY_PROVIDES", ["Package"], ["PyExternal"]),
    RelType("PY_UNRESOLVED_IMPORT", ["PyApplication"], ["PyExternal"], {"prov": "string[]"}),
    # config_use (#162): the resolved-read bridge from a call site's body node
    # to the PyConfigKey it reads. `src`/`dst` are already GLOBAL ordinal /
    # ConfigKey ids (resolved upstream by `resolve_uses`), so no discriminant
    # is needed -- one call site reads one key per edge.
    RelType("PY_USES_CONFIG", ["PyBodyNode"], ["ConfigKey"], {"prov": "string[]"}),
    # A detector-matched read that never closed on exactly one declared key --
    # first-class per #162, PyApplication -> PyExternal ghost of the callee
    # (mirrors PY_UNRESOLVED_IMPORT's shape). `_k` discriminates by (key,
    # reason): the same external callee (e.g. `os.getenv`) legitimately reads
    # several distinct undeclared/dynamic keys across a codebase -- without a
    # discriminant a plain endpoint-pair MERGE would collapse those onto one
    # relationship and silently drop every key but the last one SET.
    RelType(
        "PY_READS_CONFIG_UNRESOLVED", ["PyApplication"], ["PyExternal"],
        {"key": "string", "reason": "string", "prov": "string[]", "_k": "string"},
    ),
]


def uniqueness_constraints() -> list[str]:
    """One uniqueness constraint per distinct (merge_label, key)."""
    seen: set[tuple[str, str]] = set()
    out: list[str] = []

    for node in NODE_LABELS:
        identifier = (node.merge_label, node.key)
        if identifier in seen:
            continue

        seen.add(identifier)
        out.append(
            f"CREATE CONSTRAINT {node.merge_label.lower()}_{node.key} "
            f"IF NOT EXISTS FOR (x:{node.merge_label}) "
            f"REQUIRE x.{node.key} IS UNIQUE"
        )

    return out


CONSTRAINTS: List[str] = uniqueness_constraints()

INDEXES: List[str] = [
    "CREATE INDEX py_callable_name IF NOT EXISTS FOR (c:PyCallable) ON (c.name)",
    "CREATE INDEX py_class_name IF NOT EXISTS FOR (c:PyClass) ON (c.name)",
    "CREATE FULLTEXT INDEX py_code_fts IF NOT EXISTS FOR (c:PyCallable) ON EACH [c.code, c.docstring]",
    # #173: every destructive statement is ``MATCH (x:PyCanNode) WHERE x.id STARTS WITH $p``.
    # A range index on the marker makes that a prefix seek; without it, a store scan per
    # changed module. STARTS WITH is index-backed; CONTAINS / ENDS WITH are not.
    "CREATE INDEX py_can_node_id IF NOT EXISTS FOR (n:PyCanNode) ON (n.id)",
]


@dataclass
class SchemaDocument:
    schema_version: str
    generator: str
    marker_labels: List[str]
    node_labels: List[NodeLabel]
    relationship_types: List[RelType]
    constraints: List[str]
    indexes: List[str]


def build_schema_document() -> dict:
    """Build the full machine-readable schema document emitted by ``--emit schema``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": "codeanalyzer-python",
        "marker_labels": list(MARKER_LABELS),
        "node_labels": [
            {
                "label": n.label,
                "merge_label": n.merge_label,
                "key": n.key,
                "properties": n.properties,
            }
            for n in NODE_LABELS
        ],
        "relationship_types": [
            {
                "type": r.type,
                "from": r.from_labels,
                "to": r.to_labels,
                "properties": r.properties,
            }
            for r in REL_TYPES
        ],
        "constraints": list(CONSTRAINTS),
        "indexes": list(INDEXES),
    }
