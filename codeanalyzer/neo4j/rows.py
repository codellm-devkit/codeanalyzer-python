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

"""The output-agnostic intermediate between :func:`project` and the two writers
(cypher snapshot / bolt incremental). Pure data — no I/O, no driver. A
:class:`GraphRows` is a deterministic, deduped bag of nodes and edges that both
writers consume identically.

Property values are restricted to Neo4j-legal shapes: primitives and homogeneous
arrays of primitives. ``None`` values are pruned (in Neo4j a null property is
simply absence).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

# A property value: a primitive, or a homogeneous list of primitives.
Scalar = Union[str, int, float, bool]
Prop = Union[Scalar, List[str], List[int], List[float], List[bool]]
Props = Dict[str, Prop]


@dataclass(frozen=True)
class NodeRef:
    """How an edge addresses one of its endpoints: the label + key property to
    MATCH on, and the value."""

    label: str  # the label carrying the uniqueness constraint (e.g. "Symbol", "Module")
    key_prop: str  # "signature" | "file_key" | "name" | "id"
    value: str


@dataclass
class NodeRow:
    labels: List[str]  # labels[0] is the constrained MERGE label; the rest are SET as extra labels
    key_prop: str
    value: str
    props: Props


@dataclass
class EdgeRow:
    type: str
    from_ref: NodeRef
    to_ref: NodeRef
    props: Props


@dataclass
class GraphRows:
    nodes: List[NodeRow] = field(default_factory=list)
    edges: List[EdgeRow] = field(default_factory=list)


def prune(p: Dict[str, Optional[Prop]]) -> Props:
    """Drop ``None`` entries — in Neo4j a null property means "absent", so we
    never store one. Empty lists are kept (a present-but-empty array is legal)."""
    return {k: v for k, v in p.items() if v is not None}


class RowBuilder:
    """Accumulates nodes/edges with ``MERGE`` semantics in memory, so the same
    node touched many times (a hot external symbol, a canonical decorator)
    collapses to one row, and cross-reference edges to a target that never
    materialized are dropped (the "edge-only-when-resolved" rule).
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, NodeRow] = {}  # key: f"{labels[0]} {value}"
        self._edges: List[EdgeRow] = []
        self._deferred: List[EdgeRow] = []  # edges gated against node existence at finish()
        self._keys: set = set()  # every node value seen, for resolved-gating

    def node(self, labels: List[str], key_prop: str, value: str, props: Props) -> NodeRef:
        """Upsert a node. Re-seeing the same ``(labels[0], value)`` merges props
        (last write wins) and unions labels — the in-memory analog of
        ``MERGE (n:Label {key}) SET n += props``."""
        node_id = f"{labels[0]} {value}"
        existing = self._nodes.get(node_id)
        if existing is not None:
            existing.props.update(props)
            for label in labels:
                if label not in existing.labels:
                    existing.labels.append(label)
        else:
            self._nodes[node_id] = NodeRow(list(labels), key_prop, value, dict(props))
        self._keys.add(value)
        return NodeRef(labels[0], key_prop, value)

    def edge(self, type_: str, from_ref: NodeRef, to_ref: NodeRef, props: Optional[Props] = None) -> None:
        """An edge whose endpoints are known to exist (both ends emitted this run)."""
        self._edges.append(EdgeRow(type_, from_ref, to_ref, dict(props or {})))

    def edge_to_symbol(
        self, type_: str, from_ref: NodeRef, target_signature: str, props: Optional[Props] = None
    ) -> None:
        """An edge to a ``:Symbol`` target that may be external/library code not
        present in the graph. Deferred and kept only if the target signature was
        actually emitted as a node — so EXTENDS / RESOLVES_TO never dangle (the
        string fallback lives on the source node's props)."""
        self._deferred.append(
            EdgeRow(
                type_,
                from_ref,
                NodeRef("Symbol", "signature", target_signature),
                dict(props or {}),
            )
        )

    def has_key(self, value: str) -> bool:
        return value in self._keys

    def finish(self) -> GraphRows:
        for e in self._deferred:
            if e.to_ref.value in self._keys:
                self._edges.append(e)
        nodes = sorted(self._nodes.values(), key=lambda n: f"{n.labels[0]} {n.value}")
        edges = sorted(self._edges, key=lambda e: f"{e.type} {e.from_ref.value} {e.to_ref.value}")
        return GraphRows(nodes, edges)


# ----------------------------------------------------------------------------------------------
# Cypher literal rendering (used by the snapshot writer; the bolt writer passes params instead).
# ----------------------------------------------------------------------------------------------


def cypher_value(v: Prop) -> str:
    """Render a property value as a Cypher literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return _cypher_string(v)
    if isinstance(v, (int, float)):
        # bools are handled above; int/float fall through here.
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            return "null"
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, list):
        return "[" + ", ".join(cypher_value(x) for x in v) + "]"
    return "null"


def cypher_map(props: Props) -> str:
    """Render a props map as a Cypher map literal: ``{key: value, ...}``.
    Keys are valid identifiers."""
    return "{" + ", ".join(f"{k}: {cypher_value(v)}" for k, v in props.items()) + "}"


def _cypher_string(s: str) -> str:
    escaped = (
        s.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f"'{escaped}'"


def chunk(items: list, size: int) -> list:
    """Split a list into chunks of at most ``size`` (UNWIND batch sizing)."""
    return [items[i : i + size] for i in range(0, len(items), size)]
