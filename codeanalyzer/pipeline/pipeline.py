import time

from codeanalyzer.pipeline.context import AnalysisContext
from codeanalyzer.pipeline.passes import (
    _pass_call_graph, _pass_interproc_dataflow,
    _pass_intraproc_dataflow, _pass_symbol_table,
)
from codeanalyzer.provenance import analyzer_info
from codeanalyzer.schema import Analysis
from codeanalyzer.utils import logger


class AnalysisPipeline:
    """Fluent chain of analysis passes over a shared AnalysisContext.

    Each ``.with_*`` runs one pass through the shared ``_run`` gate: a pass
    below its intrinsic ``min_level`` is a logged no-op. ``.build()`` assembles
    the ``Analysis`` envelope from the produced context.
    """

    def __init__(self, ctx: AnalysisContext):
        self.ctx = ctx

    def with_symbol_table(self):
        return self._run("symbol_table", 1, _pass_symbol_table)

    def with_call_graph(self):
        return self._run("call_graph", 1, _pass_call_graph)

    def with_intraproc_dataflow(self):
        return self._run("intraproc_dataflow", 3, _pass_intraproc_dataflow)

    def with_interproc_dataflow(self):
        return self._run("interproc_dataflow", 4, _pass_interproc_dataflow)

    def _run(self, name, min_level, fn):
        if self.ctx.analysis_level < min_level:
            logger.info("⏭️  %s: skipped (level %d < %d)", name,
                        self.ctx.analysis_level, min_level)
            return self
        t0 = time.perf_counter()
        fn(self.ctx)
        logger.info("✅ %s: %.1fs", name, time.perf_counter() - t0)
        return self

    def build(self) -> Analysis:
        return Analysis(
            max_level=self.ctx.analysis_level,
            k_limit=self.ctx.options.graph_field_depth
            if self.ctx.analysis_level >= 3 else None,
            analyzer=analyzer_info(self.ctx.analysis_level),
            application=self.ctx.app,
        )
