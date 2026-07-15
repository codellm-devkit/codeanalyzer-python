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

"""Stage 5b of the level-3 dataflow ladder: SCC condensation of the call graph.

The call graph is a frozen oracle (level-1 Jedi edges, provenance-merged with
level-2 PyCG when enabled); Tarjan condenses it into strongly connected
components, and the condensation DAG in reverse topological order is the
bottom-up processing schedule for summary composition — callees before
callers, one monotone fixpoint per SCC (mutual recursion).

Iterative Tarjan (no recursion — real projects overflow Python's stack), with
sorted tie-breaking so the schedule is deterministic.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple


def strongly_connected_components(
    nodes: List[str], edges: List[Tuple[str, str]]
) -> List[List[str]]:
    """Tarjan SCCs in reverse topological order (callees before callers).
    Deterministic: nodes are visited in sorted order and members sorted."""
    adj: Dict[str, List[str]] = {n: [] for n in nodes}
    for s, t in sorted(set(edges)):
        if s in adj and t in adj:
            adj[s].append(t)

    index_of: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    sccs: List[List[str]] = []
    counter = [0]

    for root in sorted(adj):
        if root in index_of:
            continue
        # Iterative DFS: (node, iterator position over successors).
        work: List[Tuple[str, int]] = [(root, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index_of[node] = lowlink[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            recurse = False
            successors = adj[node]
            while i < len(successors):
                succ = successors[i]
                i += 1
                if succ not in index_of:
                    work.append((node, i))
                    work.append((succ, 0))
                    recurse = True
                    break
                if succ in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[succ])
            if recurse:
                continue
            if lowlink[node] == index_of[node]:
                component: List[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                sccs.append(sorted(component))
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

    # Tarjan emits SCCs in reverse topological order already.
    return sccs
