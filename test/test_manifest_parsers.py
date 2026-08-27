"""Task 3: every spec §6 manifest format parses into RawDep records."""
import textwrap
from codeanalyzer.artifacts.parsers import (
    RawDep, normalize_name, parse_lock_pins, parse_manifest,
    parse_requirement_line, parse_requirement_refs,
)


def test_normalize_name():
    assert normalize_name("PyYAML") == "pyyaml"
    assert normalize_name("ruamel.yaml") == "ruamel-yaml"
    assert normalize_name("typing_extensions") == "typing-extensions"


def test_requirements_txt():
    text = textwrap.dedent("""\
        # comment
        requests>=2.31,<3
        pyyaml
        celery[redis]==5.3.*
        -e ./local-pkg
        --index-url https://example.invalid
    """)
    deps, partial = parse_manifest("requirements.txt", text)
    assert not partial
    assert [(d.name, d.spec) for d in deps] == [
        ("requests", ">=2.31,<3"), ("pyyaml", ""), ("celery", "==5.3.*"),
    ]
    assert deps[2].extras == ("redis",)
    assert all(d.kind == "runtime" for d in deps)


def test_requirements_dev_kind():
    deps, _ = parse_manifest("requirements-dev.txt", "pytest\n")
    assert deps[0].kind == "dev"


def test_pyproject_pep621_poetry_and_build():
    text = textwrap.dedent("""\
        [build-system]
        requires = ["setuptools>=68"]
        [project]
        dependencies = ["requests>=2.31"]
        [project.optional-dependencies]
        docs = ["sphinx"]
        [tool.poetry.dependencies]
        python = "^3.10"
        rich = "^13.0"
        [tool.poetry.group.dev.dependencies]
        mypy = "*"
    """)
    deps, partial = parse_manifest("pyproject.toml", text)
    assert not partial
    by = {(d.name, d.kind) for d in deps}
    assert ("setuptools", "build") in by
    assert ("requests", "runtime") in by
    assert ("sphinx", "optional") in by
    assert ("rich", "runtime") in by and ("mypy", "dev") in by
    assert ("python", "runtime") not in {(d.name, d.kind) for d in deps}  # interpreter, not a dep


def test_setup_py_static_literals():
    text = 'from setuptools import setup\nsetup(install_requires=["flask>=2"], extras_require={"test": ["pytest"]})\n'
    deps, partial = parse_manifest("setup.py", text)
    assert not partial
    assert {(d.name, d.kind) for d in deps} == {("flask", "runtime"), ("pytest", "optional")}


def test_setup_py_dynamic_is_partial():
    text = "from setuptools import setup\nreqs = compute()\nsetup(install_requires=reqs)\n"
    deps, partial = parse_manifest("setup.py", text)
    assert partial and deps == []


def test_setup_cfg():
    text = "[options]\ninstall_requires =\n    numpy>=1.24\n    pandas\n"
    deps, _ = parse_manifest("setup.cfg", text)
    assert [(d.name, d.spec) for d in deps] == [("numpy", ">=1.24"), ("pandas", "")]


def test_pipfile_and_environment_yml():
    pip = '[packages]\nrequests = ">=2.31"\n[dev-packages]\nblack = "*"\n'
    deps, _ = parse_manifest("Pipfile", pip)
    assert {(d.name, d.kind, d.spec) for d in deps} == {
        ("requests", "runtime", ">=2.31"), ("black", "dev", ""),
    }
    env = "dependencies:\n  - numpy=1.26\n  - scipy>=1.10\n  - pip\n  - pip:\n      - fastapi>=0.100\n"
    deps, _ = parse_manifest("environment.yml", env)
    assert {(d.name, d.spec) for d in deps} == {
        ("numpy", "=1.26"), ("scipy", ">=1.10"), ("fastapi", ">=0.100"),
    }


def test_lock_pins():
    poetry = '[[package]]\nname = "requests"\nversion = "2.31.0"\n[[package]]\nname = "PyYAML"\nversion = "6.0.1"\n'
    assert parse_lock_pins("poetry.lock", poetry) == {"requests": "2.31.0", "pyyaml": "6.0.1"}
    uv = '[[package]]\nname = "requests"\nversion = "2.32.0"\n'
    assert parse_lock_pins("uv.lock", uv) == {"requests": "2.32.0"}
    pipf = '{"default": {"requests": {"version": "==2.31.0"}}, "develop": {}}'
    assert parse_lock_pins("Pipfile.lock", pipf) == {"requests": "2.31.0"}


def test_requirements_kind_word_boundary():
    deps, _ = parse_manifest("requirements-docker.txt", "requests\n")
    assert deps[0].kind == "runtime"
    deps, _ = parse_manifest("requirements-latest.txt", "requests\n")
    assert deps[0].kind == "runtime"
    deps, _ = parse_manifest("requirements-dev.txt", "requests\n")
    assert deps[0].kind == "dev"


def test_malformed_manifests_are_partial():
    assert parse_manifest("pyproject.toml", "not [ valid toml") == ([], True)
    assert parse_manifest("Pipfile", "not [ valid toml") == ([], True)
    assert parse_manifest("setup.cfg", "[options\nbroken") == ([], True)
    assert parse_manifest("environment.yml", "dependencies: [unclosed") == ([], True)


def test_malformed_lock_files_return_empty():
    assert parse_lock_pins("poetry.lock", "not [ valid toml") == {}
    assert parse_lock_pins("uv.lock", "not [ valid toml") == {}
    assert parse_lock_pins("Pipfile.lock", "not valid json") == {}


def test_poetry_inline_table_dep():
    text = textwrap.dedent("""\
        [tool.poetry.dependencies]
        python = "^3.10"
        requests = {version = "^2.28", extras = ["socks"]}
    """)
    deps, partial = parse_manifest("pyproject.toml", text)
    assert not partial
    assert deps == [RawDep("requests", "^2.28", "runtime", ("socks",))]


def test_pipfile_inline_table_extras():
    text = '[packages]\nrequests = {version = "*", extras = ["security"]}\n'
    deps, _ = parse_manifest("Pipfile", text)
    assert deps == [RawDep("requests", "", "runtime", ("security",))]


def test_direct_ref_and_line_continuation():
    assert parse_requirement_line("mylib @ git+https://x/y.git") == RawDep("mylib", "", "runtime", ())
    assert parse_requirement_line("pkg==1.4.2 \\") == RawDep("pkg", "==1.4.2", "runtime", ())


def test_parse_requirement_refs():
    text = "requests\n-r base.txt\n-c constraints/prod.txt\npytest\n"
    assert parse_requirement_refs(text) == ["base.txt", "constraints/prod.txt"]
    long_form = "--requirement base.txt\n--constraint constraints/prod.txt\n"
    assert parse_requirement_refs(long_form) == ["base.txt", "constraints/prod.txt"]


def test_setup_py_syntax_error():
    deps, partial = parse_manifest("setup.py", "def foo(:\n    pass")
    assert deps == []
    assert partial is True
