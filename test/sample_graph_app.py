"""A small v2 :class:`PyApplication` carried through the real L1 → L4 pipeline,
so the Neo4j projection tests exercise the whole overlay: a module, a class with
inheritance + a method + an attribute + an inner class, functions with
decorators + call sites + local variables, module variables, imports, a call
graph with a resolved edge and a ghost edge, each callable's CPG
``body``/``cfg``/``cdg``/``ddg`` (level 3), and — new at level 4 — the
interprocedural ``param_in``/``param_out``/``summary`` param-passing overlay plus
the points-to ``ddg`` delta.

The symbol table is built from a real (temporary) source file so
``build_function_pdgs`` can recover each callable's AST; ``assign_ids`` +
``populate_l1_body`` + ``build_function_pdgs`` (syntactic oracle) + ``emit_l3_body``
populate the v2 tree at L3, then ``build_program_graphs`` (Scalpel-primary alias
oracle) + ``emit_l4`` + ``emit_ddg_pointsto_delta`` layer the L4 delta on top. The
tests need neither a checked-in fixture tree nor a virtualenv — they stay fast and
deterministic.

``make_sample_app`` returns ``(app, sig_to_id)``: the projection
(``project(app, app_name, sig_to_id)``) needs the signature → ``can://`` id map
that ``assign_ids`` produced.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, Tuple

from codeanalyzer.dataflow.builder import (
    _base_types,
    build_function_pdgs,
    build_program_graphs,
    emit_ddg_pointsto_delta,
    emit_l3_body,
    emit_l4,
)
from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle
from codeanalyzer.dataflow.syntactic import SyntacticOracle
from codeanalyzer.schema import PyApplication, PyExternalSymbol
from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.l2_callees import backfill_callees
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.semantic_analysis.call_graph import (
    iter_callables_in_symbol_table,
)
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder

# A tiny program with every symbol-table shape the projection walks: a base class
# and a subclass (inheritance → PY_EXTENDS), a method + attribute + inner class,
# decorated functions, a resolved call site (``build(flag)`` → ``service.build``),
# and enough control flow (an ``if``) and def-use (``message``/``y``) that each
# recovered callable yields a non-empty CFG / CDG / DDG. ``build(flag)`` is a
# resolved interprocedural call with a parameter, so the L4 SDG carries
# PARAM_IN / PARAM_OUT / SUMMARY edges over ``build``'s pass-through.
_SOURCE = """import os

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
"""


def _qualify_base_classes(module) -> None:
    """Resolve each class's bare base-class names to their in-module signatures
    (``BaseService`` → ``service.BaseService``), mirroring what a semantic
    resolution pass does, so the Neo4j PY_EXTENDS edge lands on the declared base
    class's ``can://`` id rather than dangling on an unresolved bare name."""
    name_to_sig = {cls.name: cls.signature for cls in (module.types or {}).values()}
    for cls in (module.types or {}).values():
        cls.base_classes = [name_to_sig.get(b, b) for b in (cls.base_classes or [])]


