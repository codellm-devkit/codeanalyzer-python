"""Task 2: config-key flatteners with reference recognition."""
import textwrap

from codeanalyzer.artifacts.config_keys import extract_config_keys, is_config_eligible
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


# --- dockerfile: ENV/ARG forms, continuations, quoting (#165) --------------

def test_dockerfile_env_single_and_multi_key():
    text = textwrap.dedent("""\
        FROM python:3.12-slim
        ENV APP_MODE=production
        ENV A=1 B=2
        """)
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["APP_MODE"].value == "production" and by["APP_MODE"].namespace == "env"
    assert by["A"].value == "1" and by["B"].value == "2"
    assert all(k.namespace == "env" for k in (by["APP_MODE"], by["A"], by["B"]))


def test_dockerfile_env_legacy_space_form():
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, "ENV MY_NAME John Doe\n", True)
    assert ok is True
    assert _by_key(keys)["MY_NAME"].value == "John Doe"


def test_dockerfile_env_legacy_space_form_keeps_quotes_verbatim():
    # Real Docker does NO quote processing in the legacy form (moby's
    # parseNameVal) -- unlike the key=value form, quotes stay in the value.
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, 'ENV MY_NAME "John Doe"\n', True)
    assert ok is True
    assert _by_key(keys)["MY_NAME"].value == '"John Doe"'


def test_dockerfile_env_legacy_space_form_tab_separated():
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, "ENV MY_NAME\tJohn Doe\n", True)
    assert ok is True
    assert _by_key(keys)["MY_NAME"].value == "John Doe"


def test_dockerfile_env_quoted_values_in_multi_key_form():
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, 'ENV GREETING="hello world" OTHER=2\n', True)
    assert ok is True
    by = _by_key(keys)
    assert by["GREETING"].value == "hello world"
    assert by["OTHER"].value == "2"


def test_dockerfile_env_backslash_continuation():
    text = "ENV A=1 \\\n    B=2 \\\n    C=3\n"
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["A"].value == "1" and by["B"].value == "2" and by["C"].value == "3"


def test_dockerfile_env_docker_docs_example_escaped_spaces_and_continuation():
    # Docker's own ENV docs example: a quoted value, a backslash-escaped-
    # space value, and a third key on a continuation line -- all three exact.
    text = (
        'ENV MY_NAME="John Doe" MY_DOG=Rex\\ The\\ Dog \\\n'
        '    MY_CAT=fluffy\n'
    )
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["MY_NAME"].value == "John Doe"
    assert by["MY_DOG"].value == "Rex The Dog"
    assert by["MY_CAT"].value == "fluffy"


def test_dockerfile_env_double_backslash_collapses_to_one():
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, "ENV PATTERN=a\\\\b\n", True)
    assert ok is True
    assert _by_key(keys)["PATTERN"].value == "a\\b"


def test_dockerfile_arg_with_and_without_default():
    text = "ARG BUILD_REV\nARG VERSION=1.0\n"
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["BUILD_REV"].value is None and by["BUILD_REV"].namespace == "dockerfile"
    assert by["VERSION"].value == "1.0" and by["VERSION"].namespace == "dockerfile"


def test_dockerfile_arg_and_env_same_name_mint_distinct_ids():
    # ARG-then-ENV-promotion is a common idiom (`ARG V=1` / `ENV V=$V`) --
    # both namespaces key on the bare name, so this is the one shape where a
    # naive id would collide; the "dockerfile" namespace disambiguates.
    text = "ARG APP_VERSION=1.0\nENV APP_VERSION=$APP_VERSION\n"
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    matches = [k for k in keys if k.key == "APP_VERSION"]
    assert len(matches) == 2
    assert len({k.id for k in matches}) == 2
    assert {k.namespace for k in matches} == {"env", "dockerfile"}
    env_key = next(k for k in matches if k.namespace == "env")
    assert env_key.value == "$APP_VERSION" and env_key.references == ["$APP_VERSION"]


def test_dockerfile_comments_blank_and_other_directives_skipped():
    text = "# a comment\n\nRUN pip install -e .\nENV OK=1\n"
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    assert [k.key for k in keys] == ["OK"]


def test_dockerfile_directives_are_case_insensitive():
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, "env foo=bar\narg baz=qux\n", True)
    assert ok is True
    by = _by_key(keys)
    assert by["foo"].value == "bar" and by["foo"].namespace == "env"
    assert by["baz"].value == "qux" and by["baz"].namespace == "dockerfile"


def test_dockerfile_with_no_env_or_arg_yields_empty():
    art = _artifact("Dockerfile", "dockerfile")
    keys, ok = extract_config_keys(art, "FROM python:3.12-slim\n", True)
    assert keys == [] and ok is True


def test_dockerfile_is_config_eligible():
    assert is_config_eligible(_artifact("Dockerfile", "dockerfile")) is True


# --- compose/k8s env recognition: dual-minted "env" namespace keys (#165) --

