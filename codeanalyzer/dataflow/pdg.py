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

"""Stage 4 of the level-3 dataflow ladder: PDG assembly.

Per callable, the PDG is the union of the stage-2 control-dependence edges
(``CDG``) and the stage-3 def-use edges (``DDG``), over the same
``(signature, node_id)`` nodes. Nothing new is computed here — this module is
bookkeeping plus the intraprocedural backward slice that gates it: reverse
reachability over CDG ∪ DDG from a criterion node, expected to match a
hand-computed node set exactly on the fixture.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from codeanalyzer.dataflow.access_paths import (
    FunctionScope,
    StatementFacts,
    build_scope,
    statement_facts,
)
from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.dataflow.cfg import ControlFlowGraph, build_cfg
from codeanalyzer.dataflow.defuse import ddg_edges
from codeanalyzer.dataflow.dominance import control_dependence


@dataclass(frozen=True)
class PDGEdge:
    source: int
    target: int
    type: str  # "CDG" | "DDG"
    var: Optional[str] = None  # access path on DDG edges


@dataclass
class FunctionPDG:
    """One callable's intraprocedural graphs, keyed externally by signature."""

    cfg: ControlFlowGraph
    edges: List[PDGEdge]
    scope: FunctionScope
    facts: Dict[int, StatementFacts] = field(default_factory=dict)


def build_pdg(
    func: ast.AST,
    enclosing_locals: Set[str],
    oracle: TypeBasedAliasOracle,
    k: int = 3,
    global_qualifier: Optional[str] = None,
) -> FunctionPDG:
    """CFG → dominance → def-use → PDG for one callable."""
    cfg = build_cfg(func)
    scope = build_scope(func, enclosing_locals)
    facts = statement_facts(cfg, func, scope, k, global_qualifier)

    edges: List[PDGEdge] = [
        PDGEdge(source=a, target=b, type="CDG") for a, b in control_dependence(cfg)
    ]
    edges.extend(
        PDGEdge(source=e.source, target=e.target, type="DDG", var=e.var)
        for e in ddg_edges(cfg, facts, oracle)
    )
    edges.sort(key=lambda e: (e.source, e.target, e.type, e.var or ""))
    return FunctionPDG(cfg=cfg, edges=edges, scope=scope, facts=facts)


def intraprocedural_backward_slice(pdg: FunctionPDG, criterion: int) -> Set[int]:
    """Reverse reachability over CDG ∪ DDG from the criterion node (the
    criterion itself is in the slice). The stage-4 gate."""
    reverse: Dict[int, List[int]] = {}
    for e in pdg.edges:
        reverse.setdefault(e.target, []).append(e.source)
    seen: Set[int] = set()
    stack = [criterion]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(reverse.get(n, []))
    return seen
