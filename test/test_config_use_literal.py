"""Task 2 (#162): config_use_rules.yml detector table + the literal tier.

`os.getenv`/`os.environ.get` reads against `.env`-defined keys resolve to
`PY_USES_CONFIG` edges (`prov=["literal"]`); a key that decodes to a string
but matches no declared `PyConfigKey` becomes a first-class
`PyConfigRead(reason="undefined-key")`; a key that isn't a string literal at
all becomes `reason="non-literal"`. See `config_use.py`'s module docstring
for the `os.environ["X"]` subscript finding the first test below confirms.
"""
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json
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


# --- namespace preference ------------------------------------------------

def test_namespace_preference_ini_before_properties(tmp_path):
    app = _app(
        tmp_path, "namespace",
        "import configparser\n\n"
        "def read_option():\n"
        "    cp = configparser.ConfigParser()\n"
        "    return cp.get('db', 'host')\n",
        {
            "app.ini": "[db]\nhost = ini-value\n",
            "app.properties": "host=props-value\n",
        },
    )
    ini_key = app.artifacts["app.ini"].config_keys[0]
    props_key = app.artifacts["app.properties"].config_keys[0]
    assert ini_key.key == "db.host" and ini_key.namespace == "ini"
    assert props_key.key == "host" and props_key.namespace == "properties"

    assert app.config_reads_unresolved == []
    (edge,) = app.config_uses
    assert edge.dst == ini_key.id
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
