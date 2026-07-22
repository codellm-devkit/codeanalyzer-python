from pathlib import Path
from typing import Dict, List

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.pipeline.context import AnalysisContext
from codeanalyzer.pipeline.symbol_table import build_symbol_table
from codeanalyzer.provenance import repository_info
from codeanalyzer.schema import PyApplication, PyExternalSymbol
from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.schema.call_graph_ids import reidentify_call_graph
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.l2_callees import backfill_callees
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.semantic_analysis.call_graph import (
    filter_external_edges, jedi_call_graph_edges, merge_edges,
    resolve_unresolved_constructors,
)
from codeanalyzer.semantic_analysis.pycg import PyCG, PyCGExceptions
from codeanalyzer.syntactic_analysis.import_resolver import resolve_imports
from codeanalyzer.utils import logger


def home_external_symbols(
    app: PyApplication, app_id: str, sig_to_id: Dict[str, str]
) -> Dict[str, PyExternalSymbol]:
    """Home every call-graph endpoint that is not a declared callable onto a
    ``can://…/@external/<module>/<name>`` id. Moved verbatim from
    ``Codeanalyzer._home_external_symbols`` (static method, no ``self``)."""
    externals: Dict[str, PyExternalSymbol] = {}
    for edge in app.call_graph:
        for sig in (edge.src, edge.dst):
            if sig in sig_to_id:
                continue
            module, name = sig.rsplit(".", 1) if "." in sig else (None, sig)
            ext_id = f"{app_id}/@external/{module}/{name}" if module else \
                f"{app_id}/@external/{name}"
            sig_to_id[sig] = ext_id
            externals[ext_id] = PyExternalSymbol(id=ext_id, name=name, module=module)
    return externals


def pycg_call_graph_edges(
    project_dir: Path, symbol_table, jedi_edges, options: AnalysisOptions
) -> List[PyCallEdge]:
    """Build PyCG-resolved call edges, degrading to Jedi-only on failure. Moved
    from ``Codeanalyzer._get_pycg_call_graph`` (``self.X`` -> ``options.X`` /
    ``project_dir``)."""
    try:
        pycg = PyCG(
            project_dir,
            skip_tests=options.skip_tests,
            shard=options.pycg_shard,
            shard_ceiling=options.pycg_shard_ceiling,
            shard_timeout=options.pycg_shard_timeout,
            shard_strategy=options.pycg_shard_strategy,
            max_iter=options.pycg_max_iter,
            using_ray=options.using_ray,
        )
        return pycg.build_call_graph_edges(symbol_table, jedi_edges=jedi_edges)
    except PyCGExceptions.PyCGImportError as exc:
        logger.warning(f"PyCG not installed — level 2 edges will be Jedi-only: {exc}")
        return []
    except PyCGExceptions.PyCGAnalysisError as exc:
        logger.warning(f"PyCG analysis failed — level 2 edges will be Jedi-only: {exc}")
        logger.debug("PyCG full traceback:", exc_info=True)
        return []


def _pass_symbol_table(ctx: AnalysisContext) -> None:
    symbol_table = build_symbol_table(
        ctx.project_dir, ctx.virtualenv, ctx.options, ctx.cached_symbol_table
    )
    resolve_unresolved_constructors(symbol_table)
    ctx.symbol_table = symbol_table


def _pass_call_graph(ctx: AnalysisContext) -> None:
    assert ctx.symbol_table is not None, "call_graph pass requires the symbol-table pass"
    st = ctx.symbol_table

    call_graph = list(jedi_call_graph_edges(st))
    if ctx.analysis_level >= 2:
        pycg_edges = pycg_call_graph_edges(
            ctx.project_dir, st, call_graph, ctx.options
        )
        call_graph = merge_edges(call_graph, pycg_edges)
    call_graph = filter_external_edges(call_graph, st)
    call_graph.sort(key=lambda e: (e.src, e.dst))

    app = PyApplication.builder().symbol_table(st).call_graph(call_graph).build()
    resolve_imports(app, ctx.project_dir)
    app.repository = repository_info(ctx.project_dir)

    sig_to_id = assign_ids(app, ctx.app_name)
    app.external_symbols = home_external_symbols(app, app.id, sig_to_id)
    populate_l1_body(app)
    if ctx.analysis_level >= 2:
        backfill_callees(app, sig_to_id)
    reidentify_call_graph(app, sig_to_id)

    ctx.app = app
    ctx.sig_to_id = sig_to_id


def _pass_intraproc_dataflow(ctx: AnalysisContext) -> None:
    assert ctx.app is not None and ctx.sig_to_id is not None, \
        "intraproc dataflow pass requires the call-graph pass"
    from codeanalyzer.dataflow.builder import build_function_pdgs, emit_l3_body
    from codeanalyzer.dataflow.syntactic import SyntacticOracle

    infos, _func_asts = build_function_pdgs(
        ctx.app,
        k=ctx.options.graph_field_depth,
        oracle_factory=lambda c, fast: SyntacticOracle(),
    )
    emit_l3_body(ctx.app, infos, ctx.sig_to_id, set(ctx.options.graphs.split(",")))
    ctx.infos = infos


def _pass_interproc_dataflow(ctx: AnalysisContext) -> None:
    assert ctx.app is not None and ctx.sig_to_id is not None, \
        "interproc dataflow pass requires the call-graph pass"
    assert ctx.infos is not None, \
        "interproc dataflow pass requires the intraproc pass (it reuses its PDGs)"
    from codeanalyzer.dataflow.builder import (
        _base_types, build_program_graphs, emit_ddg_pointsto_delta, emit_l4,
    )
    from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle

    ir = build_program_graphs(
        ctx.app,
        k=ctx.options.graph_field_depth,
        oracle_factory=lambda c, fast: make_alias_oracle(c, fast, _base_types(c)),
    )
    emit_l4(ctx.app, ir, ctx.sig_to_id)
    emit_ddg_pointsto_delta(ctx.app, ctx.infos, ir, ctx.sig_to_id)
    ctx.ir = ir
