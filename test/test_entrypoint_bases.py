from codeanalyzer.entrypoints.matching import entrypoints_from_bases
from codeanalyzer.entrypoints.rules import BaseRule
from codeanalyzer.schema.py_schema import PyCallable, PyClass

RULE = BaseRule(
    id="drf.apiview",
    match="rest_framework.views.APIView",
    transitive=True,
    dispatch=["get", "post", "put"],
)


def _cls(*methods: str, bases=("rest_framework.views.APIView",)) -> PyClass:
    return PyClass(
        name="V",
        signature="a.V",
        base_classes=list(bases),
        callables={m: PyCallable(name=m, path="a.py", signature=f"a.V.{m}") for m in methods},
    )


def test_class_is_flagged_and_only_defined_methods_dispatch():
    cls = _cls("get")                       # defines get, not post
    class_eps, method_eps = entrypoints_from_bases(cls, "drf", [RULE], lambda b: b)
    assert len(class_eps) == 1
    assert list(method_eps) == ["get"], "no phantom post entrypoint"


def test_methods_point_back_at_the_routed_class_via():
    cls = _cls("get")
    cls.id = "can://python/app/a.py/V"
    _, method_eps = entrypoints_from_bases(cls, "drf", [RULE], lambda b: b)
    assert method_eps["get"][0].via == "can://python/app/a.py/V"


def test_transitive_base_resolves_one_hop():
    cls = _cls("get", bases=("app.BaseView",))
    resolve = {"app.BaseView": "rest_framework.views.APIView"}.get
    class_eps, _ = entrypoints_from_bases(cls, "drf", [RULE], resolve)
    assert len(class_eps) == 1


def test_unrelated_class_is_not_flagged():
    cls = _cls("get", bases=("object",))
    class_eps, method_eps = entrypoints_from_bases(cls, "drf", [RULE], lambda b: b)
    assert class_eps == [] and method_eps == {}
