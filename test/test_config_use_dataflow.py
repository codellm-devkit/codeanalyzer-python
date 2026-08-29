"""Task 3 (#162): the two dataflow tiers layered on config_use.py's literal
tier -- intra (`-a 3`, DDG single-literal closure over the read's own
callable) and interprocedural (`-a 4`, one level of caller-argument
closure). Spec: docs/design/specs/2026-08-28-config-use-edge-design.md
decision 2; plan: docs/design/plans/2026-08-28-config-use-edge.md Task 3.

Both tiers only ever widen what the literal tier left unresolved -- a read
resolved at a lower tier is never recomputed, and `prov` on a still-
unresolved `PyConfigRead` lists every tier attempted (`["literal"]` at `-a
2`, `["literal", "dataflow"]` at `-a 3`/`-a 4` -- the vocabulary has no
separate intra/interproc tag).
"""
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json


def _project(tmp_path, tag, mod_source, files):
    proj = tmp_path / f"proj-{tag}"
    proj.mkdir()
    (proj / "mod.py").write_text(mod_source)
    for name, content in files.items():
        (proj / name).write_text(content)
    return proj


def _app(tmp_path, tag, mod_source, files, level):
    proj = _project(tmp_path, tag, mod_source, files)
    return Codeanalyzer(AnalysisOptions(
        input=proj, analysis_level=level, no_venv=True, cache_dir=tmp_path / f"cache-{tag}",
    )).analyze().application


# --- intra tier (-a 3) ------------------------------------------------------

def test_variable_closing_to_one_literal_resolves_at_l3_not_l2(tmp_path):
    source = (
        "import os\n\n"
        "def read_host():\n"
        "    KEY = 'DB_HOST'\n"
        "    os.getenv(KEY)\n"
    )
    files = {".env": "DB_HOST=example.com\n"}

    l2 = _app(tmp_path, "l2", source, files, level=2)
    assert l2.config_uses == []
    (read,) = l2.config_reads_unresolved
    assert read.reason == "non-literal"
    assert read.prov == ["literal"]

    l3 = _app(tmp_path, "l3", source, files, level=3)
    assert l3.config_reads_unresolved == []
    (edge,) = l3.config_uses
    assert edge.prov == ["dataflow"]
    key = l3.artifacts[".env"].config_keys[0]
    assert edge.dst == key.id


def test_two_differing_defs_stay_unresolved_at_every_level(tmp_path):
    source = (
        "import os\n\n"
        "def read_host(flag):\n"
        "    if flag:\n"
        "        KEY = 'DB_HOST'\n"
        "    else:\n"
        "        KEY = 'DB_PORT'\n"
        "    os.getenv(KEY)\n"
    )
    files = {".env": "DB_HOST=example.com\nDB_PORT=5432\n"}
    for level in (3, 4):
        app = _app(tmp_path, f"differ-{level}", source, files, level=level)
        assert app.config_uses == []
        (read,) = app.config_reads_unresolved
        assert read.reason == "non-literal"
        assert read.prov == ["literal", "dataflow"]


def test_loop_carried_key_stays_unresolved(tmp_path):
    source = (
        "import os\n\n"
        "def read_many():\n"
        "    for k in ['DB_HOST', 'DB_PORT']:\n"
        "        os.getenv(k)\n"
    )
    files = {".env": "DB_HOST=example.com\nDB_PORT=5432\n"}
    app = _app(tmp_path, "loop", source, files, level=3)
    assert app.config_uses == []
    (read,) = app.config_reads_unresolved
    assert read.reason == "non-literal"


# --- interprocedural tier (-a 4) -------------------------------------------

def test_param_passed_literal_resolves_at_l4_not_l3(tmp_path):
    source = (
        "import os\n\n"
        "def read(name):\n"
        "    os.getenv(name)\n\n"
        "def caller():\n"
        "    read('DB_HOST')\n"
    )
    files = {".env": "DB_HOST=example.com\n"}

    l3 = _app(tmp_path, "l3-interproc", source, files, level=3)
    assert l3.config_uses == []
    (read,) = l3.config_reads_unresolved
    assert read.reason == "non-literal"
    assert read.prov == ["literal", "dataflow"]

    l4 = _app(tmp_path, "l4-interproc", source, files, level=4)
    assert l4.config_reads_unresolved == []
    (edge,) = l4.config_uses
    assert edge.prov == ["dataflow"]


