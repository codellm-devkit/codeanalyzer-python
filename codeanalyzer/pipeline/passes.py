from pathlib import Path
from typing import Dict, List

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import PyApplication, PyExternalSymbol
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.semantic_analysis.pycg import PyCG, PyCGExceptions
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
