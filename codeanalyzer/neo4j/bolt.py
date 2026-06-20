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

"""The incremental writer: push :class:`GraphRows` into a live Neo4j over Bolt.
Unlike the snapshot writer, this one reads the DB's current state and updates
only what changed.

Algorithm (the module subgraph is the unit of idempotent replacement):
  1. ensure constraints + indexes.
  2. diff each module's ``content_hash`` against the DB → the set of changed modules.
  3. per changed module, in a transaction: delete the edges it owned (edges out of
     its nodes), detach-delete the declarations it no longer emits, then upsert
     its current nodes.
  4. upsert edges owned by changed modules (+ the shared edges).
  5. on a FULL run only, prune modules whose source file vanished.

Nodes are MERGE-upserted, never blindly deleted, so a declaration another
(unchanged) module still references survives and its incoming edges stay valid.
``:PyExternal`` / ``:PyPackage`` / ``:PyDecorator`` are shared (no ``_module``) and are
MERGE-only.

The ``neo4j`` driver is imported lazily so it stays an optional dependency and
off the default (json) output path entirely.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from codeanalyzer.neo4j.rows import EdgeRow, GraphRows, NodeRow, chunk
from codeanalyzer.neo4j.schema import CONSTRAINTS, INDEXES
from codeanalyzer.utils import logger

DESCENDANTS = "[:PY_DECLARES|PY_HAS_METHOD|PY_HAS_ATTRIBUTE|PY_DECLARES_VAR|PY_HAS_CALLSITE*1..]"
BATCH = 1000


@dataclass
class BoltConfig:
    uri: str
    user: str
    password: str
    database: Optional[str] = None


def bolt_writer(rows: GraphRows, cfg: BoltConfig, full_run: bool) -> None:
    try:
        import neo4j  # noqa: WPS433 (lazy, optional dependency)
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "The 'neo4j' driver is required for '--emit neo4j --neo4j-uri'. "
            "Install it with: pip install 'codeanalyzer-python[neo4j]'"
        ) from exc

    driver = neo4j.GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))
    session_kwargs = {"database": cfg.database} if cfg.database else {}

    def session():
        return driver.session(**session_kwargs)

    try:
        # 1. schema (DDL runs in its own autocommit transactions).
        with session() as s:
            for stmt in [*CONSTRAINTS, *INDEXES]:
                s.run(stmt)

        # Partition nodes by owning module; shared nodes have no _module.
        by_module: Dict[str, List[NodeRow]] = {}
        shared: List[NodeRow] = []
        module_of: Dict[str, str] = {}  # node value → owning module
        for n in rows.nodes:
            m = n.props.get("_module")
            if isinstance(m, str):
                by_module.setdefault(m, []).append(n)
                module_of[n.value] = m
            else:
                shared.append(n)

        # 2. diff content_hash.
        db_hash: Dict[str, Optional[str]] = {}
        with session() as s:
            res = s.run("MATCH (m:PyModule) RETURN m.file_key AS k, m.content_hash AS h")
            for rec in res:
                db_hash[rec["k"]] = rec["h"]
        changed = set()
        for m, nodes in by_module.items():
            row_hash = _hash_of(nodes, m)
            if m not in db_hash or row_hash is None or row_hash != db_hash.get(m):
                changed.add(m)
        logger.info(
            f"neo4j(bolt): {len(by_module)} modules ({len(changed)} changed), "
            f"{len(shared)} shared nodes, {len(rows.edges)} edges"
        )

        # 3. shared nodes are always upserted (MERGE-only).
        _upsert_nodes(session, neo4j, shared)

        # 4. per changed module: purge owned edges + vanished decls, then upsert its nodes.
        for m in changed:
            nodes = by_module[m]
            keys = [n.value for n in nodes]
            with session() as s:
                def _purge(tx, module=m, node_keys=keys):
                    tx.run("MATCH (x {_module: $m})-[r]->() DELETE r", m=module)
                    tx.run(
                        "MATCH (x {_module: $m}) "
                        "WHERE NOT coalesce(x.signature, x.id, x.file_key) IN $keys "
                        "DETACH DELETE x",
                        m=module,
                        keys=node_keys,
                    )

                s.execute_write(_purge)
            _upsert_nodes(session, neo4j, nodes)

        # 5. upsert edges owned by a changed module (owner = source node's module) or shared.
        edges = [
            e
            for e in rows.edges
            if module_of.get(e.from_ref.value) is None or module_of.get(e.from_ref.value) in changed
        ]
        _upsert_edges(session, neo4j, edges)

        # 6. orphan prune — only safe on a full run (a targeted run can't tell deleted from untargeted).
        if full_run:
            present = list(by_module.keys())
            with session() as s:
                res = s.run(
                    "MATCH (m:PyModule) WHERE NOT m.file_key IN $present "
                    f"OPTIONAL MATCH (m)-{DESCENDANTS}->(x) DETACH DELETE x, m "
                    "RETURN count(m) AS pruned",
                    present=present,
                )
                pruned = res.single()
                pruned_count = pruned["pruned"] if pruned else 0
                logger.info(f"neo4j(bolt): pruned {pruned_count} vanished module(s)")
        else:
            logger.info(
                "neo4j(bolt): targeted run — orphan pruning skipped (deleted files not removed)"
            )
    finally:
        driver.close()


# ----------------------------------------------------------------------------------------------
# Batched upserts
# ----------------------------------------------------------------------------------------------


def _upsert_nodes(session, neo4j, nodes: List[NodeRow]) -> None:
    groups: Dict[str, List[NodeRow]] = {}
    for n in nodes:
        groups.setdefault(f"{':'.join(n.labels)}|{n.key_prop}", []).append(n)

    for group in groups.values():
        labels = group[0].labels
        key_prop = group[0].key_prop
        set_labels = f", n:{':'.join(labels[1:])}" if len(labels) > 1 else ""
        cypher = (
            f"UNWIND $rows AS row MERGE (n:{labels[0]} {{{key_prop}: row.k}}) "
            f"SET n += row.p{set_labels}"
        )
        for batch in chunk(group, BATCH):
            payload = [{"k": n.value, "p": _to_params(n.props, neo4j)} for n in batch]
            with session() as s:
                s.run(cypher, rows=payload)


def _upsert_edges(session, neo4j, edges: List[EdgeRow]) -> None:
    groups: Dict[str, List[EdgeRow]] = {}
    for e in edges:
        key = f"{e.type}|{e.from_ref.label}.{e.from_ref.key_prop}|{e.to_ref.label}.{e.to_ref.key_prop}"
        groups.setdefault(key, []).append(e)

    for group in groups.values():
        first = group[0]
        from_ref, to_ref = first.from_ref, first.to_ref
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{from_ref.label} {{{from_ref.key_prop}: row.f}}) "
            f"MATCH (b:{to_ref.label} {{{to_ref.key_prop}: row.t}}) "
            f"MERGE (a)-[r:{first.type}]->(b) SET r += row.p"
        )
        for batch in chunk(group, BATCH):
            payload = [
                {"f": e.from_ref.value, "t": e.to_ref.value, "p": _to_params(e.props, neo4j)}
                for e in batch
            ]
            with session() as s:
                s.run(cypher, rows=payload)


# ----------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------


def _hash_of(nodes: List[NodeRow], file_key: str) -> Optional[str]:
    for n in nodes:
        if n.labels[0] == "PyModule" and n.value == file_key:
            h = n.props.get("content_hash")
            return h if isinstance(h, str) else None
    return None


def _to_params(props, neo4j) -> dict:
    """Map props to driver params. The Python driver already distinguishes int
    from float, so unlike the JS driver no integer coercion is needed — this is a
    straight passthrough kept symmetric with the snapshot writer's shape."""
    return dict(props)
