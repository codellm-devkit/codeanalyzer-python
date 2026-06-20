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

"""The declarative Neo4j schema catalog — the single in-repo source of truth for
the graph contract (node labels, their keys and typed properties, relationship
types and their endpoints). ``--emit schema`` serializes this (with the DDL from
:mod:`codeanalyzer.neo4j.schema`) to a machine-readable ``schema.json``, and the
conformance test (``test/test_neo4j_schema.py``) asserts the real emitter never
produces a label / relationship / property that isn't declared here — so this
file cannot silently drift from :mod:`codeanalyzer.neo4j.project`.

SCHEMA_VERSION is the contract version: bump MAJOR on a breaking change
(renamed/removed label, relationship or key), MINOR on an additive change (new
label/rel/property). It is stamped onto the ``:Application`` node of every
emitted graph so any consumer can detect a producer/consumer mismatch at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from codeanalyzer.neo4j.schema import CONSTRAINTS, INDEXES

SCHEMA_VERSION = "1.0.0"

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
        "Application",
        "Application",
        "name",
        {"name": "string", "schema_version": "string"},
    ),
    NodeLabel(
        "Module",
        "Module",
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
        "Class",
        "Symbol",
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
        "Callable",
        "Symbol",
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
        "External",
        "Symbol",
        "signature",
        {"signature": "string", "name": "string"},
    ),
    NodeLabel("Package", "Package", "name", {"name": "string"}),
    NodeLabel(
        "Decorator",
        "Decorator",
        "name",
        {"name": "string"},
    ),
    NodeLabel(
        "CallSite",
        "CallSite",
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
        "Attribute",
        "Attribute",
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
        "Variable",
        "Variable",
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
]

_DECL_TARGETS = ["Class", "Callable"]


REL_TYPES: List[RelType] = [
    RelType("HAS_MODULE", ["Application"], ["Module"]),
    RelType("DECLARES", ["Module", "Class", "Callable"], _DECL_TARGETS),
    RelType("HAS_METHOD", ["Class"], ["Callable"]),
    RelType("HAS_ATTRIBUTE", ["Class"], ["Attribute"]),
    RelType("DECLARES_VAR", ["Module", "Callable"], ["Variable"]),
    RelType("HAS_CALLSITE", ["Callable"], ["CallSite"]),
    RelType("RESOLVES_TO", ["CallSite"], ["Callable", "External"]),
    RelType(
        "CALLS",
        ["Callable", "External"],
        ["Callable", "External"],
        {"weight": "integer", "provenance": "string[]"},
    ),
    RelType("EXTENDS", ["Class"], ["Class"]),
    RelType(
        "IMPORTS",
        ["Module"],
        ["Package"],
        {"imported_names": "string[]", "aliases": "string[]"},
    ),
    RelType("DECORATED_BY", ["Callable"], ["Decorator"]),
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
