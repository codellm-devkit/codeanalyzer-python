"""``stream_analysis_json`` streams ``application.artifacts`` like the other big
collections: same JSON content, never materialized in the envelope, and restored."""
import io
import json

import pytest

from codeanalyzer.core import Codeanalyzer, stream_analysis_json
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import strip_internal_only
from codeanalyzer.schema.py_schema import Analysis, PyArtifact


@pytest.fixture
def analysis(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text(
        "def f(x):\n    return g(x)\n\ndef g(x):\n    return x + 1\n"
    )
    with Codeanalyzer(AnalysisOptions(
        input=project, cache_dir=tmp_path / "cache", analysis_level=4,
        no_venv=True,
    )) as analyzer:
        return analyzer.analyze()


@pytest.mark.parametrize("strip_internal", [False, True])
def test_same_content_as_whole_tree_dump(analysis, strip_internal):
    analysis.application.artifacts = {
        path: PyArtifact(path=path, format="text", source=source)
        for path, source in [
            ('配置-🧪/quote".txt', 'café 🙂\n\\ "call_sites"\t'),
            ("empty.txt", ""),
            ("plain.txt", "ASCII\n"),
        ]
    }
    if strip_internal:
        expected = strip_internal_only(analysis.model_dump(mode="json", exclude_none=True))
    else:
        expected = json.loads(analysis.model_dump_json(exclude_none=True))
    out = io.StringIO()
    stream_analysis_json(analysis, out, strip_internal=strip_internal)
    actual = json.loads(out.getvalue())
    assert actual == expected
    assert list(actual["application"]["artifacts"]) == list(analysis.application.artifacts)


def test_artifacts_are_not_materialized_in_envelope(analysis, monkeypatch):
    analysis.application.artifacts = {
        str(i): PyArtifact(path=str(i), format="text", source="🙂" * 4096)
        for i in range(20)
    }
    original = Analysis.model_dump_json

    def dump(self, *args, **kwargs):
        assert self.application.artifacts == {}
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Analysis, "model_dump_json", dump)
    stream_analysis_json(analysis, io.StringIO(), strip_internal=False)


def test_artifacts_restored_when_envelope_serialization_fails(analysis, monkeypatch):
    artifacts = {"a": PyArtifact(path="a", format="text", source="🙂")}
    analysis.application.artifacts = artifacts
    modules = analysis.application.symbol_table

    def fail(self, *args, **kwargs):
        raise ValueError("serialization failed")

    monkeypatch.setattr(Analysis, "model_dump_json", fail)
    with pytest.raises(ValueError, match="serialization failed"):
        stream_analysis_json(analysis, io.StringIO(), strip_internal=False)
    assert analysis.application.artifacts is artifacts
    assert analysis.application.symbol_table is modules
