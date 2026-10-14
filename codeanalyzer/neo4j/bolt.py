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

**A push never deletes by default** (#171). Steps 3 and 5 are the only destructive
ones and both run on ``eager`` (``--eager``) only; a default ``--lazy`` push is purely
additive — MERGE-upsert of nodes and edges, nothing removed. The cost of the default is
staleness: a declaration or a call edge the source no longer has stays in the graph until
an ``--eager`` push reconciles it. That is the deliberate trade — an incremental push into
a shared database should not be able to destroy anything, and the destructive rebuild is
opt-in under the same flag that already forces a clean analysis rebuild.

Nodes are MERGE-upserted, never blindly deleted, so a declaration another
(unchanged) module still references survives and its incoming edges stay valid.
``:PyExternal`` / ``:PyPackage`` / ``:PyDecorator`` have no owning module and are
MERGE-only.

**Every destructive statement is scoped on the ``can://`` id prefix** (#173). The id is a
path — ``can://<app>/python/<file>/...`` — so ``id = <module-id> OR id STARTS WITH
<module-id> + '/'`` is containment, and it is one application, one language and one
module at once. That is what neither a label anchor nor the retired ``_module`` property
could give: two python applications sharing ``src/foo.py`` carry identical labels and an
identical file key, and only the id tells them apart. ``:PyCanNode`` anchors the predicate
so it seeks an index instead of scanning the store; it carries no safety claim.

The ``neo4j`` driver is imported lazily so it stays an optional dependency and
off the default (json) output path entirely.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from codeanalyzer.neo4j.rows import (
    CAN_NODE, EdgeRow, GraphRows, NodeRef, NodeRow, RowBuilder, application_prefix,
    chunk, descendant_prefix, normalize_node,
)
from codeanalyzer.neo4j.schema import CONSTRAINTS, INDEXES
from codeanalyzer.utils import logger

BATCH = 1000

# The per-module purge (#173): the module by equality, its subtree by prefix. Anchored
# on :PyCanNode only so the predicate can seek (see ``rows.CAN_NODE``).
PURGE_MODULE_EDGES = (
    f"MATCH (x:{CAN_NODE}) WHERE x.id = $mid OR x.id STARTS WITH $pre "
    "MATCH (x)-[r]->() DELETE r"
)
PURGE_VANISHED_NODES = (
    f"MATCH (x:{CAN_NODE}) WHERE (x.id = $mid OR x.id STARTS WITH $pre) "
    "AND NOT x.id IN $keys DETACH DELETE x"
)
# The orphan prune: modules inside this application's prefix that the run no longer
# emits, and everything under each. Batched — deleting a large application in one
# transaction exhausts dbms.memory.transaction.total.max (typescript#116).
PRUNE_VANISHED_MODULES = (
    f"MATCH (m:PyModule:{CAN_NODE}) WHERE m.id STARTS WITH $app AND NOT m.id IN $present "
    f"CALL {{ WITH m MATCH (x:{CAN_NODE}) WHERE x.id = m.id OR x.id STARTS WITH m.id + '/' "
    "DETACH DELETE x } IN TRANSACTIONS OF 1000 ROWS "
    "RETURN count(*) AS pruned"
)


@dataclass
class BoltConfig:
    uri: str
    user: str
    password: str
    database: Optional[str] = None


class _NodeBatches(RowBuilder):
    """Writes node batches as the projection emits them, keeping only the
    :PySymbol keys (for edge gating) and one changed module's ids (for --eager)."""

    def __init__(self, session, neo4j, changed, app_prefix, eager):
        super().__init__()
        self._session, self._neo4j = session, neo4j
        self._changed, self._app_prefix, self._eager = changed, app_prefix, eager
        self._pending, self._waiting = [], set()
        self._module = self._module_id = None
        self._module_keys = set()

    def begin_module(self, file_key, module):
        self._flush()
        self._module, self._module_id, self._module_keys = file_key, module.id, set()
        if self._eager and file_key in self._changed:
            if not module.id or not module.id.startswith(self._app_prefix):
                raise ValueError(f"neo4j: module {file_key!r} has no can:// id under "
                                 f"{self._app_prefix!r}; refusing to purge")
            with self._session() as s:
                s.run(PURGE_MODULE_EDGES, mid=module.id, pre=descendant_prefix(module.id)).consume()

    def end_module(self):
        self._flush()
        if self._eager and self._module in self._changed:
            with self._session() as s:
                s.run(PURGE_VANISHED_NODES, mid=self._module_id, keys=list(self._module_keys),
                      pre=descendant_prefix(self._module_id)).consume()
        self._module = self._module_id = None
        self._module_keys = set()

    def node(self, labels, key_prop, value, props):
        labels, props, module = normalize_node(labels, key_prop, value, props)
        identity = (labels[0], value)
        if labels[0] == "PySymbol":
            self._keys.add(identity)
        ref = NodeRef(labels[0], key_prop, value)
        if module is not None:
            if module != self._module:
                raise ValueError("neo4j: owned node emitted outside its module boundary")
            if module not in self._changed:
                return ref
            self._module_keys.add(value)
        if labels[0] == "PyModule":
            # The hash is published after the whole push succeeds; a failed push
            # must not leave a partly written module looking unchanged.
            props.pop("content_hash", None)
        # A repeated node must be written in emission order, even across label groups.
        if identity in self._waiting:
            self._flush()
        self._pending.append(NodeRow(list(labels), key_prop, value, props, module))
        self._waiting.add(identity)
        if len(self._pending) >= BATCH:
            self._flush()
        return ref

    def edge(self, *args, **kwargs):
        pass

    def edge_to_symbol(self, *args, **kwargs):
        pass

    def _flush(self):
        _upsert_nodes(self._session, self._neo4j, self._pending)
        self._pending, self._waiting = [], set()

    def finish(self):
        self._flush()
        return GraphRows([], [])


class _EdgeBatches(RowBuilder):
    """Writes the relationships owned by changed modules (or by no module) in batches."""

    def __init__(self, session, neo4j, changed, keys, module_by_id):
        super().__init__()
        self._session, self._neo4j, self._changed = session, neo4j, changed
        self._keys, self._module_by_id = keys, module_by_id
        self._pending, self._waiting = [], set()

    def _owner(self, value):
        # The module whose id equals ``value`` or contains it (``module.id + '/'``),
        # the same containment the eager purge uses.
        end = len(value)
        while end > 0:
            if value[:end] in self._module_by_id:
                return self._module_by_id[value[:end]]
            end = value.rfind("/", 0, end)
        return None

    def node(self, labels, key_prop, value, props):
        return NodeRef(labels[0], key_prop, value)

    def edge(self, type_, from_ref, to_ref, props=None, key=None):
        owner = self._owner(from_ref.value)
        if owner is not None and owner not in self._changed:
            return
        identity = (type_, from_ref, to_ref, key)
        if identity in self._waiting:
            self._flush()
        self._pending.append(EdgeRow(type_, from_ref, to_ref, dict(props or {}), key))
        self._waiting.add(identity)
        if len(self._pending) >= BATCH:
            self._flush()

    def edge_to_symbol(self, type_, from_ref, target_ref, props=None):
        if (target_ref.label, target_ref.value) in self._keys:
            self.edge(type_, from_ref, target_ref, props)

    def _flush(self):
        _upsert_edges(self._session, self._neo4j, self._pending)
        self._pending, self._waiting = [], set()

    def finish(self):
        self._flush()
        return GraphRows([], [])


def bolt_writer_streaming(app, app_name, sig_to_id, analyzer, cfg: BoltConfig, full_run: bool,
                          eager: bool = False) -> None:
    """:func:`bolt_writer` without materializing :class:`GraphRows`: the projection
    runs twice (nodes, then relationships) and is written in bounded batches."""
    try:
        import neo4j  # noqa: WPS433 (lazy, optional dependency)
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "The 'neo4j' driver is required for a live Bolt push. "
            "Install it with: pip install 'codeanalyzer-python[neo4j]'"
        ) from exc
    from codeanalyzer.neo4j.project import project

    app_prefix = application_prefix(app_name)
    driver = neo4j.GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))

    session_kwargs = {"database": cfg.database} if cfg.database else {}

    def session():
        return driver.session(**session_kwargs)

    try:
        with session() as s:
            for stmt in CONSTRAINTS + INDEXES:
                s.run(stmt).consume()
            db_hash = {rec["k"]: rec["h"] for rec in s.run(
                f"MATCH (m:PyModule:{CAN_NODE}) WHERE m.id STARTS WITH $app "
                "RETURN m.id AS k, m.content_hash AS h", app=app_prefix,
            )}
        changed = {key for key, mod in app.symbol_table.items()
                   if mod.id not in db_hash or mod.content_hash is None
                   or mod.content_hash != db_hash[mod.id]}
        logger.info(f"neo4j(bolt): {len(app.symbol_table)} modules ({len(changed)} changed)")

        nodes = _NodeBatches(session, neo4j, changed, app_prefix, eager)
        project(app, app_name, sig_to_id, analyzer=analyzer, builder=nodes, module_scoped=True)
        module_by_id = {mod.id: key for key, mod in app.symbol_table.items()}
        edges = _EdgeBatches(session, neo4j, changed, nodes._keys, module_by_id)
        project(app, app_name, sig_to_id, analyzer=analyzer, builder=edges)

        if full_run and eager:
            with session() as s:
                rec = s.run(PRUNE_VANISHED_MODULES, app=app_prefix, present=list(module_by_id)).single()
                logger.info(f"neo4j(bolt): pruned {rec['pruned'] if rec else 0} vanished module(s)")
        _upsert_nodes(session, neo4j, [
            NodeRow(["PyModule"], "id", app.symbol_table[key].id,
                    {"content_hash": app.symbol_table[key].content_hash})
            for key in changed if app.symbol_table[key].content_hash is not None
        ])
    finally:
        driver.close()


