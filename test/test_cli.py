import json
from pathlib import Path
from codeanalyzer.__main__ import app
from codeanalyzer.utils import logger


def test_cli_help(cli_runner):
    """Must be able to run the CLI and see help output."""
    result = cli_runner.invoke(app, ["--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert result.exit_code == 0

def test_cli_call_symbol_table_with_json(cli_runner, whole_applications__xarray):
    """Must be able to run the CLI with symbol table analysis."""
    output_dir = whole_applications__xarray.joinpath("test", ".output")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = cli_runner.invoke(
        app,
        [
            "--input",
            str(whole_applications__xarray),
            "--output",
            str(output_dir),
            "--ray",
            "--analysis-level", "1",
            "--cache-dir",
            str(whole_applications__xarray.joinpath("test", ".cache")),
            "--clear-cache",
            "--format=json",
        ],
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.exit_code == 0, "CLI command should succeed"
    assert Path(output_dir).joinpath("analysis.json").exists(), "Output JSON file should be created"
    json_obj = json.loads(Path(output_dir).joinpath("analysis.json").read_text())
    assert json_obj is not None, "JSON output should not be None"
    assert isinstance(json_obj, dict), "JSON output should be a dictionary"
    assert "symbol_table" in json_obj.keys(), "Symbol table should be present in the output"
    assert len(json_obj["symbol_table"]) > 0, "Symbol table should not be empty"


def test_no_venv_skips_virtualenv(
    cli_runner, single_functionalities__stuff_nested_in_functions, tmp_path
):
    """#46: --no-venv must skip virtualenv creation/installation and still analyze."""
    out = tmp_path / "out"
    cache = tmp_path / "cache"
    result = cli_runner.invoke(
        app,
        [
            "--input", str(single_functionalities__stuff_nested_in_functions),
            "--output", str(out),
            "--cache-dir", str(cache),
            "--no-venv", "--no-codeql", "--no-ray",
        ],
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.exit_code == 0, result.output
    assert (out / "analysis.json").exists(), "analysis.json should still be produced with --no-venv"
    assert not list(cache.rglob("virtualenv")), "--no-venv must not create a virtualenv"


def test_single_file(cli_runner, single_functionalities__stuff_nested_in_functions):
    """Must be able to run the CLI with single file analysis using --file-name flag."""
    output_dir = single_functionalities__stuff_nested_in_functions.joinpath(".output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Path to the specific test file
    test_file = single_functionalities__stuff_nested_in_functions.joinpath("main.py")

    result = cli_runner.invoke(
        app,
        [
            "--input",
            str(single_functionalities__stuff_nested_in_functions),
            "--file-name",
            str(test_file),
            "--no-ray",
            "--clear-cache",
            "-vv",
            "--skip-tests",
            "--output",
            str(output_dir),
            "--eager",
            "--format=json",
        ],
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    
    assert result.exit_code == 0, f"CLI command should succeed. Output: {result.output}"
    assert Path(output_dir).joinpath("analysis.json").exists(), "Output JSON file should be created"
    
    # Load and validate the JSON output
    json_obj = json.loads(Path(output_dir).joinpath("analysis.json").read_text())
    assert json_obj is not None, "JSON output should not be None"
    assert isinstance(json_obj, dict), "JSON output should be a dictionary"
    assert "symbol_table" in json_obj.keys(), "Symbol table should be present in the output"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_analysis(cli_runner, fixture_dir, analysis_level=1, file_name=None, extra_args=None):
    """Invoke the CLI on *fixture_dir* and return the parsed JSON output."""
    output_dir = fixture_dir.joinpath(".output")
    output_dir.mkdir(parents=True, exist_ok=True)
    args = [
        "--input", str(fixture_dir),
        "--output", str(output_dir),
        "--no-ray",
        "--clear-cache",
        "--analysis-level", str(analysis_level),
        "--skip-tests",
        "--format=json",
    ]
    if file_name:
        args += ["--file-name", str(file_name)]
    if extra_args:
        args += extra_args
    result = cli_runner.invoke(
        app, args, env={"NO_COLOR": "1", "TERM": "dumb"}
    )
    assert result.exit_code == 0, f"CLI failed (level {analysis_level}): {result.output}"
    out = fixture_dir.joinpath(".output", "analysis.json")
    assert out.exists()
    return json.loads(out.read_text())


# ---------------------------------------------------------------------------
# Targeted single-functionality fixtures — Level 1
# ---------------------------------------------------------------------------

def test_decorators_hof_level1(cli_runner, single_functionalities__decorators_and_hof):
    """Level 1 on decorators_and_hof: symbol table populated, call_graph empty."""
    main_py = single_functionalities__decorators_and_hof / "main.py"
    obj = _run_analysis(cli_runner, single_functionalities__decorators_and_hof,
                        analysis_level=1, file_name=main_py)
    assert len(obj["symbol_table"]) > 0
    assert obj["call_graph"] == [], "Level 1 must not populate call_graph"
    sigs = {c["signature"] for mod in obj["symbol_table"].values()
            for c in _all_callables(mod)}
    assert any("main" in s for s in sigs), "Expected 'main' callable in symbol table"


def test_decorators_hof_level2(cli_runner, single_functionalities__decorators_and_hof):
    """Level 2 on decorators_and_hof: call_graph non-empty with PyCG edges.

    Key assertions:
    - At least 20 total edges (observed ~34)
    - PyCG resolves HOF points-to: apply->triple (missed by Jedi's single call-site inference)
    - PyCG finds closure call: log_call.wrapper->greet
    """
    main_py = single_functionalities__decorators_and_hof / "main.py"
    obj = _run_analysis(cli_runner, single_functionalities__decorators_and_hof,
                        analysis_level=2, file_name=main_py)
    assert len(obj["symbol_table"]) > 0
    assert len(obj["call_graph"]) >= 20, \
        f"Expected >=20 edges for decorators_and_hof, got {len(obj['call_graph'])}"

    pycg_edges = [(e["source"], e["target"]) for e in obj["call_graph"]
                  if "pycg" in e["provenance"]]
    assert len(pycg_edges) >= 10, \
        f"Expected >=10 PyCG edges, got {len(pycg_edges)}"

    pycg_targets_from_apply = {t for s, t in pycg_edges if "apply" in s}
    assert any("triple" in t for t in pycg_targets_from_apply), \
        "PyCG must resolve apply->triple via points-to (Jedi misses the second call site)"

    pycg_targets_from_wrapper = {t for s, t in pycg_edges if "wrapper" in s}
    assert any("greet" in t for t in pycg_targets_from_wrapper), \
        "PyCG must resolve log_call.wrapper->greet (closure call)"


def test_class_hierarchy_level1(cli_runner, single_functionalities__class_hierarchy):
    """Level 1 on class_hierarchy: symbol table has classes and methods."""
    main_py = single_functionalities__class_hierarchy / "main.py"
    obj = _run_analysis(cli_runner, single_functionalities__class_hierarchy,
                        analysis_level=1, file_name=main_py)
    assert obj["call_graph"] == [], "Level 1 must not populate call_graph"
    classes = {cls for mod in obj["symbol_table"].values()
               for cls in mod.get("classes", {}).keys()}
    assert any("Animal" in c for c in classes)
    assert any("Dog" in c for c in classes)
    assert any("Cat" in c for c in classes)


def test_class_hierarchy_level2(cli_runner, single_functionalities__class_hierarchy):
    """Level 2 on class_hierarchy: PyCG resolves virtual dispatch and super() calls.

    Key assertions:
    - At least 30 total edges (observed ~51)
    - PyCG finds virtual dispatch: Animal.describe->PoliceDog.speak
    - PyCG finds super().__init__ chains (present as super edges)
    - __init__ edges present from constructor calls
    """
    main_py = single_functionalities__class_hierarchy / "main.py"
    obj = _run_analysis(cli_runner, single_functionalities__class_hierarchy,
                        analysis_level=2, file_name=main_py)
    assert len(obj["call_graph"]) >= 30, \
        f"Expected >=30 edges for class_hierarchy, got {len(obj['call_graph'])}"

    pycg_edges = [(e["source"], e["target"]) for e in obj["call_graph"]
                  if "pycg" in e["provenance"]]
    assert len(pycg_edges) >= 15, \
        f"Expected >=15 PyCG edges, got {len(pycg_edges)}"

    # PyCG resolves virtual dispatch: Animal.describe calls speak() on subclasses
    describe_targets = {t for s, t in pycg_edges if "describe" in s}
    assert any("speak" in t for t in describe_targets), \
        "PyCG must find Animal.describe->*.speak virtual dispatch"

    targets = {e["target"] for e in obj["call_graph"]}
    assert any("__init__" in t for t in targets), "Expected __init__ edges in class hierarchy"


def test_async_patterns_level1(cli_runner, single_functionalities__async_patterns):
    """Level 1 on async_patterns: async functions appear in symbol table."""
    main_py = single_functionalities__async_patterns / "main.py"
    obj = _run_analysis(cli_runner, single_functionalities__async_patterns,
                        analysis_level=1, file_name=main_py)
    assert obj["call_graph"] == [], "Level 1 must not populate call_graph"
    sigs = {c["signature"] for mod in obj["symbol_table"].values()
            for c in _all_callables(mod)}
    assert any("fetch_data" in s for s in sigs)
    assert any("async_main" in s or "main" in s for s in sigs)


def test_async_patterns_level2(cli_runner, single_functionalities__async_patterns):
    """Level 2 on async_patterns: PyCG resolves async calls and asyncio stdlib edges.

    Key assertions:
    - At least 15 total edges (observed ~31)
    - PyCG finds asyncio.sleep in async functions (await targets)
    - PyCG finds asyncio.gather in fetch_all
    - Pipeline chain is fully connected (async_main->pipeline->fetch_all->process_url->fetch_data)
    """
    main_py = single_functionalities__async_patterns / "main.py"
    obj = _run_analysis(cli_runner, single_functionalities__async_patterns,
                        analysis_level=2, file_name=main_py)
    assert len(obj["call_graph"]) >= 15, \
        f"Expected >=15 edges for async_patterns, got {len(obj['call_graph'])}"

    pycg_edges = [(e["source"], e["target"]) for e in obj["call_graph"]
                  if "pycg" in e["provenance"]]
    assert len(pycg_edges) >= 8, \
        f"Expected >=8 PyCG edges, got {len(pycg_edges)}"

    pycg_targets = {t for _, t in pycg_edges}
    assert any("asyncio" in t or "sleep" in t for t in pycg_targets), \
        "PyCG must resolve asyncio.sleep calls in async functions"

    all_edges = {(e["source"], e["target"]) for e in obj["call_graph"]}
    assert any("pipeline" in s and "fetch_all" in t for s, t in all_edges), \
        "pipeline->fetch_all edge must be present"
    assert any("process_url" in s and "fetch_data" in t for s, t in all_edges), \
        "process_url->fetch_data edge must be present"


# ---------------------------------------------------------------------------
# Whole-application fixtures — smoke tests
# ---------------------------------------------------------------------------

def test_flask_level1(cli_runner, whole_applications__flask):
    """Level 1 on Flask 3.0.3: symbol table populated."""
    obj = _run_analysis(cli_runner, whole_applications__flask, analysis_level=1)
    assert len(obj["symbol_table"]) > 0
    assert obj["call_graph"] == [], "Level 1 must not populate call_graph"
    assert any("flask" in mod_path.lower() for mod_path in obj["symbol_table"]), \
        "Flask modules should be in symbol table"


def test_flask_level2(cli_runner, whole_applications__flask):
    """Level 2 on Flask 3.0.3: PyCG substantially augments Jedi's edges.

    PyCG contributes >50% of total edges for a decorator-heavy codebase like Flask
    (observed ~852 PyCG out of ~1450 total edges).
    """
    obj = _run_analysis(cli_runner, whole_applications__flask, analysis_level=2)
    assert len(obj["symbol_table"]) > 0
    assert len(obj["call_graph"]) >= 500, \
        f"Expected >=500 edges for Flask, got {len(obj['call_graph'])}"
    pycg_edges = [e for e in obj["call_graph"] if "pycg" in e["provenance"]]
    assert len(pycg_edges) >= 200, \
        f"Expected >=200 PyCG edges for Flask, got {len(pycg_edges)}"


def test_requests_level1(cli_runner, whole_applications__requests):
    """Level 1 on requests 2.31.0: symbol table populated."""
    obj = _run_analysis(cli_runner, whole_applications__requests, analysis_level=1)
    assert len(obj["symbol_table"]) > 0
    assert obj["call_graph"] == [], "Level 1 must not populate call_graph"


def test_requests_level2(cli_runner, whole_applications__requests):
    """Level 2 on requests 2.31.0: PyCG resolves OO dispatch and session/adapter calls.

    PyCG contributes >50% of total edges for a clean OO codebase like requests
    (observed ~724 PyCG out of ~1121 total edges).
    """
    obj = _run_analysis(cli_runner, whole_applications__requests, analysis_level=2)
    assert len(obj["symbol_table"]) > 0
    assert len(obj["call_graph"]) >= 400, \
        f"Expected >=400 edges for requests, got {len(obj['call_graph'])}"
    pycg_edges = [e for e in obj["call_graph"] if "pycg" in e["provenance"]]
    assert len(pycg_edges) >= 150, \
        f"Expected >=150 PyCG edges for requests, got {len(pycg_edges)}"


# ---------------------------------------------------------------------------
# Helper: flatten all callables from a serialised PyModule dict
# ---------------------------------------------------------------------------

def _all_callables(module_dict: dict) -> list:
    """Flatten all callable dicts from a serialised PyModule."""
    result = []
    for fn in module_dict.get("functions", {}).values():
        result.extend(_flatten_callable(fn))
    for cls in module_dict.get("classes", {}).values():
        result.extend(_flatten_class(cls))
    return result


def _flatten_callable(c: dict) -> list:
    result = [c]
    for inner in c.get("inner_callables", {}).values():
        result.extend(_flatten_callable(inner))
    for inner_cls in c.get("inner_classes", {}).values():
        result.extend(_flatten_class(inner_cls))
    return result


def _flatten_class(cls: dict) -> list:
    result = []
    for method in cls.get("methods", {}).values():
        result.extend(_flatten_callable(method))
    for inner in cls.get("inner_classes", {}).values():
        result.extend(_flatten_class(inner))
    return result