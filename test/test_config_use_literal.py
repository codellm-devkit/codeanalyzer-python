"""Task 2 (#162): config_use_rules.yml detector table + the literal tier.

`os.getenv`/`os.environ.get` reads against `.env`-defined keys resolve to
`PY_USES_CONFIG` edges (`prov=["literal"]`); a key that decodes to a string
but matches no declared `PyConfigKey` becomes a first-class
`PyConfigRead(reason="undefined-key")`; a key that isn't a string literal at
all becomes `reason="non-literal"`. See `config_use.py`'s module docstring
for the `os.environ["X"]` subscript finding the first test below confirms.

The per-rule and rules-loader sections below hand-build a `PyApplication`
directly (same idiom as `test_v2_l2.py`'s `_app_with_one_call`) rather than
running the full Codeanalyzer pipeline -- detector-rule matching and
literal-tier resolution don't need Jedi/PyCG/ray, and a full pipeline run
per rule is both slower and a load-coupled flake class of its own (PyCG
shard timeouts under load produce spurious L2 failures).
"""
import json

import pytest

from codeanalyzer.artifacts.config_use import ConfigUseRulesError, detect_config_reads, load_rules, resolve_uses
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json
from codeanalyzer.schema.py_schema import (
    BodyNode, PyApplication, PyArtifact, PyCallArgument, PyCallable, PyConfigKey,
    PyExternalSymbol, PyModule,
)
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder


def _project(tmp_path, tag, mod_source, files):
    proj = tmp_path / f"proj-{tag}"
    proj.mkdir()
    (proj / "mod.py").write_text(mod_source)
    for name, content in files.items():
        (proj / name).write_text(content)
    return proj


def _app(tmp_path, tag, mod_source, files, level=2):
    proj = _project(tmp_path, tag, mod_source, files)
    return Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=level, no_venv=True, cache_dir=tmp_path / f"cache-{tag}",
    )).analyze().application


def _literal_arg(value) -> PyCallArgument:
    return PyCallArgument(ast_kind="Constant", value=json.dumps(value))


def _artifact(path: str, fmt: str, key: str, namespace: str) -> dict:
    art_id = f"can://artifact/app/{path}"
    return {path: PyArtifact(
        id=art_id, path=path, format=fmt,
        config_keys=[PyConfigKey(id=f"{art_id}@key/{key}", key=key, namespace=namespace)],
    )}


def _hand_app(module: str, callable_name: str, arguments, artifacts=None) -> PyApplication:
    """A `PyApplication` with exactly one callable containing one call to
    `module.callable_name` at its detector-rule key-argument position(s) --
    the minimal substrate `detect_config_reads`/`resolve_uses` need, with no
    analyzer pipeline involved."""
    ext_id = f"can://python/app/@external/{module}/{callable_name}"
    fn = PyCallable(
        name="f", path="mod.py", signature="mod.f", id="can://python/app/mod.py/f()",
        body={"1:0": BodyNode(kind="call", callee=ext_id, arguments=list(arguments))},
    )
    mod = PyModule(file_path="mod.py", module_name="mod", functions={"f": fn})
    return PyApplication(
        symbol_table={"mod.py": mod},
        external_symbols={ext_id: PyExternalSymbol(id=ext_id, name=callable_name, module=module)},
        artifacts=artifacts or {},
    )


# --- controller ruling (b): empirical subscript probe -----------------------

def test_environ_subscript_is_not_lowered_to_a_call_node(tmp_path):
    """`os.environ["X"]` is an `ast.Subscript`, not `ast.Call` --
    `_iter_calls_in_scope` (symbol_table_builder.py) only ever yields
    `ast.Call` nodes, so this produces zero call sites. Confirms the
    recorded gap documented in config_use.py's module docstring, rather
    than assuming it."""
    (tmp_path / "mod.py").write_text(
        "import os\n\ndef read():\n    return os.environ['DB_HOST']\n"
    )
    module = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(tmp_path / "mod.py")
    assert module.functions["read"].call_sites == []


# --- literal tier -------------------------------------------------------

def test_literal_key_resolves_to_env_config_key(tmp_path):
    app = _app(
        tmp_path, "literal",
        "import os\n\ndef read_host():\n    return os.getenv('DB_HOST')\n",
        {".env": "DB_HOST=example.com\n"},
    )
    assert app.config_reads_unresolved == []
    (edge,) = app.config_uses
    assert edge.prov == ["literal"]

    key = app.artifacts[".env"].config_keys[0]
    assert key.key == "DB_HOST" and key.namespace == "env"
    assert edge.dst == key.id

    fn = app.symbol_table["mod.py"].functions["read_host"]
    assert edge.src.startswith(fn.id + "@")


def test_undefined_key_is_first_class_unresolved(tmp_path):
    app = _app(
        tmp_path, "undefined",
        "import os\n\ndef read_missing():\n    return os.getenv('MISSING')\n",
        {".env": "DB_HOST=example.com\n"},
    )
    assert app.config_uses == []
    (read,) = app.config_reads_unresolved
    assert read.reason == "undefined-key"
    assert read.key == "MISSING"
    assert read.prov == ["literal"]
    assert read.callee.endswith("/@external/os/getenv")


def test_non_literal_key_is_unresolved_at_l2(tmp_path):
    app = _app(
        tmp_path, "nonliteral",
        "import os\n\ndef read_dynamic(kvar):\n    return os.getenv(kvar)\n",
        {".env": "DB_HOST=example.com\n"},
    )
    assert app.config_uses == []
    (read,) = app.config_reads_unresolved
    assert read.reason == "non-literal"
    assert read.key is None


