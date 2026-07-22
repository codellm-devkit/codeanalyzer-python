from pathlib import Path

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.config import OutputFormat
from codeanalyzer.pipeline import AnalysisContext
from codeanalyzer.pipeline.symbol_table import build_symbol_table


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
