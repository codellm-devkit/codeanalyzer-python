from pathlib import Path

import pytest

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.config import OutputFormat
from codeanalyzer.pipeline import AnalysisContext, AnalysisPipeline
from codeanalyzer.pipeline.passes import (
    _pass_symbol_table, _pass_call_graph,
    _pass_intraproc_dataflow, _pass_interproc_dataflow,
    home_external_symbols,
)
from codeanalyzer.pipeline.symbol_table import build_symbol_table
from codeanalyzer.schema import Analysis, PyApplication
from codeanalyzer.schema.py_schema import PyCallEdge


def _opts(tmp_path):
    return AnalysisOptions(
        input=tmp_path, output=None, format=OutputFormat.JSON,
        analysis_level=1, skip_tests=True, no_venv=True,
    )


def test_context_defaults(tmp_path):
    ctx = AnalysisContext(
        options=_opts(tmp_path), project_dir=Path(tmp_path),
        virtualenv=None, analysis_level=1, app_name="proj",
    )
    assert ctx.cached_symbol_table == {}
    assert ctx.symbol_table is None
    assert ctx.app is None
    assert ctx.sig_to_id is None
    assert ctx.infos is None
    assert ctx.ir is None


def test_build_symbol_table_free_function(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    opts = AnalysisOptions(
        input=proj, output=None, format=OutputFormat.JSON,
        analysis_level=1, skip_tests=True, no_venv=True,
    )
    table = build_symbol_table(proj, None, opts, cached_symbol_table={})
    assert "m.py" in table


def test_home_external_symbols_homes_undeclared_endpoints():
    app = PyApplication.builder().symbol_table({}).call_graph([]).build()
    app.id = "can://python/proj"
    # a call edge whose endpoints are not declared callables
    app.call_graph = [PyCallEdge(src="a.b", dst="os.getcwd", prov=["jedi"], weight=1)]
    sig_to_id = {}
    externals = home_external_symbols(app, app.id, sig_to_id)
    assert "can://python/proj/@external/os/getcwd" in externals
    assert sig_to_id["os.getcwd"] == "can://python/proj/@external/os/getcwd"


def _ctx(tmp_path, level, src="def g():\n    return 1\ndef f():\n    return g()\n"):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text(src, encoding="utf-8")
    opts = AnalysisOptions(
        input=proj, output=None, format=OutputFormat.JSON,
        analysis_level=level, skip_tests=True, no_venv=True,
    )
    return AnalysisContext(
        options=opts, project_dir=proj, virtualenv=None,
        analysis_level=level, app_name="proj",
    )


def test_pass_symbol_table_populates_symbol_table(tmp_path):
    ctx = _ctx(tmp_path, 1)
    _pass_symbol_table(ctx)
    assert ctx.symbol_table is not None and "m.py" in ctx.symbol_table


def test_pass_call_graph_populates_app_and_ids(tmp_path):
    ctx = _ctx(tmp_path, 1)
    _pass_symbol_table(ctx)
    _pass_call_graph(ctx)
    assert ctx.app is not None and ctx.sig_to_id is not None
    assert ctx.app.id.startswith("can://python/proj")


def test_pass_call_graph_requires_symbol_table(tmp_path):
    ctx = _ctx(tmp_path, 1)
    with pytest.raises(AssertionError):
        _pass_call_graph(ctx)


def test_pass_interproc_requires_infos(tmp_path):
    ctx = _ctx(tmp_path, 4)
    _pass_symbol_table(ctx)
    _pass_call_graph(ctx)
    # intraproc pass deliberately skipped -> infos is still None
    with pytest.raises(AssertionError):
        _pass_interproc_dataflow(ctx)


def test_pass_intraproc_populates_infos(tmp_path):
    ctx = _ctx(tmp_path, 3, src="def g(x):\n    return x\ndef f(a):\n    b = a\n    g(b)\n    return b\n")
    _pass_symbol_table(ctx)
    _pass_call_graph(ctx)
    _pass_intraproc_dataflow(ctx)
    assert ctx.infos is not None


def test_pipeline_gating_skips_dataflow_below_level(tmp_path):
    ctx = _ctx(tmp_path, 2)
    analysis = (
        AnalysisPipeline(ctx)
        .with_symbol_table()
        .with_call_graph()
        .with_intraproc_dataflow()   # min_level 3 -> skipped at level 2
        .with_interproc_dataflow()   # min_level 4 -> skipped at level 2
        .build()
    )
    assert isinstance(analysis, Analysis)
    assert ctx.app is not None and ctx.sig_to_id is not None  # call-graph pass RAN
    assert ctx.infos is None and ctx.ir is None            # gates fired
    assert analysis.max_level == 2
    assert analysis.k_limit is None                         # L3+ only


def test_pipeline_with_methods_return_self(tmp_path):
    ctx = _ctx(tmp_path, 1)
    pipe = AnalysisPipeline(ctx)
    # both the run path (symbol_table/call_graph at L1) and the gated skip path
    # (intraproc/interproc gated off at L1) return self
    assert (
        pipe.with_symbol_table()
            .with_call_graph()
            .with_intraproc_dataflow()
            .with_interproc_dataflow()
    ) is pipe


def test_pipeline_level4_runs_intraproc_before_interproc(tmp_path):
    ctx = _ctx(tmp_path, 4, src="def g(x):\n    return x\ndef f(a):\n    b = g(a)\n    return b\n")
    (AnalysisPipeline(ctx)
        .with_symbol_table().with_call_graph()
        .with_intraproc_dataflow().with_interproc_dataflow().build())
    assert ctx.infos is not None    # L3 ran before L4 (reuse precondition held)
    assert ctx.ir is not None
