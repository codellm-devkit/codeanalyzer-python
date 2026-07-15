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

"""Stage 6 of the level-3 dataflow ladder: bottom-up function summaries.

A summary is relational: which formal inputs (parameters, captures, read
globals) may flow to which formal outputs (the return value, caller-visible
parameter mutations, written globals). Summaries compose bottom-up over the
SCC condensation DAG of the call-graph oracle; within an SCC (mutual
recursion) all members iterate to a monotone fixpoint — the domains (formal
keys and qualified global names) are finite and effects only grow, so
termination is structural. k-limiting bounds the access-path vocabulary.

At statement granularity a callsite node is already a transformer (all its
defs depend on all its uses), so the composition step callee summaries
actually contribute is the *global footprint*: a callsite node gains the
callee's transitive global reads as uses and writes as defs, the reaching
definitions are re-solved, and flows are re-derived. External/unmodeled
callees default to conservative pass-through (their argument paths are
already weak-defined and used at the call statement).

Summary flow keys: ``param:NAME``, ``capture:NAME``, ``global:MODULE::NAME``
for inputs; ``return``, ``param:NAME`` (mutation), ``global:MODULE::NAME``
for outputs.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from codeanalyzer.dataflow.access_paths import RETURN_PATH, base_of, suffix_of
from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.dataflow.defuse import DDGEdge, ddg_edges
from codeanalyzer.dataflow.pdg import FunctionPDG
from codeanalyzer.dataflow.scc import strongly_connected_components


@dataclass(frozen=True)
class CallSite:
    """One resolved call at one CFG statement node (builder-provided)."""

    node_id: int
    targets: Tuple[str, ...]  # callee signatures declared in the symbol table
    # callee param name -> actual access path (None: a non-path expression)
    arg_paths: Tuple[Tuple[str, Optional[str]], ...] = ()
    line: int = -1

    def arg_path_of(self, param: str) -> Optional[str]:
        for name, path in self.arg_paths:
            if name == param:
                return path
        return None


@dataclass
class FunctionInfo:
    """Everything the interprocedural stages need about one callable."""

    signature: str
    pdg: FunctionPDG
    oracle: TypeBasedAliasOracle
    call_sites: List[CallSite] = field(default_factory=list)
    # Nested callables defined at a statement node: (def_node_id, nested_sig).
    nested_defs: List[Tuple[int, str]] = field(default_factory=list)


@dataclass
class FunctionSummary:
    global_reads: Set[str] = field(default_factory=set)
    global_writes: Set[str] = field(default_factory=set)
    mutated_params: Set[str] = field(default_factory=set)
    flows: Set[Tuple[str, str]] = field(default_factory=set)

    def __eq__(self, other):
        return (
            isinstance(other, FunctionSummary)
            and self.global_reads == other.global_reads
            and self.global_writes == other.global_writes
            and self.mutated_params == other.mutated_params
            and self.flows == other.flows
        )


def _is_global(path: str) -> bool:
    return "::" in base_of(path)


def augmented_facts(info: FunctionInfo, summaries: Dict[str, FunctionSummary]):
    """Per-node facts with callee global footprints injected at callsites."""
    facts = {nid: f for nid, f in info.pdg.facts.items()}
    out = {}
    for nid, f in facts.items():
        out[nid] = type(f)(defs=set(f.defs), uses=set(f.uses))
    for cs in info.call_sites:
        for target in cs.targets:
            s = summaries.get(target)
            if s is None:
                continue
            out[cs.node_id].uses |= s.global_reads
            out[cs.node_id].defs |= s.global_writes
    return out


def solve_function(
    info: FunctionInfo, summaries: Dict[str, FunctionSummary]
) -> Tuple[FunctionSummary, Dict[int, object], List[DDGEdge]]:
    """One summary iteration: inject callee footprints, re-solve reaching
    definitions, derive flows. Returns (summary, augmented facts, DDG)."""
    facts = augmented_facts(info, summaries)
    ddg = ddg_edges(info.pdg.cfg, facts, info.oracle)

    # Forward adjacency over DDG ∪ CDG (a statement transforms all its
    # inputs into all its outputs — statement-granularity posture).
    adj: Dict[int, List[int]] = {}
    for e in ddg:
        adj.setdefault(e.source, []).append(e.target)
    for e in info.pdg.edges:
        if e.type == "CDG":
            adj.setdefault(e.source, []).append(e.target)

    entry = info.pdg.cfg.entry_id
    scope = info.pdg.scope

    # Seeds: the ENTRY-def DDG edges, grouped by formal key.
    seeds: Dict[str, Set[int]] = {}
    for e in ddg:
        if e.source != entry:
            continue
        b = base_of(e.var)
        if b == scope.self_name or b in scope.params:
            key = f"param:{b}"
        elif b in scope.captures:
            key = f"capture:{b}"
        elif _is_global(e.var):
            key = f"global:{b}"
        else:
            continue
        seeds.setdefault(key, set()).add(e.target)

    def reach(start: Set[int]) -> Set[int]:
        seen: Set[int] = set()
        stack = list(start)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(adj.get(n, []))
        return seen

    summary = FunctionSummary()
    param_names = set(scope.params)
    if scope.self_name:
        param_names.add(scope.self_name)

    for nid, f in facts.items():
        if nid == entry:
            continue
        for d in f.defs:
            b = base_of(d)
            if _is_global(d):
                summary.global_writes.add(b)
            elif b in param_names and suffix_of(d):
                summary.mutated_params.add(b)
        for u in f.uses:
            if _is_global(u):
                summary.global_reads.add(base_of(u))

    for key, start in seeds.items():
        for nid in reach(start):
            f = facts[nid]
            if RETURN_PATH in f.defs:
                summary.flows.add((key, "return"))
            for d in f.defs:
                b = base_of(d)
                if _is_global(d):
                    summary.flows.add((key, f"global:{b}"))
                elif b in param_names and suffix_of(d):
                    summary.flows.add((key, f"param:{b}"))

    return summary, facts, ddg


def compute_summaries(
    infos: Dict[str, FunctionInfo],
    call_edges: List[Tuple[str, str]],
) -> Dict[str, FunctionSummary]:
    """Bottom-up composition over the SCC condensation DAG, monotone fixpoint
    within each SCC."""
    order = strongly_connected_components(sorted(infos), call_edges)
    summaries: Dict[str, FunctionSummary] = {}
    for scc in order:
        members = [s for s in scc if s in infos]
        changed = True
        while changed:
            changed = False
            for sig in members:
                new, _, _ = solve_function(infos[sig], summaries)
                if summaries.get(sig) != new:
                    summaries[sig] = new
                    changed = True
    return summaries
