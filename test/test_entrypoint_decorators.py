from codeanalyzer.entrypoints.matching import entrypoints_from_decorators, match_pattern
from codeanalyzer.entrypoints.rules import DecoratorRule
from codeanalyzer.schema.py_schema import PyCallable, PyDecorator


def test_brace_alternation_and_wildcard():
    assert match_pattern("flask.Blueprint.{get,post}", "flask.Blueprint.get")
    assert not match_pattern("flask.Blueprint.{get,post}", "flask.Blueprint.delete")
    assert match_pattern("rest_framework.viewsets.*", "rest_framework.viewsets.ModelViewSet")
    assert not match_pattern("flask.Flask.route", "flask.Flask.routes")


def test_wildcard_does_not_cross_a_dot():
    assert match_pattern("rest_framework.viewsets.*", "rest_framework.viewsets.ModelViewSet")
    assert not match_pattern(
        "rest_framework.viewsets.*", "rest_framework.viewsets.mixins.ListModelMixin"
    )


def test_route_and_methods_are_extracted():
    fn = PyCallable(name="h", path="a.py", signature="a.h")
    fn.decorators.append(
        PyDecorator(
            name="app.route",
            qualified_name="flask.Flask.route",
            positional_arguments=["'/products'"],
            keyword_arguments={"methods": "['POST']"},
        )
    )
    rule = DecoratorRule(
        id="flask.route",
        match="flask.Flask.route",
        route={"from": "positional", "index": 0},
        methods={"from": "keyword", "name": "methods", "default": ["GET"]},
    )
    (ep,) = entrypoints_from_decorators(fn, "flask", [rule])
    assert ep.route == "/products"
    assert ep.http_methods == ["POST"]
    assert ep.rule == "flask.route" and ep.ruleset == "shipped"


def test_verb_comes_from_the_matched_suffix():
    fn = PyCallable(name="h", path="a.py", signature="a.h")
    fn.decorators.append(
        PyDecorator(name="router.post", qualified_name="fastapi.APIRouter.post",
                    positional_arguments=["'/x'"])
    )
    rule = DecoratorRule(
        id="fastapi.router-verb",
        match="fastapi.APIRouter.{get,post}",
        route={"from": "positional", "index": 0},
        methods={"from": "match_suffix"},
    )
    (ep,) = entrypoints_from_decorators(fn, "fastapi", [rule])
    assert ep.http_methods == ["POST"]


def test_unresolved_decorator_never_matches():
    """qualified_name is None when Jedi could not resolve; must not guess."""
    fn = PyCallable(name="h", path="a.py", signature="a.h")
    fn.decorators.append(PyDecorator(name="app.route", qualified_name=None))
    rule = DecoratorRule(id="flask.route", match="flask.Flask.route")
    assert entrypoints_from_decorators(fn, "flask", [rule]) == []


def test_wildcard_inside_alternation_expands():
    from codeanalyzer.entrypoints.matching import match_pattern
    assert match_pattern("{route,*.route,*.*.route}", "route")
    assert match_pattern("{route,*.route,*.*.route}", "http.route")
    assert match_pattern("{route,*.route,*.*.route}", "odoo.http.route")
    assert not match_pattern("{route,*.route,*.*.route}", "a.b.c.route")


def test_heuristic_rules_match_the_written_spelling_without_a_framework():
    from codeanalyzer.entrypoints.matching import entrypoints_from_decorators
    from codeanalyzer.entrypoints.rules import load_rules
    from codeanalyzer.schema.py_schema import PyCallable, PyDecorator
    fn = PyCallable(name="f", path="a.py", signature="a.f")
    fn.decorators.append(PyDecorator(name="http.route", qualified_name=None,
                                     positional_arguments=['"/x"']))
    fn.decorators.append(PyDecorator(name="router.post", qualified_name=None,
                                     positional_arguments=['"/y"']))
    eps = entrypoints_from_decorators(fn, "heuristic", load_rules().heuristics, None, on_written=True)
    assert [(e.rule, e.route, e.http_methods, e.confidence, e.evidence) for e in eps] == [
        ("heuristic.http-route", "/x", [], "heuristic", "http.route"),
        ("heuristic.http-verb", "/y", ["POST"], "heuristic", "router.post"),
    ]