def test_level_one_never_populates_config_uses(tmp_path):
    """Literal tier needs a resolved callee -- L1 body data has `callee=None`
    on every call node, so detection finds nothing to match."""
    app = _app(
        tmp_path, "l1",
        "import os\n\ndef read_host():\n    return os.getenv('DB_HOST')\n",
        {".env": "DB_HOST=example.com\n"},
        level=1,
    )
    assert app.config_uses == []
    assert app.config_reads_unresolved == []


# --- per-rule regression (carried review item) ------------------------------
# The pipeline tests above exercise os.getenv and configparser.get; the
# other five shipped rules get no coverage without these. One hand-built
# `PyApplication` per rule, matched against `config_use_rules.yml` as-shipped.

def test_os_environ_get_rule_resolves_literal():
    app = _hand_app(
        "os.environ", "get", [_literal_arg("DB_HOST")],
        artifacts=_artifact(".env", "env", "DB_HOST", "env"),
    )
    edges, unresolved = resolve_uses(detect_config_reads(app), app)
    assert unresolved == []
    (edge,) = edges
    assert edge.prov == ["literal"]
    assert edge.dst == app.artifacts[".env"].config_keys[0].id


def test_dotenv_get_key_rule_resolves_literal():
    # key_arg=1: dotenv.get_key(dotenv_path, key_to_get).
    app = _hand_app(
        "dotenv", "get_key",
        [_literal_arg(".env"), _literal_arg("DB_HOST")],
        artifacts=_artifact(".env", "env", "DB_HOST", "env"),
    )
    edges, unresolved = resolve_uses(detect_config_reads(app), app)
    assert unresolved == []
    (edge,) = edges
    assert edge.prov == ["literal"]


def test_configparser_getint_rule_resolves_literal():
    # key_arg=1, kwarg="option": cp.getint(section, option).
    app = _hand_app(
        "configparser", "getint",
        [_literal_arg("db"), _literal_arg("port")],
        artifacts=_artifact("app.ini", "ini", "db.port", "ini"),
    )
    edges, unresolved = resolve_uses(detect_config_reads(app), app)
    assert unresolved == []
    (edge,) = edges
    assert edge.prov == ["literal"]


def test_configparser_getfloat_rule_resolves_literal():
    app = _hand_app(
        "configparser", "getfloat",
        [_literal_arg("db"), _literal_arg("timeout")],
        artifacts=_artifact("app.ini", "ini", "db.timeout", "ini"),
    )
    edges, unresolved = resolve_uses(detect_config_reads(app), app)
    assert unresolved == []
    (edge,) = edges
    assert edge.prov == ["literal"]


def test_configparser_getboolean_rule_resolves_literal():
    app = _hand_app(
        "configparser", "getboolean",
        [_literal_arg("db"), _literal_arg("debug")],
        artifacts=_artifact("app.ini", "ini", "db.debug", "ini"),
    )
    edges, unresolved = resolve_uses(detect_config_reads(app), app)
    assert unresolved == []
    (edge,) = edges
    assert edge.prov == ["literal"]


# --- rules loader ------------------------------------------------------------

def test_kwarg_must_be_a_string(tmp_path):
    """Carried review item: `kwarg` is stored (not yet actionable, per
    `Rule.kwarg`'s comment) but was never type-checked -- a non-string value
    would silently ride into a `Rule` instead of failing the load."""
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "version: 1\n"
        "rules:\n"
        "  - id: bad\n"
        "    module: m\n"
        "    callable: f\n"
        "    key_arg: 0\n"
        "    kwarg: 3\n"
        "    namespaces: [env]\n"
    )
    with pytest.raises(ConfigUseRulesError, match="kwarg"):
        load_rules(bad)


# --- namespace preference ------------------------------------------------

def test_namespace_preference_ini_before_properties():
    app = _hand_app(
        "configparser", "get", [_literal_arg("db"), _literal_arg("host")],
        artifacts={
            **_artifact("app.ini", "ini", "db.host", "ini"),
            **_artifact("app.properties", "properties", "host", "properties"),
        },
    )
    ini_key = app.artifacts["app.ini"].config_keys[0]
    props_key = app.artifacts["app.properties"].config_keys[0]

    edges, unresolved = resolve_uses(detect_config_reads(app), app)
    assert unresolved == []
    (edge,) = edges
    assert edge.dst == ini_key.id
    assert edge.dst != props_key.id
    assert edge.prov == ["literal"]


# --- determinism ----------------------------------------------------------

def test_determinism_two_runs(tmp_path):
    """Same project (so the same app_name/ids), independent cache dirs --
    isolates config_use's own determinism from the id-scheme churn a
    different `proj-*` directory name would otherwise introduce."""
    proj = _project(
        tmp_path, "determinism",
        "import os\n\ndef read_host():\n    return os.getenv('DB_HOST')\n",
        {".env": "DB_HOST=example.com\n"},
    )

    def run(tag):
        return Codeanalyzer(AnalysisOptions(
            input=proj, analysis_level=2, no_venv=True, cache_dir=tmp_path / f"cache-{tag}",
        )).analyze().application

    a = model_dump_json(run("r1"))
    b = model_dump_json(run("r2"))
    assert a == b