def test_compose_environment_map_dual_mints_env_namespace():
    text = textwrap.dedent("""\
        services:
          web:
            build: .
            environment:
              APP_MODE: production
        """)
    art = _artifact("docker-compose.yml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    dotted, bare = by["services.web.environment.APP_MODE"], by["APP_MODE"]
    assert dotted.namespace == "yaml" and dotted.value == "production"
    assert bare.namespace == "env" and bare.value == "production"
    assert dotted.id != bare.id  # dual-mint: distinct ids by construction


def test_compose_environment_list_dual_mints_env_namespace():
    text = textwrap.dedent("""\
        services:
          web:
            environment:
              - APP_MODE=production
              - BARE_KEY
        """)
    art = _artifact("docker-compose.yml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["APP_MODE"].namespace == "env" and by["APP_MODE"].value == "production"
    # a bare list entry ("no value here, inherit from the environment") has
    # no leaf "=value" to stringify -- same "" a null stringifies to
    # elsewhere in this module (no dockerfile-ARG-style None distinction was
    # asked for here).
    assert by["BARE_KEY"].namespace == "env" and by["BARE_KEY"].value == ""
    # the plain yaml flatten still sees the list form under its own path.
    assert by["services.web.environment.0"].namespace == "yaml"
    assert by["services.web.environment.0"].value == "APP_MODE=production"


def test_k8s_env_list_dual_mints_env_namespace_at_any_depth():
    text = textwrap.dedent("""\
        spec:
          template:
            spec:
              containers:
                - name: app
                  env:
                    - name: DATABASE_URL
                      value: postgresql://x
        """)
    art = _artifact("k8s/deployment.yml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    by = _by_key(keys)
    assert by["DATABASE_URL"].namespace == "env"
    assert by["DATABASE_URL"].value == "postgresql://x"
    # still dual-minted under its own full dotted path too.
    assert by["spec.template.spec.containers.0.env.0.name"].namespace == "yaml"


def test_k8s_env_list_missing_value_sibling():
    # a `valueFrom`-style entry (no literal "value:") -- missing sibling,
    # same "" a null stringifies to elsewhere in this module.
    text = textwrap.dedent("""\
        containers:
          - env:
              - name: SECRET_REF
        """)
    art = _artifact("k8s/pod.yml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    assert _by_key(keys)["SECRET_REF"].namespace == "env"
    assert _by_key(keys)["SECRET_REF"].value == ""


def test_env_recognition_scoped_to_yaml_only_not_json_or_toml():
    # compose/k8s files are always yaml in this ecosystem -- the recognition
    # pass is scoped to namespace=="yaml"; the identical shape in json is
    # NOT dual-minted.
    text = '{"services": {"web": {"environment": {"APP_MODE": "x"}}}}'
    art = _artifact("compose.json", "json")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    assert {k.key for k in keys} == {"services.web.environment.APP_MODE"}
    assert all(k.namespace == "json" for k in keys)


def test_yaml_top_level_key_and_env_dual_mint_same_name_no_id_collision():
    # Reviewer-reported: a top-level yaml key sharing a name with a
    # compose/k8s-recognized env var, in the SAME file, must not collide on
    # id -- the "differs by construction" claim only holds for the RECOGNIZED
    # shapes' own dotted paths, not for an unrelated top-level leaf.
    text = textwrap.dedent("""\
        COMPOSE_ONLY_KEY: top
        services:
          web:
            environment:
              COMPOSE_ONLY_KEY: nested
        """)
    art = _artifact("docker-compose.yml", "yaml")
    keys, ok = extract_config_keys(art, text, True)
    assert ok is True
    matches = [k for k in keys if k.key == "COMPOSE_ONLY_KEY"]
    assert len(matches) == 2
    assert len({k.id for k in matches}) == 2
    assert {k.namespace for k in matches} == {"yaml", "env"}
    by_ns = {k.namespace: k for k in matches}
    assert by_ns["yaml"].value == "top"
    assert by_ns["env"].value == "nested"


def test_compose_k8s_env_recognition_is_deterministic_and_sorted():
    text = textwrap.dedent("""\
        services:
          web:
            environment:
              APP_MODE: production
              OTHER: x
          db:
            environment:
              - DB_KEY=1
        """)
    art = _artifact("docker-compose.yml", "yaml")
    keys1, ok1 = extract_config_keys(art, text, True)
    keys2, ok2 = extract_config_keys(art, text, True)
    assert ok1 is True and ok2 is True
    snap = lambda ks: [(k.id, k.key, k.namespace, k.value) for k in ks]
    assert snap(keys1) == snap(keys2)
    assert [k.key for k in keys1] == sorted(k.key for k in keys1)


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
    # "dockerfile" is namespace-eligible as of #165 (see the dedicated
    # dockerfile section above) -- use a genuinely unsupported format here.
    art = _artifact("requirements.txt", "requirements")
    keys, ok = extract_config_keys(art, "requests==2.32.3\n", True)
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


# --- is_config_eligible: Task 3's core.py wiring uses this to skip a disk
# read + extraction attempt on artifacts that can never yield config keys ---

def test_is_config_eligible_env_family_regardless_of_format():
    # .env/.env.*/.flaskenv are format="text" on disk (discovery.py) -- still
    # eligible via the basename rule, independent of the declared format.
    for name in (".env", ".env.production", ".flaskenv"):
        assert is_config_eligible(_artifact(name, "text")) is True


def test_is_config_eligible_by_namespace_bearing_format():
    for fmt in ("yaml", "json", "toml", "ini", "properties"):
        assert is_config_eligible(_artifact(f"config.{fmt}", fmt)) is True


def test_is_config_eligible_false_for_other_formats():
    # "dockerfile" is namespace-eligible as of #165 -- see
    # test_dockerfile_is_config_eligible above.
    for fmt in ("text", "requirements"):
        assert is_config_eligible(_artifact("misc", fmt)) is False


def test_is_config_eligible_false_for_binary_even_if_env_basename():
    # A rule-matched-but-undecodable file downgrades to format="binary"
    # regardless of basename (discovery.py) -- never eligible, there is no
    # decodable text to flatten.
    assert is_config_eligible(_artifact(".env", "binary")) is False
