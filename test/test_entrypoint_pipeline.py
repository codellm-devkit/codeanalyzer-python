from codeanalyzer.schema import model_dump
from pathlib import Path

import pytest

from codeanalyzer.entrypoints.pipeline import detect_entrypoints
from codeanalyzer.entrypoints.rules import RulesError
from codeanalyzer.schema.py_schema import PyApplication


def test_pass_is_a_noop_on_an_empty_application(tmp_path: Path):
    app = PyApplication(symbol_table={})
    detect_entrypoints(app, tmp_path)
    assert app.entrypoint_report.errors == []


def test_pass_never_raises_and_records_the_failure(tmp_path: Path, monkeypatch):
    """A finder crash must lose flags, not the analysis."""
    import codeanalyzer.entrypoints.pipeline as p

    def boom(*a, **k):
        raise RuntimeError("finder exploded")

    monkeypatch.setattr(p, "_run_stages", boom)
    app = PyApplication(symbol_table={})
    detect_entrypoints(app, tmp_path)          # must not raise
    assert any("finder exploded" in e for e in app.entrypoint_report.errors)


def test_derives_is_entrypoint_from_the_list(tmp_path: Path):
    """`entrypoints` is written entirely by the pass itself (it clears and
    rebuilds the list every run, see the duplication regression test below)
    -- so drive this through a real decorator match rather than hand-seeding
    the list, and check `_derive_flags` sets the boolean from the result."""
    from codeanalyzer.schema.py_schema import PyCallable, PyDecorator, PyImport, PyModule

    fn = PyCallable(name="f", path="a.py", signature="a.f")
    fn.decorators.append(PyDecorator(name="route", qualified_name="flask.sansio.scaffold.Scaffold.route"))
    app = PyApplication(
        symbol_table={
            "a.py": PyModule(
                file_path="a.py",
                module_name="a",
                functions={"f": fn},
                imports=[PyImport(module="flask", name="Flask")],
            )
        }
    )
    detect_entrypoints(app, tmp_path)
    assert fn.is_entrypoint is True


def test_malformed_user_rules_file_raises_instead_of_being_swallowed(tmp_path: Path):
    """A bad --entrypoint-rules file is a CONFIGURATION error, not a detection
    failure: it must stop the run via RulesError, not land quietly in
    entrypoint_report.errors like a finder crash would."""
    bad_rules = tmp_path / "bad_rules.yml"
    bad_rules.write_text("frameworks: not-a-mapping\n")
    app = PyApplication(symbol_table={})

    with pytest.raises(RulesError):
        detect_entrypoints(app, tmp_path, rule_paths=(bad_rules,))

    assert app.entrypoint_report.errors == []


def test_ruleset_provenance_distinguishes_shipped_from_user_rules(tmp_path: Path):
    """A shipped rule's record must say "shipped" even when a user rules
    file is also loaded -- the ruleset field exists so someone debugging a
    surprising flag can find which file produced it."""
    from codeanalyzer.schema.py_schema import PyCallable, PyDecorator, PyImport, PyModule

    user_rules = tmp_path / "user.yml"
    user_rules.write_text(
        "frameworks:\n"
        "  inhouse:\n"
        "    detect: [inhouse]\n"
        "    decorators:\n"
        "      - id: inhouse.handler\n"
        "        match: 'inhouse.app.handler'\n"
    )

    shipped_fn = PyCallable(name="f", path="a.py", signature="a.f")
    shipped_fn.decorators.append(PyDecorator(name="app.route", qualified_name="flask.sansio.scaffold.Scaffold.route"))
    user_fn = PyCallable(name="g", path="a.py", signature="a.g")
    user_fn.decorators.append(PyDecorator(name="handler", qualified_name="inhouse.app.handler"))

    app = PyApplication(
        symbol_table={
            "a.py": PyModule(
                file_path="a.py",
                module_name="a",
                functions={"f": shipped_fn, "g": user_fn},
                imports=[
                    PyImport(module="flask", name="Flask"),
                    PyImport(module="inhouse", name="app"),
                ],
            )
        }
    )
    detect_entrypoints(app, tmp_path, rule_paths=(user_rules,))

    assert shipped_fn.entrypoints[0].ruleset == "shipped"
    assert user_fn.entrypoints[0].ruleset == f"user:{user_rules}"


def test_direct_base_class_is_flagged_when_the_import_resolves_it(tmp_path: Path):
    """``class V(APIView)`` under ``from rest_framework.views import APIView``
    is the idiomatic spelling -- base_classes stores the written name
    ``"APIView"``, and it must resolve via the module's own import table."""
    from codeanalyzer.schema.py_schema import PyClass, PyImport, PyModule

    cls = PyClass(name="V", signature="a.V", base_classes=["APIView"])
    app = PyApplication(
        symbol_table={
            "a.py": PyModule(
                file_path="a.py",
                module_name="a",
                types={"a.V": cls},
                imports=[PyImport(module="rest_framework.views", name="APIView")],
            )
        }
    )
    detect_entrypoints(app, tmp_path)
    assert cls.is_entrypoint is True


