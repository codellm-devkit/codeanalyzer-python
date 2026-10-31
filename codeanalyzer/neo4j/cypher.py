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

"""The snapshot writer: render :class:`GraphRows` to a self-contained ``.cypher``
script. Running it (e.g. ``cypher-shell < graph.cypher``) rebuilds this project's
subgraph from scratch — constraints, a scoped wipe of the prior version, then
batched ``UNWIND … MERGE`` for nodes and edges.

This artifact is intentionally NOT incremental: a static script has no view of
the live DB, so it expresses the full truth. Incremental updates are the bolt
writer's job.
"""
from __future__ import annotations

from typing import Dict, List

from codeanalyzer.neo4j.rows import (
    CAN_NODE,
    EdgeRow,
    GraphRows,
    NodeRow,
    application_prefix,
    chunk,
    cypher_map,
    cypher_value,
)
from codeanalyzer.neo4j.schema import CONSTRAINTS, INDEXES
from codeanalyzer.schema.ids import application_id

BATCH = 500


def render_cypher(rows: GraphRows, app_name: str, namespace: str | None = None) -> str:
    out: List[str] = []

    out.append("// ── constraints & indexes ──")
    for stmt in CONSTRAINTS:
        out.append(f"{stmt};")
    for stmt in INDEXES:
        out.append(f"{stmt};")

    out.append("")
    out.append("// ── wipe this project's prior subgraph (externals/packages/decorators are shared) ──")
    out.append(_wipe(app_name, namespace))

    out.append("")
    out.append("// ── nodes ──")
    out.extend(_node_statements(rows.nodes))

    out.append("")
    out.append("// ── relationships ──")
    out.extend(_edge_statements(rows.edges))

    out.append("")
    return "\n".join(out)


def _wipe(app_name: str, namespace: str | None = None) -> str:
    """The application root by equality plus everything under ``can://<app>/`` (#173).
    Scoped by id prefix, so it is one application by construction — a second python
    app sharing a module path, and a sibling analyzer's :Py* graph, are outside it.
    :Package nodes (``pkg:`` purls) stay outside too.

    Two things moved with the app-outermost grammar. The root is matched by its
    ``can://<app>`` id rather than by the free-text ``--app-name``, so two apps
    sharing a name no longer wipe each other's root; and the app's :Artifact /
    :ConfigKey nodes are now *inside* the prefix, so the snapshot rebuilds them
    instead of leaving them to accumulate. That is deliberate, and it is the one
    behavioural widening here: a cross-language edge into a shared :Artifact is
    dropped by a python snapshot and restored on that analyzer's next push."""
    prefix = cypher_value(application_prefix(app_name, namespace))
    app_id = cypher_value(application_id(app_name, namespace))
    return "\n".join(
        [
            f"MATCH (x:{CAN_NODE}) WHERE x.id = {app_id} OR x.id STARTS WITH {prefix}",
            "CALL { WITH x DETACH DELETE x } IN TRANSACTIONS OF 1000 ROWS;",
        ]
    )


# ----------------------------------------------------------------------------------------------
# Nodes — grouped by their full label set + key property, batched into UNWIND lists.
# ----------------------------------------------------------------------------------------------


def _node_statements(nodes: List[NodeRow]) -> List[str]:
    groups: Dict[str, List[NodeRow]] = {}
    for n in nodes:
        key = f"{':'.join(n.labels)}|{n.key_prop}"
        groups.setdefault(key, []).append(n)

    blocks: List[str] = []
    for group in groups.values():
        labels = group[0].labels
        key_prop = group[0].key_prop
        merge_label = labels[0]
        extra = labels[1:]
        set_labels = f", n:{':'.join(extra)}" if extra else ""
        for batch in chunk(group, BATCH):
            rows_lit = ",\n".join(
                f"  {{k: {cypher_value(n.value)}, p: {cypher_map(n.props)}}}" for n in batch
            )
            blocks.append(
                f"UNWIND [\n{rows_lit}\n] AS row\n"
                f"MERGE (n:{merge_label} {{{key_prop}: row.k}})\n"
                f"SET n += row.p{set_labels};"
            )
    return blocks


# ----------------------------------------------------------------------------------------------
# Edges — grouped by (type, endpoint labels + key props), batched.
# ----------------------------------------------------------------------------------------------


def _edge_statements(edges: List[EdgeRow]) -> List[str]:
    groups: Dict[str, List[EdgeRow]] = {}
    for e in edges:
        key = (
            f"{e.type}|{e.from_ref.label}.{e.from_ref.key_prop}"
            f"|{e.to_ref.label}.{e.to_ref.key_prop}|{e.key is not None}"
        )
        groups.setdefault(key, []).append(e)

    blocks: List[str] = []
    for group in groups.values():
        first = group[0]
        from_ref, to_ref = first.from_ref, first.to_ref
        # Discriminated relationships MERGE on ``{_k}`` — see EdgeRow.key.
        rel_key = " {_k: row.k}" if first.key is not None else ""
        for batch in chunk(group, BATCH):
            rows_lit = ",\n".join(
                f"  {{f: {cypher_value(e.from_ref.value)}, t: {cypher_value(e.to_ref.value)}, "
                + (f"k: {cypher_value(e.key)}, " if first.key is not None else "")
                + f"p: {cypher_map(e.props)}}}"
                for e in batch
            )
            blocks.append(
                f"UNWIND [\n{rows_lit}\n] AS row\n"
                f"MATCH (a:{from_ref.label} {{{from_ref.key_prop}: row.f}})\n"
                f"MATCH (b:{to_ref.label} {{{to_ref.key_prop}: row.t}})\n"
                f"MERGE (a)-[r:{first.type}{rel_key}]->(b)\n"
                f"SET r += row.p;"
            )
    return blocks


