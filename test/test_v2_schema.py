from codeanalyzer.schema.py_schema import (
    Analysis, PyApplication, PyModule, PyCallable, BodyNode, Span, DdgEdge,
)
from codeanalyzer.schema import model_dump_json, model_validate_json, PYDANTIC_V2


def _fields(model_cls):
    """Field map across Pydantic v1/v2."""
    return model_cls.model_fields if PYDANTIC_V2 else model_cls.__fields__


def test_envelope_round_trips():
    fn = PyCallable(
        name="f", path="m.py", signature="m.f",
        id="can://python/app/m.py/f()", kind="function",
        span=Span(start=(1, 0), end=(2, 12), bytes=(0, 21)),
        body={"@entry": BodyNode(kind="entry")},
    )
    mod = PyModule(file_path="m.py", module_name="m",
                   id="can://python/app/m.py", kind="module",
                   source="def f():\n    return 1\n", functions={"f": fn})
    app = PyApplication(id="can://python/app", kind="application",
                        symbol_table={"m.py": mod})
    analysis = Analysis(max_level=1, k_limit=3, application=app)
    blob = model_dump_json(analysis)
    back = model_validate_json(Analysis, blob)
    assert back.schema_version == "2.0.0"
    assert back.application.symbol_table["m.py"].functions["f"].body["@entry"].kind == "entry"


def test_ddg_edge_carries_prov():
    e = DdgEdge(src="15:4", dst="17:4", var="h", prov=["ssa"])
    assert e.prov == ["ssa"]


def test_program_graphs_field_is_gone():
    assert "program_graphs" not in _fields(PyApplication)
