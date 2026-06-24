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
    EdgeRow,
    GraphRows,
    NodeRow,
    chunk,
    cypher_map,
    cypher_value,
)
from codeanalyzer.neo4j.schema import CONSTRAINTS, INDEXES

BATCH = 500


def render_cypher(rows: GraphRows, app_name: str) -> str:
    out: List[str] = []

    out.append("// ── constraints & indexes ──")
    for stmt in CONSTRAINTS:
        out.append(f"{stmt};")
    for stmt in INDEXES:
        out.append(f"{stmt};")

    out.append("")
    out.append("// ── wipe this project's prior subgraph (externals/packages/decorators are shared) ──")
    out.append(_wipe(app_name))

    out.append("")
    out.append("// ── nodes ──")
    out.extend(_node_statements(rows.nodes))

    out.append("")
    out.append("// ── relationships ──")
    out.extend(_edge_statements(rows.edges))

    out.append("")
    return "\n".join(out)


def _wipe(app_name: str) -> str:
    name = cypher_value(app_name)
    return "\n".join(
        [
            f"MATCH (a:PyApplication {{name: {name}}})",
            "OPTIONAL MATCH (a)-[:PY_HAS_MODULE]->(m:PyModule)",
            "OPTIONAL MATCH (m)-[:PY_DECLARES|PY_HAS_METHOD|PY_HAS_ATTRIBUTE|PY_DECLARES_VAR|PY_HAS_CALLSITE*1..]->(x)",
            "DETACH DELETE x, m, a;",
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
        key = f"{e.type}|{e.from_ref.label}.{e.from_ref.key_prop}|{e.to_ref.label}.{e.to_ref.key_prop}"
        groups.setdefault(key, []).append(e)

    blocks: List[str] = []
    for group in groups.values():
        first = group[0]
        from_ref, to_ref = first.from_ref, first.to_ref
        for batch in chunk(group, BATCH):
            rows_lit = ",\n".join(
                f"  {{f: {cypher_value(e.from_ref.value)}, t: {cypher_value(e.to_ref.value)}, "
                f"p: {cypher_map(e.props)}}}"
                for e in batch
            )
            blocks.append(
                f"UNWIND [\n{rows_lit}\n] AS row\n"
                f"MATCH (a:{from_ref.label} {{{from_ref.key_prop}: row.f}})\n"
                f"MATCH (b:{to_ref.label} {{{to_ref.key_prop}: row.t}})\n"
                f"MERGE (a)-[r:{first.type}]->(b)\n"
                f"SET r += row.p;"
            )
    return blocks
