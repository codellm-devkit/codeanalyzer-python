"""A small v2 :class:`PyApplication` carried through the real L1 → L3 pipeline,
so the Neo4j projection tests exercise the whole overlay: a module, a class with
inheritance + a method + an attribute + an inner class, functions with
decorators + call sites + local variables, module variables, imports, a call
graph with a resolved edge and a ghost edge, and — new at level 3 — each
callable's CPG ``body``/``cfg``/``cdg``/``ddg``.

The symbol table is built from a real (temporary) source file so
``build_function_pdgs`` can recover each callable's AST; ``assign_ids`` +
``populate_l1_body`` + ``build_function_pdgs`` (syntactic oracle) + ``emit_l3_body``
then populate the v2 tree. The tests need neither a checked-in fixture tree nor a
virtualenv — they stay fast and deterministic.

``make_sample_app`` returns ``(app, sig_to_id)``: the projection
(``project(app, app_name, sig_to_id)``) needs the signature → ``can://`` id map
that ``assign_ids`` produced.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, Tuple

from codeanalyzer.dataflow.builder import build_function_pdgs, emit_l3_body
from codeanalyzer.dataflow.syntactic import SyntacticOracle
from codeanalyzer.schema import PyApplication, PyExternalSymbol
from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder

# A tiny program with every symbol-table shape the projection walks, plus enough
# control flow (an ``if``) and def-use (``message``/``y``) that each recovered
# callable yields non-empty CFG / CDG / DDG at level 3.
_SOURCE = '''import os

CONFIG = {}


def trace(fn):
    return fn


class BaseService:
    pass


class Service(BaseService):
    name: str

    def announce(self, flag):
        message = build(flag)
        if flag:
            message = message + "!"
        return message

    class Inner:
        pass


@trace
def helper(flag):
    svc = Service()
    result = svc.announce(flag)
    return result


@trace
def build(x):
    y = x
    return y
'''


def make_sample_app() -> Tuple[PyApplication, Dict[str, str]]:
    """Build the v2 sample application with its level-3 CPG overlay emitted.

    Returns ``(app, sig_to_id)`` — the projection consumes both.
    """
    workdir = Path(tempfile.mkdtemp(prefix="sample-graph-app-"))
    source_file = workdir / "service.py"
    source_file.write_text(_SOURCE, encoding="utf-8")

    module = SymbolTableBuilder(workdir, None).build_pymodule_from_file(source_file)
    app = PyApplication(symbol_table={"service.py": module})

    # A resolved call-graph edge (both endpoints declared) and a ghost edge whose
    # target is a third-party member — materialized as a :PyExternal node.
    app.call_graph = [
        PyCallEdge(
            source="service.helper",
            target="service.Service.announce",
            weight=1,
            provenance=["jedi"],
        ),
        PyCallEdge(
            source="service.helper",
            target="os.getcwd",
            weight=2,
            provenance=["jedi", "pycg"],
        ),
    ]
    app.external_symbols = {
        "os.getcwd": PyExternalSymbol(name="getcwd", module="os")
    }

    # Identity + L1 bodies, then the intraprocedural (syntactic) L3 overlay.
    sig_to_id = assign_ids(app, "sample-app")
    populate_l1_body(app)
    infos, _func_asts = build_function_pdgs(
        app, k=3, oracle_factory=lambda c, fast: SyntacticOracle()
    )
    emit_l3_body(app, infos, sig_to_id, {"cfg", "dfg", "pdg"})

    return app, sig_to_id
