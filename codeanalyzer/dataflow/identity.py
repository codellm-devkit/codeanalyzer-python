"""Bijection between internal IR node ids (ints, per function) and canonical
ordinal ids `<callable can:// id>@<tag>` — `@entry`/`@exit` for the synthetic
CFG bookends, `@line:col` for real statements. Both emitters consume this so
JSON body-node ids and Neo4j PyCFGNode keys are identical."""
from __future__ import annotations
from typing import Dict, Iterable


class IdentityMap:
    def __init__(self, callable_id: str, id_to_ordinal: Dict[int, str]):
        self._callable_id = callable_id
        self._map = id_to_ordinal

    @classmethod
    def for_function(cls, callable_id: str, pdg) -> "IdentityMap":
        cfg = pdg.cfg
        m: Dict[int, str] = {}
        for n in cfg.nodes:
            if n.id == cfg.entry_id:
                m[n.id] = f"{callable_id}@entry"
            elif n.id == cfg.exit_id:
                m[n.id] = f"{callable_id}@exit"
            else:
                m[n.id] = f"{callable_id}@{n.start_line}:{n.start_column}"
        return cls(callable_id, m)

    def ordinal(self, node_id: int) -> str:
        return self._map[node_id]

    def node_ids(self) -> Iterable[int]:
        return self._map.keys()
