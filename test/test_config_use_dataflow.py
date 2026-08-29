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
import json

from codeanalyzer.artifacts.config_use import (
    Rule, _Read, dataflow_intra_tier, dataflow_interproc_tier, resolve_uses,
)
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json
from codeanalyzer.schema.py_schema import (
    BodyNode, DdgEdge, PyApplication, PyArtifact, PyCallArgument, PyCallable,
    PyCallableParameter, PyCallEdge, PyConfigKey, PyExternalSymbol, PyModule, Span,
)


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


# --- hand-built helpers (review carried items) ------------------------------
# The tier functions (`dataflow_intra_tier`/`dataflow_interproc_tier`) are
# pure functions of `(List[_Read], PyApplication)` -- exercised directly
# here, bypassing `detect_config_reads`/the Codeanalyzer pipeline entirely,
# same motivation as `test_config_use_literal.py`'s `_hand_app`. Each
# callable lives in its own tiny module so span byte-offsets never need to
# account for another callable's text sharing the same `source` string.

_EXT_ID = "can://python/app/@external/os/getenv"
_ENV_RULE = Rule(id="os.getenv", module="os", callable="getenv", key_arg=0, namespaces=("env",))


def _span(source: str, text: str, occurrence: int = 0) -> Span:
    """Byte span of `text`'s `occurrence`-th (0-based) match in `source` --
    only `.bytes` is read by the tiers under test, so `start`/`end` (line,
    col) are unused placeholders."""
    lo = -1
    for _ in range(occurrence + 1):
        lo = source.index(text, lo + 1)
    hi = lo + len(text)
    return Span(start=(1, lo), end=(1, hi), bytes=(lo, hi))


def _const(value) -> PyCallArgument:
    return PyCallArgument(ast_kind="Constant", value=json.dumps(value))


def _name_arg(identifier: str) -> PyCallArgument:
    return PyCallArgument(ast_kind="Name", name=identifier)


def _module(file_path: str, source: str, callable_: PyCallable) -> PyModule:
    return PyModule(file_path=file_path, module_name=file_path[:-3], source=source,
                     functions={callable_.name: callable_})


def _hand_app(modules: dict, call_graph=(), artifacts=None) -> PyApplication:
    return PyApplication(
        symbol_table=modules,
        external_symbols={_EXT_ID: PyExternalSymbol(id=_EXT_ID, name="getenv", module="os")},
        call_graph=list(call_graph), artifacts=artifacts or {},
    )


def _env_artifact(*keys: str) -> dict:
    art_id = "can://artifact/app/.env"
    return {".env": PyArtifact(
        id=art_id, path=".env", format="env",
        config_keys=[PyConfigKey(id=f"{art_id}@key/{k}", key=k, namespace="env") for k in keys],
    )}


def _resolve(read, app, *tiers):
    return resolve_uses([read], app, tier_fns=list(tiers))


def _target_with_param() -> PyCallable:
    """`def f(key): os.getenv(key)` -- the shared interproc target: `key`
    is a parameter with no local reassignment, so the intra tier always
    fails to close it (no reaching def reaches the call at all) and the
    read falls through to the interproc tier, same as the real pipeline."""
    return PyCallable(
        name="f", path="f.py", signature="f.f", id="f_id",
        parameters=[PyCallableParameter(name="key")],
        body={"read": BodyNode(kind="call", callee=_EXT_ID, arguments=[_name_arg("key")])},
    )


def _target_read() -> _Read:
    return _Read(site="f_id@read", callable_id="f_id", local_id="read", callee=_EXT_ID,
                 rule=_ENV_RULE, key_literal=None, key_name="key")


def _caller(file_path: str, callable_id: str, argument) -> PyModule:
    """A no-frills caller: one function, one call to `f_id` at position 0."""
    c = PyCallable(
        name="caller", path=file_path, signature=f"{file_path}.caller", id=callable_id,
        body={"call": BodyNode(kind="call", callee="f_id", arguments=[argument])},
    )
    return _module(file_path, "", c)


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


# --- interproc multi-site agreement (review MEDIUM) -------------------------

def test_interproc_two_sites_agreeing_on_same_literal_resolves():
    """Two independent callers both passing the identical literal -- "all
    sites agree" holds trivially when there's exactly one distinct value."""
    app = _hand_app(
        {
            "f.py": _module("f.py", "", _target_with_param()),
            "a.py": _caller("a.py", "a_id", _const("DB_HOST")),
            "b.py": _caller("b.py", "b_id", _const("DB_HOST")),
        },
        call_graph=[PyCallEdge(src="a_id", dst="f_id"), PyCallEdge(src="b_id", dst="f_id")],
        artifacts=_env_artifact("DB_HOST"),
    )
    edges, unresolved = _resolve(_target_read(), app, dataflow_intra_tier, dataflow_interproc_tier)
    assert unresolved == []
    (edge,) = edges
    assert edge.prov == ["dataflow"]
    assert edge.dst == app.artifacts[".env"].config_keys[0].id