def test_direct_base_class_is_not_flagged_without_the_import(tmp_path: Path):
    """``rest_framework`` is imported (so the drf framework gate passes) but
    ``APIView`` itself is never imported into this module -- the written
    "APIView" base has nothing to resolve against and must not be flagged."""
    from codeanalyzer.schema.py_schema import PyClass, PyImport, PyModule

    cls = PyClass(name="V", signature="a.V", base_classes=["APIView"])
    app = PyApplication(
        symbol_table={
            "a.py": PyModule(
                file_path="a.py",
                module_name="a",
                types={"a.V": cls},
                imports=[PyImport(module="rest_framework", name="serializers")],
            )
        }
    )
    detect_entrypoints(app, tmp_path)
    assert cls.is_entrypoint is False


def test_dotted_base_class_resolves_through_a_module_import(tmp_path: Path):
    """``class V(views.APIView)`` under ``from rest_framework import views``
    -- the dotted base's head ("views") is the imported name."""
    from codeanalyzer.schema.py_schema import PyClass, PyImport, PyModule

    cls = PyClass(name="V", signature="a.V", base_classes=["views.APIView"])
    app = PyApplication(
        symbol_table={
            "a.py": PyModule(
                file_path="a.py",
                module_name="a",
                types={"a.V": cls},
                imports=[PyImport(module="rest_framework", name="views")],
            )
        }
    )
    detect_entrypoints(app, tmp_path)
    assert cls.is_entrypoint is True


def test_running_the_pass_twice_does_not_duplicate_entrypoints(tmp_path: Path):
    """#27 regression: on a warm cache, `_build_symbol_table` reuses the SAME
    cached PyModule/PyCallable objects across runs. `detect_entrypoints` must
    be safe to call again on that same PyApplication without appending
    duplicate PyEntrypoint records onto the reused nodes."""
    from codeanalyzer.schema.py_schema import PyCallable, PyDecorator, PyImport, PyModule

    fn = PyCallable(name="f", path="a.py", signature="a.f")
    fn.decorators.append(PyDecorator(name="route", qualified_name="flask.sansio.scaffold.Scaffold.route"))
    app = PyApplication(
        symbol_table={
            "a.py": PyModule(
                file_path="a.py",
                module_name="a",
                functions={"f": fn},
                imports=[PyImport(module="flask", name="Flask")],
            )
        }
    )

    detect_entrypoints(app, tmp_path)
    first = [model_dump(e) for e in fn.entrypoints]
    assert len(first) == 1

    detect_entrypoints(app, tmp_path)
    second = [model_dump(e) for e in fn.entrypoints]
    assert second == first


# ----------------------------------------------------------------------------------------------
# #177: the report counts what failed to resolve, decorators fall back to the import table, and
# odoo controllers are a shipped framework.
# ----------------------------------------------------------------------------------------------


def _odoo_module():
    from codeanalyzer.schema.py_schema import PyCallable, PyClass, PyDecorator, PyImport, PyModule

    index = PyCallable(name="index", path="c.py", signature="c.Ctl.index")
    index.decorators.append(PyDecorator(name="http.route", qualified_name=None,
                                        positional_arguments=['"/x"'],
                                        keyword_arguments={"auth": '"public"'}))
    ctl = PyClass(name="Ctl", signature="c.Ctl", base_classes=["http.Controller"],
                  callables={"index": index})
    mystery = PyCallable(name="m", path="c.py", signature="c.m")
    mystery.decorators.append(PyDecorator(name="whatever.deco", qualified_name=None))
    orphan = PyClass(name="O", signature="c.O", base_classes=["Nowhere"])
    return PyModule(
        file_path="c.py", module_name="c",
        imports=[PyImport(module="odoo", name="http")],
        types={"Ctl": ctl, "O": orphan}, functions={"m": mystery},
    )


def test_odoo_controller_detected_through_import_table_fallback(tmp_path: Path):
    mod = _odoo_module()
    app = PyApplication(symbol_table={"c.py": mod})
    detect_entrypoints(app, tmp_path)
    assert "odoo" in app.entrypoint_report.frameworks_detected
    index = mod.types["Ctl"].callables["index"]
    assert [e.rule for e in index.entrypoints] == ["odoo.route"]
    assert index.entrypoints[0].route == "/x"
    assert index.entrypoints[0].http_methods == ["GET"]
    assert index.entrypoints[0].evidence == "odoo.http.route"
    assert [e.rule for e in mod.types["Ctl"].entrypoints] == ["odoo.controller"]


def test_report_counts_decorators_and_bases_nothing_resolves(tmp_path: Path):
    mod = _odoo_module()
    app = PyApplication(symbol_table={"c.py": mod})
    detect_entrypoints(app, tmp_path)
    # `http.route` resolved through the import table, so it is NOT unresolved;
    # `whatever.deco` and base `Nowhere` map to nothing anywhere.
    assert app.entrypoint_report.unresolved == {"whatever.deco": 1, "Nowhere": 1}
    detect_entrypoints(app, tmp_path)  # idempotent on a warm cache
    assert app.entrypoint_report.unresolved == {"whatever.deco": 1, "Nowhere": 1}
