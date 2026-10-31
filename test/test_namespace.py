import json
from pathlib import Path
import pytest
from codeanalyzer.schema import ids
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.__main__ import app as cli

GOLDEN = json.loads((Path(__file__).parent / "fixtures/identity/namespaces.json").read_text())

@pytest.mark.parametrize("case", GOLDEN["roots"])
def test_golden_roots(case):
    app, namespace, root = case["application_name"], case["namespace"], case["expected_root"]
    assert ids.application_id(app, namespace) == root
    assert ids.module_id(app, "src/main.py", namespace) == root + "/python/src/main.py"
    artifact = ids.artifact_id(app, "config/settings.json", namespace)
    assert artifact == root + "/artifact/config/settings.json"
    assert ids.config_key_id(artifact, "service.port") == artifact + "@key/service.port"
    assert ids.external_id(root, "os", "getcwd") == root + "/@external/os/getcwd"
    assert ids.purl_pypi("requests") == "pkg:pypi/requests"

@pytest.mark.parametrize("case", GOLDEN["invalid"])
def test_invalid_roots(case):
    for app, namespace in [(case["value"], None), ("app", case["value"])]:
        with pytest.raises(ValueError):
            ids.application_id(app, namespace)

@pytest.mark.parametrize("level", [2, 4])
def test_namespace_analysis_and_cache(tmp_path, level):
    project = tmp_path / "inventory"
    project.mkdir()
    (project / "main.py").write_text("import os\nfrom helper import identity\ndef run(value):\n    return identity(os.getcwd())\n")
    (project / "helper.py").write_text("def identity(value):\n    return value\n")
    (project / "settings.json").write_text('{"port": 80}')
    def analyze(namespace):
        return Codeanalyzer(AnalysisOptions(input=project, namespace=namespace,
            no_venv=True, cache_dir=tmp_path / "cache", analysis_level=level)).analyze()
    for namespace in ["team", "other", None]:
        analysis = analyze(namespace)
        app = analysis.application
        root = f"can://{namespace or 'inventory'}/inventory"
        assert analysis.identity_version == 2
        assert app.application_name == "inventory"
        assert app.identity_namespace == (namespace or "inventory")
        assert app.id == root
        assert sorted(app.symbol_table) == ["helper.py", "main.py"]
        imports = app.symbol_table["main.py"].imports
        assert any(im.resolved_module == "helper.py" for im in imports)
        run = next(iter(app.symbol_table["main.py"].functions.values()))
        assert run.id == root + "/python/main.py/run(value)"
        assert run.parameters[0].id == run.id + "@formal_in:0"
        if level == 4:
            assert app.param_in
        assert app.symbol_table["main.py"].id == root + "/python/main.py"
        assert app.artifacts["settings.json"].id == root + "/artifact/settings.json"
        import re
        assert all(value == root or value.startswith(root + "/") for value in
                   re.findall(r'can://[^" ]+', app.model_dump_json()))

def test_namespace_cli(cli_runner, tmp_path):
    project = tmp_path / "inventory"
    project.mkdir()
    (project / "main.py").write_text("def run(): return 1\n")
    output = tmp_path / "out"
    args = ["-i", str(project), "-o", str(output), "--no-venv", "-c", str(tmp_path / "cache")]
    result = cli_runner.invoke(cli, args + ["--namespace", "team"])
    assert result.exit_code == 0, result.output
    payload = json.loads((output / "analysis.json").read_text())
    assert payload["identity_version"] == 2
    assert payload["application"]["id"] == "can://team/inventory"
    assert cli_runner.invoke(cli, args + ["--namespace", ""]).exit_code != 0
    assert cli_runner.invoke(cli, args + ["--workspace-root", str(tmp_path)]).exit_code != 0


def test_graph_emission_uses_namespace_and_rejects_changed_scope(tmp_path):
    from codeanalyzer.neo4j.emit import emit_neo4j
    from codeanalyzer.neo4j.project import project
    from codeanalyzer.schema.assign_ids import assign_ids
    project_dir = tmp_path / "inventory"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("import os\ndef run():\n    return os.getcwd()\n")
    (project_dir / "settings.json").write_text('{"port": 80}')
    options = AnalysisOptions(input=project_dir, namespace="team", no_venv=True,
                              cache_dir=tmp_path / "cache", output=tmp_path / "output")
    analysis = Codeanalyzer(options).analyze()
    mapping = assign_ids(analysis.application, "inventory", "team")
    rows = project(analysis.application, "inventory", mapping, namespace="team")
    anchor = next(n for n in rows.nodes if n.labels[0] == "PyApplication")
    assert anchor.props["identity_version"] == 2
    assert anchor.props["identity_namespace"] == "team"
    assert anchor.props["application_name"] == "inventory"
    assert all(n.value.startswith("can://team/inventory/") or n.value == "can://team/inventory"
               for n in rows.nodes if n.value.startswith("can://"))
    emit_neo4j(analysis, options)
    first = (options.output / "graph.cypher").read_text()
    assert "can://team/inventory/" in first
    assert "can://inventory/inventory" not in first
    emit_neo4j(analysis, options)
    assert (options.output / "graph.cypher").read_text() == first
    options.namespace = "other"
    with pytest.raises(ValueError, match="identity"):
        emit_neo4j(analysis, options)
    assert (options.output / "graph.cypher").read_text() == first


def test_analysis_requires_identity_version():
    from codeanalyzer.schema import Analysis, PyApplication
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Analysis(application=PyApplication(symbol_table={}))
    with pytest.raises(ValidationError):
        Analysis(identity_version=1, application=PyApplication(symbol_table={}))


def test_bolt_reconciliation_prefix_is_exact_namespace_scope():
    from codeanalyzer.neo4j.bolt import _artifact_prefixes
    from codeanalyzer.neo4j.rows import application_prefix
    assert _artifact_prefixes("inventory", "team") == ["can://team/inventory/artifact/"]
    assert application_prefix("inventory", "team") == "can://team/inventory/"


def test_raw_analysis_rejects_legacy_anchor_and_noninteger_marker():
    from codeanalyzer.schema import Analysis, PyApplication
    from pydantic import ValidationError
    payload = Analysis(identity_version=2, application=PyApplication(symbol_table={})).model_dump()
    for value in [2.0, True, "2"]:
        with pytest.raises(ValidationError):
            Analysis.model_validate({**payload, "identity_version": value})
    del payload["application"]["identity_version"]
    with pytest.raises(ValidationError):
        Analysis.model_validate(payload)
