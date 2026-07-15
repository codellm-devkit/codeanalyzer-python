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

"""Stage 2 of the level-3 dataflow ladder: dominance and control dependence.

Post-dominators are computed with the Cooper–Harper–Kennedy iterative
algorithm over the reverse CFG. Infinite loops are already normalized by the
CFG builder (synthetic escape edge to EXIT), so the post-dominator tree always
has the unique root EXIT.

Control dependence follows Ferrante–Ottenstein–Warren: for each CFG edge
``(a, b)`` where ``b`` does not post-dominate ``a``, every node on the
post-dominator-tree path from ``b`` up to (but not including) ``a``'s
immediate post-dominator is control-dependent on ``a``.

Nodes with no branch-node control dependence are control-dependent on ENTRY —
the conventional region root, which keeps every statement anchored in the PDG
and gives interprocedural traversals a path from a callee's ENTRY to its
unconditional statements.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from codeanalyzer.dataflow.cfg import ControlFlowGraph


def _postorder(adj: Dict[int, List[int]], root: int) -> List[int]:
    """Iterative DFS postorder over ``adj`` from ``root``."""
    order: List[int] = []
    visited: Set[int] = set()
    stack: List[Tuple[int, int]] = [(root, 0)]
    visited.add(root)
    while stack:
        node, i = stack.pop()
        children = adj.get(node, [])
        if i < len(children):
            stack.append((node, i + 1))
            child = children[i]
            if child not in visited:
                visited.add(child)
                stack.append((child, 0))
        else:
            order.append(node)
    return order


def post_dominators(cfg: ControlFlowGraph) -> Dict[int, int]:
    """Immediate post-dominator of every node, as ``{node: ipdom}``.

    EXIT is its own post-dominator (the tree root). Cooper–Harper–Kennedy
    ("A Simple, Fast Dominance Algorithm") run on the reverse CFG.
    """
    # Reverse CFG: successors of n are the CFG predecessors of n.
    radj: Dict[int, List[int]] = {n.id: [] for n in cfg.nodes}
    rpred: Dict[int, List[int]] = {n.id: [] for n in cfg.nodes}
    for e in cfg.edges:
        if e.source == e.target:
            continue  # self-loops carry no dominance information
        radj[e.target].append(e.source)
        rpred[e.source].append(e.target)

    root = cfg.exit_id
    post = _postorder(radj, root)
    number = {n: i for i, n in enumerate(post)}  # postorder number
    rpo = list(reversed(post))  # reverse postorder: root first

    ipdom: Dict[int, int] = {root: root}

    def intersect(a: int, b: int) -> int:
        while a != b:
            while number[a] < number[b]:
                a = ipdom[a]
            while number[b] < number[a]:
                b = ipdom[b]
        return a

    changed = True
    while changed:
        changed = False
        for node in rpo:
            if node == root:
                continue
            preds = [p for p in rpred[node] if p in ipdom]
            if not preds:
                continue
            new = preds[0]
            for p in preds[1:]:
                new = intersect(new, p)
            if ipdom.get(node) != new:
                ipdom[node] = new
                changed = True

    return ipdom


def control_dependence(cfg: ControlFlowGraph) -> List[Tuple[int, int]]:
    """CDG edges ``(branch_node, dependent_node)`` per Ferrante–Ottenstein–
    Warren, plus ENTRY-region edges for nodes with no other controller."""
    ipdom = post_dominators(cfg)

    deps: Set[Tuple[int, int]] = set()
    for e in cfg.edges:
        a, b = e.source, e.target
        if a == b:
            continue
        # b post-dominates a iff b is an ancestor of a in the pdom tree.
        runner = b
        stop = ipdom.get(a)
        # Walk from b up the post-dominator tree to (not including) ipdom(a).
        while runner != stop and runner != a:
            deps.add((a, runner))
            nxt = ipdom.get(runner)
            if nxt is None or nxt == runner:
                break
            runner = nxt

    # ENTRY as the region root for otherwise-uncontrolled nodes.
    controlled = {t for (_, t) in deps}
    for n in cfg.nodes:
        if n.id in (cfg.entry_id, cfg.exit_id):
            continue
        if n.id not in controlled:
            deps.add((cfg.entry_id, n.id))

    return sorted(deps)
