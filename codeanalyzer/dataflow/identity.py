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
  cross-callable references and the Neo4j PyBodyNode keys (a later task).
"""
from __future__ import annotations
from collections import defaultdict
from typing import Dict, Iterable, Optional, Tuple

from codeanalyzer.schema.ids import global_ordinal


class IdentityMap:
    def __init__(self, callable_id: str, id_to_local: Dict[int, str]):
        self._callable_id = callable_id
        self._map = id_to_local

    @classmethod
    def for_function(cls, callable_id: str, pdg, param_nodes=None) -> "IdentityMap":
        cfg = pdg.cfg
        m: Dict[int, str] = {}
        for n in cfg.nodes:
            if n.id == cfg.entry_id:
                m[n.id] = "@entry"
            elif n.id == cfg.exit_id:
                m[n.id] = "@exit"
            else:
                m[n.id] = f"{n.start_line}:{n.start_column}"
        im = cls(callable_id, m)
        if param_nodes:
            im._assign_param_locals(param_nodes)
        return im

    def _assign_param_locals(self, param_nodes) -> None:
        """Fold the L4 synthetic param vertices into ``_map`` so ``local`` /
        ``global_id`` resolve them uniformly with CFG nodes.

        Canonical locals, per node ``kind`` (idx = position within the node's
        ``(kind, call_node)`` group, in ``param_nodes`` list order):

        * ``formal_in``  → ``"@formal_in:<idx>"`` (always indexed);
        * ``formal_out`` → ``"@formal_out"`` when the function has exactly one,
          else ``"@formal_out:<idx>"``;
        * ``actual_in``  → ``"<callsite-local>/actual_in:<idx>"`` (always
          indexed), ``<callsite-local> = self.local(pn.call_node)``;
        * ``actual_out`` → ``"<callsite-local>/actual_out"`` when the callsite
          has exactly one, else ``"<callsite-local>/actual_out:<idx>"``.
        """
        counts: Dict[Tuple[str, Optional[int]], int] = defaultdict(int)
        for pn in param_nodes:
            counts[(pn.kind, pn.call_node)] += 1

        seen: Dict[Tuple[str, Optional[int]], int] = defaultdict(int)
        for pn in param_nodes:
            key = (pn.kind, pn.call_node)
            idx = seen[key]
            seen[key] += 1
            n = counts[key]
            if pn.kind == "formal_in":
                local = f"@formal_in:{idx}"
            elif pn.kind == "formal_out":
                local = "@formal_out" if n == 1 else f"@formal_out:{idx}"
            elif pn.kind == "actual_in":
                cs = self.local(pn.call_node)
                local = f"{cs}/actual_in:{idx}"
            elif pn.kind == "actual_out":
                cs = self.local(pn.call_node)
                local = f"{cs}/actual_out" if n == 1 else f"{cs}/actual_out:{idx}"
            else:
                raise ValueError(f"unknown param node kind: {pn.kind!r}")
            self._map[pn.id] = local

    def local(self, node_id: int) -> str:
        """Intra-callable id: ``"@entry"``/``"@exit"`` or ``"line:col"``."""
        return self._map[node_id]

    def global_id(self, node_id: int) -> str:
        """Fully addressable id: ``"<callable-id>@<local>"``."""
        return global_ordinal(self._callable_id, self._map[node_id])

    def node_ids(self) -> Iterable[int]:
        return self._map.keys()
