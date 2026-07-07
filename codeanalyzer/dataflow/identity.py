"""Bijection between internal IR node ids (ints, per function) and their
canonical ids.

Two forms per node:

* **local** — the intra-callable id used as the ``body`` map key and as every
  ``cfg``/``cdg``/``ddg`` edge endpoint: ``"@entry"``/``"@exit"`` for the
  synthetic CFG bookends, ``"line:col"`` for real statements. This matches the
  key format L1 already uses for ``call`` nodes (see ``schema/l1_body.py``), so
  an L1 body node and its coinciding CFG node land on the same key and L1 ⊆ L3
  holds.
* **global** — ``"<callable can:// id>@<local>"``, the fully addressable id for
  cross-callable references and the Neo4j PyCFGNode keys (a later task).
"""
from __future__ import annotations
from typing import Dict, Iterable


class IdentityMap:
    def __init__(self, callable_id: str, id_to_local: Dict[int, str]):
        self._callable_id = callable_id
        self._map = id_to_local

    @classmethod
    def for_function(cls, callable_id: str, pdg) -> "IdentityMap":
        cfg = pdg.cfg
        m: Dict[int, str] = {}
        for n in cfg.nodes:
            if n.id == cfg.entry_id:
                m[n.id] = "@entry"
            elif n.id == cfg.exit_id:
                m[n.id] = "@exit"
            else:
                m[n.id] = f"{n.start_line}:{n.start_column}"
        return cls(callable_id, m)

    def local(self, node_id: int) -> str:
        """Intra-callable id: ``"@entry"``/``"@exit"`` or ``"line:col"``."""
        return self._map[node_id]

    def global_id(self, node_id: int) -> str:
        """Fully addressable id: ``"<callable-id>@<local>"``."""
        return f"{self._callable_id}@{self._map[node_id]}"

    def node_ids(self) -> Iterable[int]:
        return self._map.keys()
