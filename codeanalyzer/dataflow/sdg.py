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

"""Stage 7 of the level-3 dataflow ladder: SDG assembly (Horwitz–Reps–Binkley).

Parameter-passing structure per function and callsite:

- **formal_in** nodes: one per parameter (var = the parameter name), one per
  captured variable (``<capture>:name``), one per transitively-read global
  (``<global>:module::name``);
- **formal_out** nodes: the return value (``<return>``), each caller-visibly
  mutated parameter, each written global;
- **actual_in / actual_out** nodes at each callsite, mirroring the callee's
  formals that the callsite binds (positional/keyword-matched arguments, the
  receiver as ``self``, globals from the callee's summary footprint);
- closure captures bind at the nested function's *definition* statement: an
  ``actual_in`` at the def node, ``PARAM_IN`` to the nested callable's
  ``<capture>`` formal.

Parameter nodes share the owning function's node-id space, allocated after
EXIT (the CFG keeps its ``ENTRY = 0 … EXIT = last CFG id`` contract; parameter
nodes are PDG/SDG-level, deterministically ordered). Intra-function wiring
(defs → formal_out, formal_in → uses, defs → actual_in, actual_out → callsite)
is emitted as ordinary DDG/CDG edges of the function's PDG; cross-function
``CALL`` / ``PARAM_IN`` / ``PARAM_OUT`` edges and same-signature ``SUMMARY``
edges (actual_in → actual_out, encoding the callee's transitive flow) form the
``sdg_edges`` section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from codeanalyzer.dataflow.access_paths import RETURN_PATH, base_of, interferes, suffix_of
from codeanalyzer.dataflow.defuse import DDGEdge
from codeanalyzer.dataflow.pdg import FunctionPDG, PDGEdge
from codeanalyzer.dataflow.summaries import (
    CallSite,
    FunctionInfo,
    FunctionSummary,
    solve_function,
)

CAPTURE_PREFIX = "<capture>:"
GLOBAL_PREFIX = "<global>:"


@dataclass
class ParamNode:
    id: int
    kind: str  # formal_in | formal_out | actual_in | actual_out
    var: str
    call_node: Optional[int] = None  # owning callsite statement (actuals)
    start_line: int = -1
    end_line: int = -1


@dataclass(frozen=True)
class SDGEdge:
    source_sig: str
    source_node: int
    target_sig: str
    target_node: int
    type: str  # CALL | PARAM_IN | PARAM_OUT | SUMMARY
    var: Optional[str] = None


@dataclass
class FunctionGraphs:
    """One callable's complete level-3 graphs, ready for emission."""

    pdg: FunctionPDG
    ddg: List[DDGEdge] = field(default_factory=list)  # augmented, final
    param_nodes: List[ParamNode] = field(default_factory=list)
    extra_edges: List[PDGEdge] = field(default_factory=list)  # param wiring
    summary: Optional[FunctionSummary] = None


@dataclass
class ProgramGraphsIR:
    functions: Dict[str, FunctionGraphs] = field(default_factory=dict)
    sdg_edges: List[SDGEdge] = field(default_factory=list)
    k_limit: int = 3


def _formal_key_to_var(key: str) -> str:
    kind, _, name = key.partition(":")
    if kind == "param":
        return name
    if kind == "capture":
        return CAPTURE_PREFIX + name
    return GLOBAL_PREFIX + name


