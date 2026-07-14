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