def test_interproc_direct_literal_plus_caller_side_closed_variable_resolves():
    """One site passes the literal directly; the other passes a bare Name
    that itself closes to the same literal one hop up, in ITS OWN caller
    (the `_site_literal` Name branch) -- both still agree."""
    src_b = 'V = "DB_HOST"\nf(V)\n'
    caller_b = PyCallable(
        name="caller", path="b.py", signature="b.caller", id="b_id",
        body={
            "def_v": BodyNode(kind="statement", span=_span(src_b, 'V = "DB_HOST"')),
            "call": BodyNode(kind="call", callee="f_id", span=_span(src_b, "f(V)"),
                              arguments=[_name_arg("V")]),
        },
        ddg=[DdgEdge(src="def_v", dst="call", var="V", prov=["ssa"])],
    )
    app = _hand_app(
        {
            "f.py": _module("f.py", "", _target_with_param()),
            "a.py": _caller("a.py", "a_id", _const("DB_HOST")),
            "b.py": _module("b.py", src_b, caller_b),
        },
        call_graph=[PyCallEdge(src="a_id", dst="f_id"), PyCallEdge(src="b_id", dst="f_id")],
        artifacts=_env_artifact("DB_HOST"),
    )
    edges, unresolved = _resolve(_target_read(), app, dataflow_intra_tier, dataflow_interproc_tier)
    assert unresolved == []
    (edge,) = edges
    assert edge.prov == ["dataflow"]


def test_interproc_literal_plus_non_closing_variable_stays_unresolved():
    """One site's literal is not enough -- a site whose Name argument has
    no reaching def at all (nothing for the one-hop closure to find) means
    "all sites agree" can never be confirmed, so the read stays unresolved
    even though the OTHER site is a clean literal."""
    src_b = "f(V)\n"
    caller_b = PyCallable(
        name="caller", path="b.py", signature="b.caller", id="b_id",
        body={"call": BodyNode(kind="call", callee="f_id", span=_span(src_b, "f(V)"),
                                arguments=[_name_arg("V")])},
        # no ddg edge for "V" -- nothing reaches, so the caller-side hop can't close it
    )
    app = _hand_app(
        {
            "f.py": _module("f.py", "", _target_with_param()),
            "a.py": _caller("a.py", "a_id", _const("DB_HOST")),
            "b.py": _module("b.py", src_b, caller_b),
        },
        call_graph=[PyCallEdge(src="a_id", dst="f_id"), PyCallEdge(src="b_id", dst="f_id")],
        artifacts=_env_artifact("DB_HOST"),
    )
    edges, unresolved = _resolve(_target_read(), app, dataflow_intra_tier, dataflow_interproc_tier)
    assert edges == []
    (read,) = unresolved
    assert read.reason == "non-literal"


# --- def-shape rejections (review LOW) --------------------------------------
# `_assign_literal` accepts only a plain single-target `Name = <str
# Constant>` Assign. Each shape below is a real Python statement that is
# NOT that shape, pinned so a future loosening of the check is deliberate.

def _assert_never_closes(read, app):
    """`reason == "non-literal"` at both `-a 3` (intra only) and `-a 4`
    (intra + interproc) -- these reads never carry a `key_name` that is
    also a parameter, so interproc never gets a chance to rescue them
    either; asserting both levels pins that the def-shape rejection is not
    an accident of which tier happened to run."""
    for tiers in ((dataflow_intra_tier,), (dataflow_intra_tier, dataflow_interproc_tier)):
        edges, unresolved = _resolve(read, app, *tiers)
        assert edges == []
        (r,) = unresolved
        assert r.reason == "non-literal"


def _single_def_app(source: str, def_text: str, call_text: str) -> PyApplication:
    c = PyCallable(
        name="f", path="f.py", signature="f.f", id="f_id",
        body={
            "def": BodyNode(kind="statement", span=_span(source, def_text)),
            "call": BodyNode(kind="call", callee=_EXT_ID, span=_span(source, call_text),
                              arguments=[_name_arg("KEY")]),
        },
        ddg=[DdgEdge(src="def", dst="call", var="KEY", prov=["ssa"])],
    )
    return _hand_app({"f.py": _module("f.py", source, c)}, artifacts=_env_artifact("DB_HOST"))


def _single_def_read() -> _Read:
    return _Read(site="f_id@call", callable_id="f_id", local_id="call", callee=_EXT_ID,
                 rule=_ENV_RULE, key_literal=None, key_name="KEY")


def test_annassign_def_shape_is_rejected():
    source = 'KEY: str = "DB_HOST"\nos.getenv(KEY)\n'
    app = _single_def_app(source, 'KEY: str = "DB_HOST"', "os.getenv(KEY)")
    _assert_never_closes(_single_def_read(), app)


def test_augassign_def_shape_is_rejected():
    source = 'KEY += "DB_HOST"\nos.getenv(KEY)\n'
    app = _single_def_app(source, 'KEY += "DB_HOST"', "os.getenv(KEY)")
    _assert_never_closes(_single_def_read(), app)