class _FunctionAssembler:
    """Allocates parameter nodes and wiring edges for one function."""

    def __init__(self, info: FunctionInfo, summary: FunctionSummary, facts, ddg):
        self.info = info
        self.summary = summary
        self.facts = facts
        self.ddg = ddg
        self.cfg = info.pdg.cfg
        self.scope = info.pdg.scope
        self.next_id = len(self.cfg.nodes)
        self.param_nodes: List[ParamNode] = []
        self.extra: List[PDGEdge] = []
        self.formal_in: Dict[str, int] = {}  # var -> node id
        self.formal_out: Dict[str, int] = {}
        # (call_node, var) -> node id
        self.actual_in: Dict[Tuple[int, str], int] = {}
        self.actual_out: Dict[Tuple[int, str], int] = {}
        entry = self.cfg.node_by_id(self.cfg.entry_id)
        exit_ = self.cfg.node_by_id(self.cfg.exit_id)
        self._entry_span = (entry.start_line, entry.end_line)
        self._exit_span = (exit_.start_line, exit_.end_line)

    def _alloc(self, kind: str, var: str, span, call_node=None) -> int:
        nid = self.next_id
        self.next_id += 1
        self.param_nodes.append(
            ParamNode(
                id=nid, kind=kind, var=var, call_node=call_node,
                start_line=span[0], end_line=span[1],
            )
        )
        return nid

    # ---------------------------------------------------------------- formals

    def build_formals(self) -> None:
        scope, summary = self.scope, self.summary
        params = list(scope.params)
        for p in params:
            self.formal_in[p] = self._alloc("formal_in", p, self._entry_span)
        for c in sorted(scope.captures):
            var = CAPTURE_PREFIX + c
            self.formal_in[var] = self._alloc("formal_in", var, self._entry_span)
        for g in sorted(summary.global_reads):
            var = GLOBAL_PREFIX + g
            self.formal_in[var] = self._alloc("formal_in", var, self._entry_span)

        self.formal_out[RETURN_PATH] = self._alloc(
            "formal_out", RETURN_PATH, self._exit_span
        )
        for p in sorted(summary.mutated_params):
            self.formal_out[p] = self._alloc("formal_out", p, self._exit_span)
        for g in sorted(summary.global_writes):
            var = GLOBAL_PREFIX + g
            self.formal_out[var] = self._alloc("formal_out", var, self._exit_span)

        # Wiring: formal_in → first uses (mirror the ENTRY-def DDG edges).
        entry = self.cfg.entry_id
        for e in self.ddg:
            if e.source != entry:
                continue
            b = base_of(e.var)
            if b in self.formal_in:
                fid = self.formal_in[b]
            elif CAPTURE_PREFIX + b in self.formal_in:
                fid = self.formal_in[CAPTURE_PREFIX + b]
            elif "::" in b and GLOBAL_PREFIX + b in self.formal_in:
                fid = self.formal_in[GLOBAL_PREFIX + b]
            else:
                continue
            self.extra.append(PDGEdge(source=fid, target=e.target, type="DDG", var=e.var))

        # Wiring: defining nodes → formal_out.
        param_names = set(scope.params)
        if scope.self_name:
            param_names.add(scope.self_name)
        for nid, f in self.facts.items():
            if nid == entry:
                continue
            if RETURN_PATH in f.defs:
                self.extra.append(
                    PDGEdge(
                        source=nid,
                        target=self.formal_out[RETURN_PATH],
                        type="DDG",
                        var=RETURN_PATH,
                    )
                )
            for d in f.defs:
                b = base_of(d)
                if "::" in b and GLOBAL_PREFIX + b in self.formal_out:
                    self.extra.append(
                        PDGEdge(
                            source=nid,
                            target=self.formal_out[GLOBAL_PREFIX + b],
                            type="DDG",
                            var=d,
                        )
                    )
                elif b in param_names and suffix_of(d) and b in self.formal_out:
                    self.extra.append(
                        PDGEdge(source=nid, target=self.formal_out[b], type="DDG", var=d)
                    )

    # ---------------------------------------------------------------- actuals

    def _defs_reaching_call_matching(self, call_node: int, path: Optional[str]):
        """Sources of DDG in-edges of the call node whose var matches the
        actual's access path (all of them when the actual is an expression)."""
        sources = []
        for e in self.ddg:
            if e.target != call_node:
                continue
            if path is None or interferes(e.var, path) or interferes(path, e.var):
                sources.append((e.source, e.var))
        return sources

    def build_actuals(
        self,
        summaries: Dict[str, FunctionSummary],
        formal_ids: Dict[str, Dict[str, int]],
        sdg_edges: List[SDGEdge],
    ) -> None:
        sig = self.info.signature
        node_span = {
            n.id: (n.start_line, n.end_line) for n in self.cfg.nodes
        }

        for cs in sorted(self.info.call_sites, key=lambda c: (c.node_id, c.targets)):
            span = node_span.get(cs.node_id, (-1, -1))
            for target in cs.targets:
                callee_summary = summaries.get(target)
                callee_formals = formal_ids.get(target)
                if callee_summary is None or callee_formals is None:
                    continue  # external — conservative pass-through already applies

                # CALL: callsite statement → callee ENTRY.
                sdg_edges.append(
                    SDGEdge(
                        source_sig=sig, source_node=cs.node_id,
                        target_sig=target, target_node=0, type="CALL",
                    )
                )

                bound_in: Dict[str, int] = {}   # formal key -> actual_in id
                bound_out: Dict[str, int] = {}  # formal key -> actual_out id

                # Argument actual_ins for the callee formals this site binds.
                for param, path in cs.arg_paths:
                    if param not in callee_formals:
                        continue
                    key = (cs.node_id, f"{target}::{param}")
                    if key not in self.actual_in:
                        aid = self._alloc("actual_in", param, span, cs.node_id)
                        self.actual_in[key] = aid
                        self.extra.append(
                            PDGEdge(source=cs.node_id, target=aid, type="CDG")
                        )
                        for src, var in self._defs_reaching_call_matching(
                            cs.node_id, path
                        ):
                            self.extra.append(
                                PDGEdge(source=src, target=aid, type="DDG", var=var)
                            )
                    bound_in[f"param:{param}"] = self.actual_in[key]
                    sdg_edges.append(
                        SDGEdge(
                            source_sig=sig, source_node=self.actual_in[key],
                            target_sig=target,
                            target_node=callee_formals[param],
                            type="PARAM_IN", var=param,
                        )
                    )

                # Global actual_ins from the callee's read footprint.
                for g in sorted(callee_summary.global_reads):
                    fvar = GLOBAL_PREFIX + g
                    if fvar not in callee_formals:
                        continue
                    key = (cs.node_id, f"{target}::{fvar}")
                    if key not in self.actual_in:
                        aid = self._alloc("actual_in", fvar, span, cs.node_id)
                        self.actual_in[key] = aid
                        self.extra.append(
                            PDGEdge(source=cs.node_id, target=aid, type="CDG")
                        )
                        for src, var in self._defs_reaching_call_matching(
                            cs.node_id, g
                        ):
                            self.extra.append(
                                PDGEdge(source=src, target=aid, type="DDG", var=var)
                            )
                    bound_in[f"global:{g}"] = self.actual_in[key]
                    sdg_edges.append(
                        SDGEdge(
                            source_sig=sig, source_node=self.actual_in[key],
                            target_sig=target, target_node=callee_formals[fvar],
                            type="PARAM_IN", var=fvar,
                        )
                    )

                # actual_outs: return, mutated bound params, written globals.
                out_specs: List[Tuple[str, str]] = [("return", RETURN_PATH)]
                for p in sorted(callee_summary.mutated_params):
                    if cs.arg_path_of(p) is not None:
                        out_specs.append((f"param:{p}", p))
                for g in sorted(callee_summary.global_writes):
                    out_specs.append((f"global:{g}", GLOBAL_PREFIX + g))

                callee_formal_outs = formal_ids.get(f"{target}<out>", {})
                for key_name, fvar in out_specs:
                    if fvar not in callee_formal_outs:
                        continue
                    key = (cs.node_id, f"{target}::out::{fvar}")
                    if key not in self.actual_out:
                        oid = self._alloc("actual_out", fvar, span, cs.node_id)
                        self.actual_out[key] = oid
                        self.extra.append(
                            PDGEdge(source=cs.node_id, target=oid, type="CDG")
                        )
                        self.extra.append(
                            PDGEdge(source=oid, target=cs.node_id, type="DDG", var=fvar)
                        )
                    bound_out[key_name] = self.actual_out[key]
                    sdg_edges.append(
                        SDGEdge(
                            source_sig=target,
                            source_node=callee_formal_outs[fvar],
                            target_sig=sig, target_node=self.actual_out[key],
                            type="PARAM_OUT", var=fvar,
                        )
                    )

                # SUMMARY: actual_in → actual_out per callee transitive flow.
                for in_key, out_key in sorted(callee_summary.flows):
                    a_in = bound_in.get(in_key)
                    a_out = bound_out.get(out_key)
                    if a_in is not None and a_out is not None:
                        sdg_edges.append(
                            SDGEdge(
                                source_sig=sig, source_node=a_in,
                                target_sig=sig, target_node=a_out,
                                type="SUMMARY", var=None,
                            )
                        )

        # Closure captures: bind at the nested callable's def statement.
        for def_node, nested_sig in sorted(self.info.nested_defs):
            nested_formals = formal_ids.get(nested_sig)
            if not nested_formals:
                continue
            span = node_span.get(def_node, (-1, -1))
            for fvar, fid in sorted(nested_formals.items()):
                if not fvar.startswith(CAPTURE_PREFIX):
                    continue
                name = fvar[len(CAPTURE_PREFIX):]
                key = (def_node, f"{nested_sig}::{fvar}")
                if key not in self.actual_in:
                    aid = self._alloc("actual_in", fvar, span, def_node)
                    self.actual_in[key] = aid
                    self.extra.append(PDGEdge(source=def_node, target=aid, type="CDG"))
                    for src, var in self._defs_reaching_call_matching(def_node, name):
                        self.extra.append(
                            PDGEdge(source=src, target=aid, type="DDG", var=var)
                        )
                sdg_edges.append(
                    SDGEdge(
                        source_sig=self.info.signature,
                        source_node=self.actual_in[key],
                        target_sig=nested_sig, target_node=fid,
                        type="PARAM_IN", var=fvar,
                    )
                )


