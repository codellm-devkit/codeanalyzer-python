from codeanalyzer.schema.py_schema import (
    PyApplication, PyCallable, PyClass, PyEntrypoint, PyEntrypointReport,
)


def test_entrypoint_record_defaults():
    e = PyEntrypoint(framework="flask", confidence="certain", rule="flask.route", ruleset="shipped")
    assert e.evidence is None and e.route is None and e.via is None
    assert e.http_methods == []


def test_callable_and_class_carry_entrypoints():
    c = PyCallable(name="f", path="a.py", signature="a.f")
    k = PyClass(name="C", signature="a.C")
    assert c.entrypoints == [] and c.is_entrypoint is False
    assert k.entrypoints == [] and k.is_entrypoint is False


def test_application_carries_a_report():
    app = PyApplication(symbol_table={})
    assert isinstance(app.entrypoint_report, PyEntrypointReport)
    assert app.entrypoint_report.frameworks_detected == []