def test_tuple_unpack_def_shape_is_rejected():
    source = 'KEY, other = "DB_HOST", 1\nos.getenv(KEY)\n'
    app = _single_def_app(source, 'KEY, other = "DB_HOST", 1', "os.getenv(KEY)")
    _assert_never_closes(_single_def_read(), app)


# --- agree-rule halves (review LOW) -----------------------------------------

def test_two_identical_literal_defs_close():
    """Both branches assign the SAME literal -- two DDG-reaching defs, one
    distinct value, closes (spec caveat: identical duplicates count as
    closed, pinned independently of the differing-defs case)."""
    source = (
        'if flag:\n'
        '    KEY = "DB_HOST"\n'
        'else:\n'
        '    KEY = "DB_HOST"\n'
        'os.getenv(KEY)\n'
    )
    c = PyCallable(
        name="f", path="f.py", signature="f.f", id="f_id",
        body={
            "def_a": BodyNode(kind="statement", span=_span(source, 'KEY = "DB_HOST"', occurrence=0)),
            "def_b": BodyNode(kind="statement", span=_span(source, 'KEY = "DB_HOST"', occurrence=1)),
            "call": BodyNode(kind="call", callee=_EXT_ID, span=_span(source, "os.getenv(KEY)"),
                              arguments=[_name_arg("KEY")]),
        },
        ddg=[
            DdgEdge(src="def_a", dst="call", var="KEY", prov=["ssa"]),
            DdgEdge(src="def_b", dst="call", var="KEY", prov=["ssa"]),
        ],
    )
    app = _hand_app({"f.py": _module("f.py", source, c)}, artifacts=_env_artifact("DB_HOST"))
    read = _Read(site="f_id@call", callable_id="f_id", local_id="call", callee=_EXT_ID,
                 rule=_ENV_RULE, key_literal=None, key_name="KEY")
    edges, unresolved = _resolve(read, app, dataflow_intra_tier)
    assert unresolved == []
    (edge,) = edges
    assert edge.prov == ["dataflow"]


def test_literal_def_plus_non_literal_def_stays_unresolved():
    """One reaching def closes on a literal; the other is a call (not a
    Constant) -- any single non-closing reaching def kills resolution even
    though a DIFFERENT def would have closed on its own."""
    source = 'KEY = "DB_HOST"\nKEY = compute()\nos.getenv(KEY)\n'
    lit_text = 'KEY = "DB_HOST"'
    call_def_text = "KEY = compute()"
    c = PyCallable(
        name="f", path="f.py", signature="f.f", id="f_id",
        body={
            "def_lit": BodyNode(kind="statement", span=_span(source, lit_text)),
            "def_call": BodyNode(kind="statement", span=_span(source, call_def_text)),
            "call": BodyNode(kind="call", callee=_EXT_ID, span=_span(source, "os.getenv(KEY)"),
                              arguments=[_name_arg("KEY")]),
        },
        ddg=[
            DdgEdge(src="def_lit", dst="call", var="KEY", prov=["ssa"]),
            DdgEdge(src="def_call", dst="call", var="KEY", prov=["ssa"]),
        ],
    )
    app = _hand_app({"f.py": _module("f.py", source, c)}, artifacts=_env_artifact("DB_HOST"))
    read = _Read(site="f_id@call", callable_id="f_id", local_id="call", callee=_EXT_ID,
                 rule=_ENV_RULE, key_literal=None, key_name="KEY")
    edges, unresolved = _resolve(read, app, dataflow_intra_tier)
    assert edges == []
    (r,) = unresolved
    assert r.reason == "non-literal"


# --- self-recursion guard (review LOW) --------------------------------------

def test_self_recursive_call_site_terminates_without_misattribution():
    """`def f(key): os.getenv(key)` plus a self-call `f("X")` -- the ONLY
    recorded caller of `f` is `f` itself. The self-call's argument is a
    direct literal, so it resolves through `_site_literal`'s value branch
    without ever consulting `visited` (that guard only gates the Name/
    intra-closure branch) -- this terminates cleanly (no recursive
    re-entry into interproc resolution) and traces to exactly "X", never
    silently dropping the read or misattributing it to an unrelated
    declared key."""
    f = PyCallable(
        name="f", path="f.py", signature="f.f", id="f_id",
        parameters=[PyCallableParameter(name="key")],
        body={
            "read": BodyNode(kind="call", callee=_EXT_ID, arguments=[_name_arg("key")]),
            "self_call": BodyNode(kind="call", callee="f_id", arguments=[_const("X")]),
        },
    )
    app = _hand_app(
        {"f.py": _module("f.py", "", f)},
        call_graph=[PyCallEdge(src="f_id", dst="f_id")],
        artifacts=_env_artifact("DB_HOST"),
    )
    read = _Read(site="f_id@read", callable_id="f_id", local_id="read", callee=_EXT_ID,
                 rule=_ENV_RULE, key_literal=None, key_name="key")
    edges, unresolved = _resolve(read, app, dataflow_intra_tier, dataflow_interproc_tier)
    assert edges == []
    (r,) = unresolved
    assert r.reason == "undefined-key"
    assert r.key == "X"