def assemble_sdg(
    infos: Dict[str, FunctionInfo],
    summaries: Dict[str, FunctionSummary],
    k: int,
) -> ProgramGraphsIR:
    """Stitch every function's PDG into the whole-program SDG."""
    ir = ProgramGraphsIR(k_limit=k)

    # Pass 1: solve each function against the final summaries and lay out its
    # formal nodes (their ids must exist before callsites reference them).
    assemblers: Dict[str, _FunctionAssembler] = {}
    formal_ids: Dict[str, Dict[str, int]] = {}
    for sig in sorted(infos):
        info = infos[sig]
        summary, facts, ddg = solve_function(info, summaries)
        asm = _FunctionAssembler(info, summary, facts, ddg)
        asm.build_formals()
        assemblers[sig] = asm
        formal_ids[sig] = dict(asm.formal_in)
        formal_ids[f"{sig}<out>"] = dict(asm.formal_out)

    # Pass 2: callsite actuals and cross-function edges.
    sdg_edges: List[SDGEdge] = []
    for sig in sorted(assemblers):
        assemblers[sig].build_actuals(summaries, formal_ids, sdg_edges)

    for sig, asm in assemblers.items():
        ir.functions[sig] = FunctionGraphs(
            pdg=asm.info.pdg,
            ddg=asm.ddg,
            param_nodes=asm.param_nodes,
            extra_edges=asm.extra,
            summary=asm.summary,
        )

    ir.sdg_edges = sorted(
        set(sdg_edges),
        key=lambda e: (e.source_sig, e.source_node, e.target_sig, e.target_node, e.type, e.var or ""),
    )
    return ir
