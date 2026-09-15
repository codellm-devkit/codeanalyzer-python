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


# --- http_methods carries HTTP methods only (#213) ---

def _probe(qualified: str, rules):
    """One decorator through the shipped rules, returning its entrypoints."""
    fn = PyCallable(name="h", path="a.py", signature="a.h")
    fn.decorators.append(
        PyDecorator(
            name=qualified,
            qualified_name=qualified,
            positional_arguments=["'/ws'"],
        )
    )
    return entrypoints_from_decorators(fn, "heuristic", rules)


def _heuristics():
    from codeanalyzer.entrypoints.rules import load_rules

    return load_rules().heuristics


def test_a_websocket_suffix_is_not_an_http_method():
    """`heuristic.http-verb` matches `.websocket` so the entrypoint IS recorded --
    but WEBSOCKET is not an HTTP method, and `http_methods` is what consumers
    filter on to enumerate real verbs (#213)."""
    verb_rules = [r for r in _heuristics() if r.id == "heuristic.http-verb"]
    (ep,) = _probe("app.websocket", verb_rules)
    assert ep.rule == "heuristic.http-verb"  # still detected
    assert ep.route == "/ws"
    assert ep.http_methods == []


def test_a_verb_suffix_still_yields_its_verb():
    verb_rules = [r for r in _heuristics() if r.id == "heuristic.http-verb"]
    (ep,) = _probe("app.get", verb_rules)
    assert ep.http_methods == ["GET"]


def test_no_shipped_rule_can_emit_a_non_http_method():
    """The invariant, not just the one rule that breaks it today: every literal
    suffix any shipped `match_suffix` rule accepts either yields an HTTP method or
    yields nothing."""
    import re

    from codeanalyzer.entrypoints.matching import _HTTP_VERBS

    emitted = set()
    for rule in _heuristics():
        spec = rule.methods if isinstance(rule.methods, dict) else None
        if not spec or spec.get("from") != "match_suffix":
            continue
        # The literal alternatives in the pattern's last segment: the suffixes this
        # rule actually accepts.
        tail = rule.match.rsplit(".", 1)[-1]
        for suffix in re.findall(r"[A-Za-z_]+", tail):
            for ep in _probe(f"app.{suffix}", [rule]):
                emitted.update(ep.http_methods)
    assert emitted, "no match_suffix rule exercised -- the test is not testing anything"
    assert emitted <= {v.upper() for v in _HTTP_VERBS}, sorted(emitted)
