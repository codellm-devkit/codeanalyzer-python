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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

SCHEMA_VERSION = "1.2.0"

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
MARKER_LABELS: List[str] = []

_SPAN = {"start_line": "integer", "end_line": "integer"}


NODE_LABELS: List[NodeLabel] = [
    NodeLabel(
        "PyApplication",
        "PyApplication",
        "name",
        {"name": "string", "schema_version": "string"},
    ),
    NodeLabel(
        "PyModule",
        "PyModule",
        "file_key",
        {
            "file_key": "string",
            "module_name": "string",
            "content_hash": "string",
            "last_modified": "float",
            "file_size": "integer",
            "_module": "string",
        },
    ),
    NodeLabel(
        "PyClass",
        "PySymbol",
        "signature",
        {
            "signature": "string",
            "name": "string",
            "code": "string",
            "base_classes": "string[]",
            "docstring": "string",
            **_SPAN,
            "_module": "string",
        },
    ),
    NodeLabel(
        "PyCallable",
        "PySymbol",
        "signature",
        {
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
            "parameters_json": "string",
            "accessed_symbols_json": "string",
            "_module": "string",
        },
    ),
    NodeLabel(
        "PyExternal",
        "PySymbol",
        "signature",
        {"signature": "string", "name": "string", "module": "string"},
    ),
    NodeLabel("PyPackage", "PyPackage", "name", {"name": "string"}),
    NodeLabel(
        "PyDecorator",
        "PyDecorator",
        "name",
        {"name": "string"},
    ),
    NodeLabel(
        "PyCallSite",
        "PyCallSite",
        "id",
        {
            "id": "string",
            "method_name": "string",
            "receiver_expr": "string",
            "receiver_type": "string",
            "argument_types": "string[]",
            "return_type": "string",
            "callee_signature": "string",
            "is_constructor_call": "boolean",
            "start_line": "integer",
            "start_column": "integer",
            "end_line": "integer",
            "end_column": "integer",
            "_module": "string",
        },
    ),
    NodeLabel(
        "PyAttribute",
        "PyAttribute",
        "id",
        {
            "id": "string",
            "name": "string",
            "type": "string",
            "docstring": "string",
            **_SPAN,
            "_module": "string",
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
            "scope": "string",
            **_SPAN,
            "_module": "string",
        },
    ),
    # Level-3 CPG overlay (present only at -a 3). The label and edge types
    # below are the shared cross-language dataflow vocabulary — deliberately
    # NOT PY_-prefixed. `id` = "<signature>#<node_id>"; parameter-passing
    # nodes (formal/actual in/out) ride the same label with `var`/`call_node`.
    NodeLabel(
        "CFGNode",
        "CFGNode",
        "id",
        {
            "id": "string",
            "kind": "string",
            "var": "string",
            "call_node": "integer",
            **_SPAN,
            "_module": "string",
        },
    ),
]

_DECL_TARGETS = ["PyClass", "PyCallable"]


REL_TYPES: List[RelType] = [
    RelType("PY_HAS_MODULE", ["PyApplication"], ["PyModule"]),
    RelType("PY_DECLARES", ["PyModule", "PyClass", "PyCallable"], _DECL_TARGETS),
    RelType("PY_HAS_METHOD", ["PyClass"], ["PyCallable"]),
    RelType("PY_HAS_ATTRIBUTE", ["PyClass"], ["PyAttribute"]),
    RelType("PY_DECLARES_VAR", ["PyModule", "PyCallable"], ["PyVariable"]),
    RelType("PY_HAS_CALLSITE", ["PyCallable"], ["PyCallSite"]),
    RelType("PY_RESOLVES_TO", ["PyCallSite"], ["PyCallable", "PyExternal"]),
    RelType(
        "PY_CALLS",
        ["PyCallable", "PyExternal"],
        ["PyCallable", "PyExternal"],
        {"weight": "integer", "provenance": "string[]"},
    ),
    RelType("PY_EXTENDS", ["PyClass"], ["PyClass"]),
    RelType(
        "PY_IMPORTS",
        ["PyModule"],
        ["PyPackage"],
        {"imported_names": "string[]", "aliases": "string[]"},
    ),
    RelType("PY_DECORATED_BY", ["PyCallable"], ["PyDecorator"]),
    # Level-3 CPG overlay (shared cross-language vocabulary, -a 3 only).
    RelType("HAS_CFG_NODE", ["PyCallable"], ["CFGNode"]),
    RelType("CFG_NEXT", ["CFGNode"], ["CFGNode"], {"kind": "string"}),
    RelType("CDG", ["CFGNode"], ["CFGNode"]),
    RelType("DDG", ["CFGNode"], ["CFGNode"], {"var": "string"}),
    RelType("PARAM_IN", ["CFGNode"], ["CFGNode"], {"var": "string"}),
    RelType("PARAM_OUT", ["CFGNode"], ["CFGNode"], {"var": "string"}),
    RelType("SUMMARY", ["CFGNode"], ["CFGNode"]),
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