def make_sample_app() -> Tuple[PyApplication, Dict[str, str]]:
    """Build the v2 sample application with its level-3 CPG overlay and level-4
    interprocedural delta emitted.

    Returns ``(app, sig_to_id)`` — the projection consumes both.
    """
    workdir = Path(tempfile.mkdtemp(prefix="sample-graph-app-"))
    source_file = workdir / "service.py"
    source_file.write_text(_SOURCE, encoding="utf-8")

    module = SymbolTableBuilder(workdir, None).build_pymodule_from_file(source_file)
    _qualify_base_classes(module)
    app = PyApplication(symbol_table={"service.py": module})

    # Two independent builds of this fixture must project byte-identical rows (the
    # determinism guard). The only environment-derived fields are the file mtime
    # and the absolute callable ``path`` (both carry the random temp dir); pin the
    # mtime and rewrite ``path`` to the portable project-relative "service.py".
    # Nothing in L1–L4 depends on either — the graph build reads ``module.file_path``,
    # which stays pointed at the real temp file.
    module.last_modified = 1.0
    for c in iter_callables_in_symbol_table(app.symbol_table):
        c.path = "service.py"

    # A resolved call-graph edge (both endpoints declared) and a ghost edge whose
    # target is a third-party member — materialized as a :PyExternal node.
    app.call_graph = [
        PyCallEdge(
            src="service.helper",
            dst="service.Service.announce",
            weight=1,
            prov=["jedi"],
        ),
        PyCallEdge(
            src="service.helper",
            dst="os.getcwd",
            weight=2,
            prov=["jedi", "pycg"],
        ),
    ]

    # Identity + L1 bodies, then the intraprocedural (syntactic) L3 overlay.
    sig_to_id = assign_ids(app, "sample-app")
    ext_id = "can://python/sample-app/@external/os/getcwd"
    app.external_symbols = {
        ext_id: PyExternalSymbol(id=ext_id, name="getcwd", module="os")
    }
    sig_to_id["os.getcwd"] = ext_id
    populate_l1_body(app)
    # Mirror the real pipeline (core.py runs this at -a 2+): body `call` nodes get
    # their resolved `callee`. Without it PY_RESOLVES_TO never fires, since #120
    # sources that edge from the body node rather than from `call_sites[]`.
    backfill_callees(app, sig_to_id)
    syntactic_infos, _func_asts = build_function_pdgs(
        app, k=3, oracle_factory=lambda c, fast: SyntacticOracle()
    )
    emit_l3_body(app, syntactic_infos, sig_to_id, {"cfg", "dfg", "pdg"})

    # The L4 interprocedural delta, layered on top of L3 (L3 ⊆ L4 by construction):
    # param vertices + summary + param_in/param_out, then the points-to ddg delta.
    ir = build_program_graphs(
        app,
        k=3,
        oracle_factory=lambda c, fast: make_alias_oracle(c, fast, _base_types(c)),
    )
    emit_l4(app, ir, sig_to_id)
    emit_ddg_pointsto_delta(app, syntactic_infos, ir, sig_to_id)

    # Repository-artifact layer (application-anchored, all levels): one manifest
    # artifact carrying a declared dependency and a config artifact carrying a
    # defined key — so the Neo4j catalog-coverage guard exercises PyArtifact /
    # PyDependency / PyConfigKey and their containment edges.
    _attach_sample_artifacts(app)

    return app, sig_to_id


def _attach_sample_artifacts(app: PyApplication) -> None:
    from codeanalyzer.schema.ids import (
        artifact_id,
        config_key_id,
        dependency_id,
    )
    from codeanalyzer.schema.py_schema import (
        PyArtifact,
        PyConfigKey,
        PyDependency,
    )

    manifest_id = artifact_id("sample-app", "pyproject.toml")
    manifest = PyArtifact(
        id=manifest_id,
        path="pyproject.toml",
        artifact_kind="build_manifest",
        format="toml",
        content_hash="0" * 64,
        size_bytes=42,
        text='[project]\ndependencies = ["requests>=2"]\n',
        text_encoding="utf-8",
    )
    manifest.dependencies = {
        "requests": PyDependency(
            id=dependency_id(manifest_id, "requests"),
            name="requests",
            version_spec=">=2",
            ecosystem="pypi",
            scope="runtime",
            direct=True,
        )
    }

    env_id = artifact_id("sample-app", ".env")
    env = PyArtifact(
        id=env_id,
        path=".env",
        artifact_kind="configuration",
        content_hash="1" * 64,
        size_bytes=20,
        text="DB_URL=${DB_HOST}\n",
        text_encoding="utf-8",
    )
    env.config_keys = {
        "DB_URL": PyConfigKey(
            id=config_key_id(env_id, "DB_URL"),
            key="DB_URL",
            namespace="env",
            value="${DB_HOST}",
            references=["env:DB_HOST"],
        )
    }

    app.artifacts = {"pyproject.toml": manifest, ".env": env}