def test_two_call_sites_with_different_literals_stay_unresolved(tmp_path):
    source = (
        "import os\n\n"
        "def read(name):\n"
        "    os.getenv(name)\n\n"
        "def caller_a():\n"
        "    read('DB_HOST')\n\n"
        "def caller_b():\n"
        "    read('DB_PORT')\n"
    )
    files = {".env": "DB_HOST=example.com\nDB_PORT=5432\n"}
    app = _app(tmp_path, "two-sites", source, files, level=4)
    assert app.config_uses == []
    (read,) = app.config_reads_unresolved
    assert read.reason == "non-literal"


def test_shadowed_parameter_is_not_misattributed_to_caller_argument(tmp_path):
    """A parameter reassigned locally before the read resolves (or not) from
    its OWN value, never from what a caller passed for that parameter name.
    Guards the interproc tier against re-deriving a value from callers once
    the intra tier already closed the read on a literal that simply names
    no declared key (undefined-key) -- the shadowing write, not the call
    site, is this read's real source."""
    source = (
        "import os\n\n"
        "def read(key):\n"
        "    key = 'NOT_A_REAL_KEY'\n"
        "    os.getenv(key)\n\n"
        "def caller():\n"
        "    read('DB_HOST')\n"
    )
    files = {".env": "DB_HOST=example.com\n"}
    app = _app(tmp_path, "shadow", source, files, level=4)
    assert app.config_uses == []
    (read,) = app.config_reads_unresolved
    assert read.reason == "undefined-key"
    assert read.key == "NOT_A_REAL_KEY"


# --- monotonicity ------------------------------------------------------------

def test_config_uses_monotonic_across_levels(tmp_path):
    """`-a 2` (literal only) subset `-a 3` (+intra) subset `-a 4`
    (+interproc) -- same additive contract as the DDG's `prov` widening
    (CI-gate test, per the plan's Global Constraints)."""
    source = (
        "import os\n\n"
        "def read_literal():\n"
        "    os.getenv('DB_HOST')\n\n"
        "def read_closed():\n"
        "    KEY = 'DB_PORT'\n"
        "    os.getenv(KEY)\n\n"
        "def read_param(name):\n"
        "    os.getenv(name)\n\n"
        "def caller():\n"
        "    read_param('DB_USER')\n"
    )
    files = {".env": "DB_HOST=example.com\nDB_PORT=5432\nDB_USER=admin\n"}
    # One project dir shared across levels -- app_name (and so every `can://`
    # id) derives from the input dir name, so comparing edge sets across
    # separately-named `proj-mono-N` dirs would spuriously never intersect.
    proj = _project(tmp_path, "mono", source, files)

    def edge_set(level):
        app = Codeanalyzer(AnalysisOptions(
            input=proj, analysis_level=level, no_venv=True,
            cache_dir=tmp_path / f"cache-mono-{level}",
        )).analyze().application
        return {(e.src, e.dst) for e in app.config_uses}

    l2, l3, l4 = edge_set(2), edge_set(3), edge_set(4)
    assert l2 <= l3 <= l4
    assert len(l2) == 1
    assert len(l3) == 2
    assert len(l4) == 3


# --- determinism -------------------------------------------------------------

def test_dataflow_tier_determinism_two_runs(tmp_path):
    source = (
        "import os\n\n"
        "def read_host():\n"
        "    KEY = 'DB_HOST'\n"
        "    os.getenv(KEY)\n"
    )
    proj = _project(tmp_path, "determinism", source, {".env": "DB_HOST=example.com\n"})

    def run(tag):
        return Codeanalyzer(AnalysisOptions(
            input=proj, analysis_level=4, no_venv=True, cache_dir=tmp_path / f"cache-{tag}",
        )).analyze().application

    a = model_dump_json(run("r1"))
    b = model_dump_json(run("r2"))
    assert a == b
