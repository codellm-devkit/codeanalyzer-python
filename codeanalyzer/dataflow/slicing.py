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

"""Stage 8 of the level-3 dataflow ladder: backward slicing as an SDG query.

The classic Horwitz–Reps–Binkley two-phase traversal, which is what makes the
slice *context-sensitive* without re-descending into callees:

- **Phase 1** walks backward over every dependence edge **except PARAM_OUT**:
  it ascends from the criterion to callers (PARAM_IN/CALL reversed) and steps
  *across* callsites through SUMMARY edges, but never descends into a callee.
- **Phase 2** starts from everything phase 1 reached and walks backward over
  every edge **except PARAM_IN and CALL**: it descends into callees
  (PARAM_OUT reversed) but never re-ascends — which is exactly what prevents
  infeasible call–return mismatches.

Slicing consumes the assembled :class:`~codeanalyzer.dataflow.sdg.
ProgramGraphsIR`; taint is the same labeled traversal with a model pack and
is deliberately left to the CLDK SDK (language-independent once the SDG is
emitted — see #67).
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from codeanalyzer.dataflow.sdg import ProgramGraphsIR

Node = Tuple[str, int]  # (signature, node_id)


def _reverse_adjacency(ir: ProgramGraphsIR) -> Dict[Node, List[Tuple[Node, str]]]:
    """target → [(source, edge_type)] over intra- and inter-procedural edges."""
    radj: Dict[Node, List[Tuple[Node, str]]] = {}

    def add(src: Node, tgt: Node, kind: str) -> None:
        radj.setdefault(tgt, []).append((src, kind))

    for sig, fg in ir.functions.items():
        for e in fg.pdg.edges:
            if e.type == "CDG":
                add((sig, e.source), (sig, e.target), "CDG")
        for e in fg.ddg:
            add((sig, e.source), (sig, e.target), "DDG")
        for e in fg.extra_edges:
            add((sig, e.source), (sig, e.target), e.type)
    for e in ir.sdg_edges:
        add(
            (e.source_sig, e.source_node),
            (e.target_sig, e.target_node),
            e.type,
        )
    return radj


def backward_slice(ir: ProgramGraphsIR, signature: str, node_id: int) -> Set[Node]:
    """Context-sensitive backward slice of ``(signature, node_id)``."""
    if signature not in ir.functions:
        raise KeyError(f"unknown signature: {signature}")
    radj = _reverse_adjacency(ir)
    criterion: Node = (signature, node_id)

    def sweep(seeds: Set[Node], skip: Set[str]) -> Set[Node]:
        seen: Set[Node] = set()
        stack = list(seeds)
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            for src, kind in radj.get(node, ()):
                if kind in skip:
                    continue
                if src not in seen:
                    stack.append(src)
        return seen

    phase1 = sweep({criterion}, skip={"PARAM_OUT"})
    phase2 = sweep(phase1, skip={"PARAM_IN", "CALL"})
    return phase1 | phase2
