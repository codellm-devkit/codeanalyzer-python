"""Task 1 (#162): literal (`value`) and bare-name (`name`) capture on
`PyCallArgument`, populated at symbol_table_builder.py:786 alongside the
existing `ast_kind`/`inferred_type`."""
from codeanalyzer.schema import model_dump_json, model_validate_json
from codeanalyzer.schema.py_schema import (
    BodyNode, PyApplication, PyCallable, PyCallArgument, PyModule,
)
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder

SRC = """\
KEY = "irrelevant"


class Obj:
    attr = 1


obj = Obj()


def f(x):
    return x


def g():
    f("DB_HOST")
    f(3)
    f(True)
    f(None)
    f(KEY)
    f(obj.attr)
"""


def _g_call_sites(tmp_path):
    """`f(...)` call sites from `g()`, in source order (`_iter_calls_in_scope`
    walks the function body sequentially) -- indexing by position rather than
    a hardcoded line number keeps this robust to SRC's exact formatting."""
    (tmp_path / "mod.py").write_text(SRC)
    module = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(tmp_path / "mod.py")
    sites = sorted(module.functions["g"].call_sites, key=lambda cs: (cs.start_line, cs.start_column))
    assert [cs.method_name for cs in sites] == ["f"] * 6
    return sites


def test_string_constant_is_json_encoded(tmp_path):
    call = _g_call_sites(tmp_path)[0]
    assert call.arguments[0].value == '"DB_HOST"'
    assert call.arguments[0].name is None


def test_int_constant_is_json_encoded(tmp_path):
    call = _g_call_sites(tmp_path)[1]
    assert call.arguments[0].value == "3"


def test_bool_constant_is_json_encoded(tmp_path):
    call = _g_call_sites(tmp_path)[2]
    assert call.arguments[0].value == "true"


def test_none_constant_is_json_encoded(tmp_path):
    call = _g_call_sites(tmp_path)[3]
    assert call.arguments[0].value == "null"


def test_bare_name_captures_identifier_not_value(tmp_path):
    call = _g_call_sites(tmp_path)[4]
    assert call.arguments[0].value is None
    assert call.arguments[0].name == "KEY"


def test_attribute_argument_captures_neither(tmp_path):
    call = _g_call_sites(tmp_path)[5]
    assert call.arguments[0].value is None
    assert call.arguments[0].name is None


def test_l1_round_trip_through_compat_helpers():
    fn = PyCallable(
        name="f", path="a.py", signature="a.f",
        body={"1:0": BodyNode(
            kind="call",
            arguments=[PyCallArgument(ast_kind="Constant", value='"DB_HOST"'),
                       PyCallArgument(ast_kind="Name", name="KEY")],
        )},
    )
    mod = PyModule(file_path="a.py", module_name="a", functions={"f": fn})
    app = PyApplication(symbol_table={"a.py": mod})
    back = model_validate_json(PyApplication, model_dump_json(app))
    args = back.symbol_table["a.py"].functions["f"].body["1:0"].arguments
    assert args[0].value == '"DB_HOST"' and args[0].name is None
    assert args[1].value is None and args[1].name == "KEY"


def test_old_payload_defaults_both_to_none():
    """A payload written before #162 has no `value`/`name` keys at all --
    must still validate, defaulting both to `None`."""
    arg = PyCallArgument(ast_kind="Constant")
    assert arg.value is None
    assert arg.name is None