def bolt_writer(rows: GraphRows, cfg: BoltConfig, full_run: bool, eager: bool = False) -> None:
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

        # The application anchor. Every destructive statement below is scoped to
        # ``can://<app>/``; an empty application is refused up front rather than
        # becoming ``STARTS WITH ''`` (every node in the database). The root row is
        # keyed on its ``can://`` id now, so the name comes off its props — reading
        # ``n.value`` here would build ``can://can://<app>/``.
        app_name = next(
            (
                n.props.get("name")
                for n in rows.nodes
                if n.labels and n.labels[0] == "PyApplication"
            ),
            None,
        )
        app_prefix = application_prefix(app_name)

        # Partition nodes by owning module (an in-memory field, never emitted, #173);
        # shared nodes have none.
        by_module: Dict[str, List[NodeRow]] = {}
        shared: List[NodeRow] = []
        module_of: Dict[str, str] = {}  # node value → owning module
        for n in rows.nodes:
            if n.module is not None:
                by_module.setdefault(n.module, []).append(n)
                module_of[n.value] = n.module
            else:
                shared.append(n)

        # 2. diff content_hash, keyed by module id inside this application's prefix.
        # Keyed by file key it was application-blind: a second application whose
        # module shares the path and the hash looked "unchanged" and was never written.
        db_hash: Dict[str, Optional[str]] = {}
        with session() as s:
            res = s.run(
                f"MATCH (m:PyModule:{CAN_NODE}) WHERE m.id STARTS WITH $app "
                "RETURN m.id AS k, m.content_hash AS h",
                app=app_prefix,
            )
            for rec in res:
                db_hash[rec["k"]] = rec["h"]
        changed = set()
        for m, nodes in by_module.items():
            mid = _module_id_of(nodes)
            row_hash = _hash_of(nodes)
            if mid not in db_hash or row_hash is None or row_hash != db_hash.get(mid):
                changed.add(m)
        logger.info(
            f"neo4j(bolt): {len(by_module)} modules ({len(changed)} changed), "
            f"{len(shared)} shared nodes, {len(rows.edges)} edges"
        )

        # 3. shared nodes are always upserted (MERGE-only).
        _upsert_nodes(session, neo4j, shared)

        # 4. per changed module: purge owned edges + vanished decls, then upsert its nodes.
        # The purge is the only destructive step in a push, so it runs on --eager only.
        for m in changed:
            nodes = by_module[m]
            keys = [n.value for n in nodes]
            if not eager:
                _upsert_nodes(session, neo4j, nodes)
                continue
            # The module id comes from the module's own row, never by splitting a
            # declaration's id: a file key may itself contain '/'.
            module_id = _module_id_of(nodes)
            if module_id is None or not module_id.startswith(app_prefix):
                raise ValueError(
                    f"neo4j: module {m!r} has no can:// id under {app_prefix!r}; "
                    "refusing to purge"
                )
            with session() as s:
                def _purge(tx, mid=module_id, node_keys=keys):
                    params = {"mid": mid, "pre": descendant_prefix(mid)}
                    tx.run(PURGE_MODULE_EDGES, **params)
                    tx.run(PURGE_VANISHED_NODES, keys=node_keys, **params)

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
        # Scoped to ``can://<app>/`` so a full run for application B never deletes
        # application A's modules from a shared database — even when both are python and
        # share a module path.
        if full_run and eager:
            present = [mid for mid in (_module_id_of(ns) for ns in by_module.values()) if mid]
            with session() as s:
                res = s.run(PRUNE_VANISHED_MODULES, app=app_prefix, present=present)
                pruned = res.single()
                pruned_count = pruned["pruned"] if pruned else 0
                logger.info(f"neo4j(bolt): pruned {pruned_count} vanished module(s)")
        elif not eager:
            logger.info(
                "neo4j(bolt): additive push (--lazy) — nothing deleted; "
                "re-run with --eager to reconcile removed declarations and edges"
            )
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
        key = (
            f"{e.type}|{e.from_ref.label}.{e.from_ref.key_prop}"
            f"|{e.to_ref.label}.{e.to_ref.key_prop}|{e.key is not None}"
        )
        groups.setdefault(key, []).append(e)

    for group in groups.values():
        first = group[0]
        from_ref, to_ref = first.from_ref, first.to_ref
        # Discriminated relationships MERGE on ``{_k}`` so several distinct
        # edges of one type coexist between the same endpoint pair (per-var
        # PY_DDG, the true/false PY_CFG_NEXT pair). See EdgeRow.key.
        rel_key = " {_k: row.k}" if first.key is not None else ""
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{from_ref.label} {{{from_ref.key_prop}: row.f}}) "
            f"MATCH (b:{to_ref.label} {{{to_ref.key_prop}: row.t}}) "
            f"MERGE (a)-[r:{first.type}{rel_key}]->(b) SET r += row.p"
        )
        for batch in chunk(group, BATCH):
            payload = [
                {
                    "f": e.from_ref.value,
                    "t": e.to_ref.value,
                    "k": e.key,
                    "p": _to_params(e.props, neo4j),
                }
                for e in batch
            ]
            with session() as s:
                s.run(cypher, rows=payload)


# ----------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------


def _module_row(nodes: List[NodeRow]) -> Optional[NodeRow]:
    return next((n for n in nodes if n.labels[0] == "PyModule"), None)


def _module_id_of(nodes: List[NodeRow]) -> Optional[str]:
    row = _module_row(nodes)
    return row.value if row is not None else None


def _hash_of(nodes: List[NodeRow]) -> Optional[str]:
    row = _module_row(nodes)
    h = row.props.get("content_hash") if row is not None else None
    return h if isinstance(h, str) else None


def _to_params(props, neo4j) -> dict:
    """Map props to driver params. The Python driver already distinguishes int
    from float, so unlike the JS driver no integer coercion is needed — this is a
    straight passthrough kept symmetric with the snapshot writer's shape."""
    return dict(props)
