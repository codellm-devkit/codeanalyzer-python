import os
from pathlib import Path

from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder


def _action_post_call_sites(project_dir: Path):
    builder = SymbolTableBuilder(project_dir, None)
    module = builder.build_pymodule_from_file(project_dir / "main.py")
    account_move = next(c for c in module.classes.values() if c.name == "AccountMove")
    return {cs.method_name: cs for cs in account_move.methods["action_post"].call_sites}


def test_attribute_calls_resolve_to_the_invoked_method(single_functionalities__method_call_resolution):
    call_sites = _action_post_call_sites(single_functionalities__method_call_resolution)

    assert call_sites["search"].callee_signature == "main.Model.search"
    assert call_sites["helper"].callee_signature == "main.Model.helper"


def test_attribute_calls_never_bind_to_the_enclosing_class(single_functionalities__method_call_resolution):
    call_sites = _action_post_call_sites(single_functionalities__method_call_resolution)

    assert all(
        cs.callee_signature != "main.AccountMove" for cs in call_sites.values()
    ), {name: cs.callee_signature for name, cs in call_sites.items()}


def test_bare_name_calls_keep_their_resolution(single_functionalities__method_call_resolution):
    call_sites = _action_post_call_sites(single_functionalities__method_call_resolution)

    assert call_sites["len"].callee_signature == "builtins.len"
    assert call_sites["len"].is_constructor_call is False
    assert call_sites["str"].callee_signature == "builtins.str.__init__"
    assert call_sites["str"].is_constructor_call is True


def test_call_return_types_reflect_the_call_result(single_functionalities__method_call_resolution):
    call_sites = _action_post_call_sites(single_functionalities__method_call_resolution)

    assert call_sites["search"].return_type == "list"
    assert call_sites["helper"].return_type == "int"
    assert call_sites["len"].return_type == "int"
    assert call_sites["str"].return_type == "str"


def test_builder_accepts_a_relative_project_dir(single_functionalities__method_call_resolution):
    relative_dir = Path(os.path.relpath(single_functionalities__method_call_resolution, Path.cwd()))
    assert not relative_dir.is_absolute()

    call_sites = _action_post_call_sites(relative_dir)

    assert call_sites["search"].callee_signature == "main.Model.search"
    assert call_sites["helper"].callee_signature == "main.Model.helper"


def test_fallback_signatures_strip_only_the_py_suffix(tmp_path):
    builder = SymbolTableBuilder(tmp_path, None)

    cases = {
        "odoo/tools/babel/python_extractor.py": "odoo.tools.babel.python_extractor.extract",
        "odoo/_monkeypatches/pytz.py": "odoo._monkeypatches.pytz.extract",
        "odoo/tools/pycompat.py": "odoo.tools.pycompat.extract",
    }
    for relative_path, expected in cases.items():
        assert builder._fallback_signature(tmp_path / relative_path, "extract") == expected


def test_class_attribute_initializers_are_captured(single_functionalities__method_call_resolution):
    builder = SymbolTableBuilder(single_functionalities__method_call_resolution, None)
    module = builder.build_pymodule_from_file(single_functionalities__method_call_resolution / "main.py")

    account_move = next(c for c in module.classes.values() if c.name == "AccountMove")
    assert account_move.attributes["_name"].initializer == "'account.move'"
    assert account_move.attributes["_inherit"].initializer == "['mail.thread']"

    model = next(c for c in module.classes.values() if c.name == "Model")
    assert model.attributes["env"].initializer == "Environment()"


def test_call_arguments_distinguish_ast_kind_from_inferred_type(single_functionalities__method_call_resolution):
    call_sites = _action_post_call_sites(single_functionalities__method_call_resolution)

    search_args = call_sites["search"].arguments
    assert [a.ast_kind for a in search_args] == ["List"]
    assert search_args[0].inferred_type == "list"

    len_args = call_sites["len"].arguments
    assert [a.ast_kind for a in len_args] == ["Name"]
    assert len_args[0].inferred_type == "list"

    str_args = call_sites["str"].arguments
    assert [a.ast_kind for a in str_args] == ["Call"]

    assert call_sites["helper"].arguments == []


def test_legacy_argument_types_field_is_unchanged(single_functionalities__method_call_resolution):
    call_sites = _action_post_call_sites(single_functionalities__method_call_resolution)

    assert call_sites["search"].argument_types == ["list"]
    assert call_sites["len"].argument_types == ["list"]
