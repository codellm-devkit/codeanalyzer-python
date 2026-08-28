"""Task 2: config-key flatteners with reference recognition."""
import textwrap

from codeanalyzer.artifacts.config_keys import extract_config_keys
from codeanalyzer.schema.ids import artifact_id, config_key_id
from codeanalyzer.schema.py_schema import PyArtifact


def _artifact(path: str, fmt: str) -> PyArtifact:
    return PyArtifact(id=artifact_id("app", path), path=path, format=fmt)


def _by_key(keys):
    return {k.key: k for k in keys}


# --- yaml: nesting, arrays, determinism -------------------------------------

def test_yaml_nested_dotted_and_numeric_array_segments():
    text = textwrap.dedent("""\
        db:
          host: localhost
          port: 5432
        services:
          - name: web
            ports:
              - 8080
              - 8081
        """)
    art = _artifact("config.yaml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["db.host"].value == "localhost"
    assert by["db.port"].value == "5432"
    assert by["services.0.name"].value == "web"
    assert by["services.0.ports.0"].value == "8080"
    assert by["services.0.ports.1"].value == "8081"
    assert all(k.namespace == "yaml" for k in keys)
    assert all(k.id == config_key_id(art.id, k.key) for k in keys)
    # L1 determinism: sorted key order within the artifact.
    assert [k.key for k in keys] == sorted(k.key for k in keys)


def test_yaml_best_effort_span_slices_the_key_line():
    text = "db:\n  host: localhost\n"
    art = _artifact("config.yaml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    host = _by_key(keys)["db.host"]
    assert host.span is not None
    assert host.span.start[0] == 2  # "  host: localhost" is line 2
    assert "localhost" in text[host.span.bytes[0]:host.span.bytes[1]]


# --- json: nesting ------------------------------------------------------

def test_json_nesting():
    text = textwrap.dedent("""\
        {
          "database": {
            "host": "localhost",
            "replicas": ["r1", "r2"]
          }
        }
        """)
    art = _artifact("config.json", "json")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["database.host"].value == "localhost"
    assert by["database.replicas.0"].value == "r1"
    assert by["database.replicas.1"].value == "r2"
    assert all(k.namespace == "json" for k in keys)


# --- toml: tables ---------------------------------------------------------

def test_toml_tables():
    text = textwrap.dedent("""\
        [server]
        host = "0.0.0.0"
        port = 8080

        [server.tls]
        enabled = true
        """)
    art = _artifact("config.toml", "toml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["server.host"].value == "0.0.0.0"
    assert by["server.port"].value == "8080"
    assert by["server.tls.enabled"].value == "true"
    assert all(k.namespace == "toml" for k in keys)


# --- env: quoting, comments, export ----------------------------------------

def test_env_quoting_comments_export():
    text = textwrap.dedent("""\
        # a comment
        export APP_NAME="myapp"
        DEBUG=true
        GREETING='hello world'
        """)
    art = _artifact(".env", "text")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["APP_NAME"].value == "myapp"
    assert by["DEBUG"].value == "true"
    assert by["GREETING"].value == "hello world"
    assert all(k.namespace == "env" for k in keys)
    # exact line: APP_NAME is on line 2, and the span slices back to it.
    app_name = by["APP_NAME"]
    assert app_name.span.start == (2, 0)
    assert "APP_NAME" in text[app_name.span.bytes[0]:app_name.span.bytes[1]]


def test_env_quoted_value_with_trailing_comment():
    art = _artifact(".env", "text")
    keys, ok = extract_config_keys(art, 'SECRET="abc123"  # rotate quarterly\n', True)
    assert ok is True
    assert _by_key(keys)["SECRET"].value == "abc123"


def test_env_unquoted_value_with_trailing_comment():
    art = _artifact(".env", "text")
    keys, ok = extract_config_keys(art, "FOO=bar # c\n", True)
    assert ok is True
    assert _by_key(keys)["FOO"].value == "bar"


def test_env_hash_inside_quotes_is_preserved():
    art = _artifact(".env", "text")
    keys, ok = extract_config_keys(art, 'URL="http://x#frag"\n', True)
    assert ok is True
    assert _by_key(keys)["URL"].value == "http://x#frag"


def test_env_family_basename_dispatch_regardless_of_format():
    for path in (".env", ".env.local", ".flaskenv"):
        art = _artifact(path, "text")
        keys, ok = extract_config_keys(art, "FOO=bar\n", True)
        assert ok is True
        assert keys[0].namespace == "env", path
    # a near-miss basename must NOT be treated as env-family.
    art = _artifact(".environment", "text")
    keys, ok = extract_config_keys(art, "FOO=bar\n", True)
    assert ok is True and keys == []


# --- properties: continuations, comments -----------------------------------

def test_properties_continuations_and_comments():
    text = textwrap.dedent("""\
        ! a bang comment
        # a hash comment
        app.name=MyApp
        message=Welcome to \\
                Wonderland
        """)
    art = _artifact("app.properties", "properties")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["app.name"].value == "MyApp"
    assert by["message"].value == "Welcome to Wonderland"
    assert all(k.namespace == "properties" for k in keys)


def test_properties_key_colon_form():
    art = _artifact("app.properties", "properties")
    keys, ok = extract_config_keys(art, "greeting: hi\n", True)
    assert ok is True
    assert _by_key(keys)["greeting"].value == "hi"


# --- ini: raw interpolation preserved + reference recognized ---------------

def test_ini_interpolation_preserved_raw_and_reference_recognized():
    text = textwrap.dedent("""\
        [paths]
        home = /usr/local
        here = %(home)s/app
        """)
    art = _artifact("tox.ini", "ini")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["paths.home"].value == "/usr/local"
    # raw=True: NOT interpolated to "/usr/local/app".
    assert by["paths.here"].value == "%(home)s/app"
    assert by["paths.here"].references == ["%(home)s"]
    assert all(k.namespace == "ini" for k in keys)
    # exact line (not best-effort): "here" is on line 3.
    assert by["paths.here"].span.start[0] == 3


def test_ini_default_only_emits_default_prefixed_keys():
    art = _artifact("tox.ini", "ini")
    keys, ok = extract_config_keys(art, "[DEFAULT]\ntimeout = 30\n", True)
    assert ok is True
    assert _by_key(keys)["DEFAULT.timeout"].value == "30"


def test_ini_default_and_section_both_emit_duplicated_key():
    # configparser inherits every DEFAULT key into each real section, so a
    # key defined only in DEFAULT deliberately shows up twice: once as
    # DEFAULT.<key>, once per inheriting section as <section>.<key>.
    text = "[DEFAULT]\ntimeout = 30\n\n[server]\nhost = 0.0.0.0\n"
    art = _artifact("tox.ini", "ini")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["DEFAULT.timeout"].value == "30"
    assert by["server.timeout"].value == "30"
    assert by["server.host"].value == "0.0.0.0"


# --- references: all three syntaxes, order of appearance, dedupe -----------

def test_references_all_three_syntaxes_order_and_dedupe():
    text = (
        'greeting: "start $FOO middle ${BAR} then ${{ tmpl.expr }} '
        'and %(baz)s end $FOO"\n'
    )
    art = _artifact("config.yaml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    refs = _by_key(keys)["greeting"].references
    assert refs == ["$FOO", "${BAR}", "${{ tmpl.expr }}", "%(baz)s"]


# --- value gating: capture_value=False ------------------------------------

def test_capture_value_false_hides_value_only():
    text = "db:\n  host: localhost\n"
    art = _artifact("config.yaml", "yaml")
    with_value, ok1 = extract_config_keys(art, text, True)
    without_value, ok2 = extract_config_keys(art, text, False)
    assert ok1 is True and ok2 is True
    assert len(with_value) == len(without_value) == 1
    a, b = with_value[0], without_value[0]
    assert a.value == "localhost"
    assert b.value is None
    # everything else identical.
    assert a.id == b.id and a.key == b.key and a.namespace == b.namespace
    assert a.span == b.span and a.references == b.references


# --- duplicate dotted key: last-wins coalescing -----------------------------

def test_literal_dotted_key_collides_with_nesting_last_wins():
    # A literal top-level key "a.b" and a nested a -> {b: ...} both flatten
    # to the same dotted path "a.b" -- indistinguishable once flattened, so
    # they coalesce into one record (last occurrence in the file wins).
    text = "a.b: 1\na:\n  b: 2\n"
    art = _artifact("config.yaml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    matches = [k for k in keys if k.key == "a.b"]
    assert len(matches) == 1
    assert matches[0].value == "2"


# --- dispatch: unsupported format -> ([], True) -----------------------------

def test_unsupported_format_returns_empty_ok():
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, "FROM python:3.12\n", True)
    assert keys == [] and ok is True


# --- malformed input per format: failure signaled, never raises ------------

def test_malformed_input_never_raises_signals_failure():
    cases = [
        ("bad.json", "json", '{"a": '),
        ("bad.yaml", "yaml", "key:\n\tbad: 1\n"),
        ("bad.toml", "toml", "key = \n"),
        ("bad.ini", "ini", "[a]\nx = 1\nx = 2\n"),  # duplicate option
    ]
    for path, fmt, text in cases:
        art = _artifact(path, fmt)
        keys, ok = extract_config_keys(art, text, True)
        assert keys == [] and ok is False, f"{fmt} should signal failure, not raise"