# ----------------------------------------------------------------------------------------------
# LOW-MEM PATCH: streaming snapshot writer (see write_cypher_streaming).
# ----------------------------------------------------------------------------------------------
from codeanalyzer.neo4j.rows import EdgeRow, GraphRows, NodeRef, RowBuilder, normalize_node  # noqa: E402


# ``edge_to_symbol`` targets are :PySymbol by contract (see RowBuilder), so pass 2
# gates on those keys only — not the millions of :PyBodyNode keys.
_GATED = frozenset({"PySymbol"})


class _NodeStreamBuilder(RowBuilder):
    """Pass 1: every node emission becomes one ``UNWIND`` row, rendered with
    ``_node_statements`` (same grouping/BATCH) the moment its batch fills; only
    the :data:`_GATED` keys are kept (see ``_EdgeStreamBuilder.edge_to_symbol``). ``MERGE … SET n += row.p, n:Extra`` replays RowBuilder's
    merge (last write wins, labels union) provided a re-emitted node's rows run
    in emission order, so a node still waiting in a batch flushes that batch first."""

    def __init__(self, fh):
        super().__init__()
        self._fh = fh
        self._pending: Dict[str, List[NodeRow]] = {}
        self._waiting: Dict[str, str] = {}  # node id -> group of its unwritten row

    def node(self, labels, key_prop, value, props):
        node_id = f"{labels[0]} {value}"
        labels, props, _module = normalize_node(labels, key_prop, value, props)
        if node_id in self._waiting:
            self._flush(self._waiting[node_id])
        g = f"{':'.join(labels)}|{key_prop}"
        batch = self._pending.setdefault(g, [])
        batch.append(NodeRow(list(labels), key_prop, value, props))
        self._waiting[node_id] = g
        if labels[0] in _GATED:
            self._keys.add((labels[0], value))
        if len(batch) >= BATCH:
            self._flush(g)
        return NodeRef(labels[0], key_prop, value)

    def edge(self, type_, from_ref, to_ref, props=None, key=None):
        return None

    def edge_to_symbol(self, type_, from_ref, target_ref, props=None):
        return None

    def _flush(self, g):
        batch = self._pending.pop(g, None)
        if batch:
            for n in batch:
                del self._waiting[f"{n.labels[0]} {n.value}"]
            for stmt in _node_statements(batch):
                self._fh.write(stmt)
                self._fh.write("\n")

    def finish(self) -> GraphRows:
        for g in list(self._pending):
            self._flush(g)
        return GraphRows([], [])


class _EdgeStreamBuilder(RowBuilder):
    """Pass 2: nodes are already on disk; each edge batch is rendered with
    ``_edge_statements`` (same grouping/BATCH) the moment it fills."""

    def __init__(self, keys, fh):
        super().__init__()
        self._keys = keys
        self._fh = fh
        self._pending: Dict[str, List[EdgeRow]] = {}

    def node(self, labels, key_prop, value, props):
        return NodeRef(labels[0], key_prop, value)

    def edge(self, type_, from_ref, to_ref, props=None, key=None):
        e = EdgeRow(type_, from_ref, to_ref, dict(props or {}), key)
        g = (
            f"{e.type}|{e.from_ref.label}.{e.from_ref.key_prop}"
            f"|{e.to_ref.label}.{e.to_ref.key_prop}|{e.key is not None}"
        )
        batch = self._pending.setdefault(g, [])
        batch.append(e)
        if len(batch) >= BATCH:
            self._flush(g)

    def edge_to_symbol(self, type_, from_ref, target_ref, props=None):
        if (target_ref.label, target_ref.value) in self._keys:
            self.edge(type_, from_ref, target_ref, props)

    def _flush(self, g):
        batch = self._pending.pop(g, None)
        if batch:
            for stmt in _edge_statements(batch):
                self._fh.write(stmt)
                self._fh.write("\n")

    def finish(self) -> GraphRows:
        for g in list(self._pending):
            self._flush(g)
        return GraphRows([], [])


def write_cypher_streaming(fh, app, app_name: str, sig_to_id: dict, analyzer=None, namespace: str | None = None) -> None:
    """Write the ``graph.cypher`` snapshot for ``app`` to ``fh`` without holding the
    node/edge rows or the rendered text in memory (two projection passes: nodes,
    then edges gated on the node key set). Rows come in emission order rather than
    sorted; the resulting graph is the same as :func:`render_cypher`'s."""
    from codeanalyzer.neo4j.project import project

    fh.write("// ── constraints & indexes ──\n")
    for stmt in CONSTRAINTS:
        fh.write(f"{stmt};\n")
    for stmt in INDEXES:
        fh.write(f"{stmt};\n")
    fh.write("\n// ── wipe this project's prior subgraph (externals/packages/decorators are shared) ──\n")
    fh.write(_wipe(app_name, namespace) + "\n")
    fh.write("\n// ── nodes ──\n")
    nodes_b = _NodeStreamBuilder(fh)
    project(app, app_name, sig_to_id, analyzer=analyzer, namespace=namespace, builder=nodes_b)
    keys = nodes_b._keys
    del nodes_b

    fh.write("\n// ── relationships ──\n")
    project(app, app_name, sig_to_id, analyzer=analyzer, namespace=namespace, builder=_EdgeStreamBuilder(keys, fh))
