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

"""Stage 3b of the level-3 dataflow ladder: reaching definitions → DDG edges.

Classic forward may-analysis with a worklist over the statement-level CFG.
SSA is an implementation shortcut some ecosystems get for free; the contract
is the def-use edges, and Python hand-builds them.

Kill discipline (sound-leaning):

- A def of a bare local/param path strong-kills earlier defs of the exact
  same path.
- Defs of attribute paths strong-kill only the identical path string (a write
  through one name never kills a potentially-aliased other name).
- Subscript (``[*]``) and k-truncated (``.*``) paths are weak updates — they
  kill nothing.

A use matches a reaching def when the paths interfere textually (exact /
prefix / wildcard — :func:`access_paths.interferes`) or when the may-alias
oracle says two suffixed paths can denote one location.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from codeanalyzer.dataflow.access_paths import StatementFacts, interferes, suffix_of
from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.dataflow.cfg import ControlFlowGraph


@dataclass(frozen=True)
class DDGEdge:
    source: int  # the def node
    target: int  # the use node
    var: str  # the access path being read


def _strong_kill(path: str) -> bool:
    return not path.endswith("*")


def reaching_definitions(
    cfg: ControlFlowGraph, facts: Dict[int, StatementFacts]
) -> Dict[int, Set[Tuple[str, int]]]:
    """IN sets: ``{node: {(path, def_node), ...}}`` via worklist iteration."""
    preds = cfg.predecessors()
    succs = cfg.successors()
    node_ids = [n.id for n in cfg.nodes]

    gen: Dict[int, Set[Tuple[str, int]]] = {}
    for nid in node_ids:
        gen[nid] = {(d, nid) for d in facts[nid].defs}

    in_sets: Dict[int, Set[Tuple[str, int]]] = {nid: set() for nid in node_ids}
    out_sets: Dict[int, Set[Tuple[str, int]]] = {nid: set() for nid in node_ids}

    worklist = list(node_ids)
    while worklist:
        nid = worklist.pop(0)
        new_in: Set[Tuple[str, int]] = set()
        for p, _ in preds[nid]:
            new_in |= out_sets[p]
        strong = {d for d in facts[nid].defs if _strong_kill(d)}
        new_out = {(p, m) for (p, m) in new_in if p not in strong} | gen[nid]
        if new_in != in_sets[nid] or new_out != out_sets[nid]:
            in_sets[nid] = new_in
            out_sets[nid] = new_out
            for s, _ in succs[nid]:
                if s not in worklist:
                    worklist.append(s)
    return in_sets


def ddg_edges(
    cfg: ControlFlowGraph,
    facts: Dict[int, StatementFacts],
    oracle: TypeBasedAliasOracle,
) -> List[DDGEdge]:
    """Def-use edges: for every use at node n, an edge from each reaching def
    whose path interferes (textually or through may-alias)."""
    in_sets = reaching_definitions(cfg, facts)
    edges: Set[DDGEdge] = set()
    for node in cfg.nodes:
        uses = facts[node.id].uses
        if not uses:
            continue
        reaching = in_sets[node.id]
        # A (path, n) pair reaches n itself only through a real cycle, so a
        # self-edge here is precisely the loop-carried dependency.
        for use in uses:
            for def_path, def_node in reaching:
                if interferes(use, def_path) or (
                    (suffix_of(use) or suffix_of(def_path))
                    and oracle.may_alias(use, def_path)
                ):
                    edges.add(DDGEdge(source=def_node, target=node.id, var=use))
    return sorted(edges, key=lambda e: (e.source, e.target, e.var))
