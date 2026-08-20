"""Receiver binding recognises staticmethod by identity, not spelling (#135).

`build_scope` decided whether a callable has a receiver with
`"staticmethod" not in {ast.unparse(d) for d in decorator_list}` — exact string
membership against written source. `@builtins.staticmethod`, or an aliased
import, failed that test, so a static method got a receiver it does not have and
`scope.self_name` was later added as a definition, producing a spurious def in
the L3/L4 dataflow.

#128 gives every decorator a Jedi-resolved `qualified_name`, so the check can be
made on identity: both `@staticmethod` and `@builtins.staticmethod` resolve to
`builtins.staticmethod`.
"""
import ast

from codeanalyzer.dataflow.access_paths import build_scope

SRC = """\
import builtins
from builtins import staticmethod as sm


class C:
    @builtins.staticmethod
    def dotted(self, x):
        return x

    @sm
    def aliased(self, x):
        return x

    @staticmethod
    def plain(self, x):
        return x

    def method(self, x):
        return x
"""

_BY_NAME = {n.name: n for n in ast.parse(SRC).body[2].body}
_RESOLVED = "builtins.staticmethod"


def test_dotted_spelling_has_no_receiver():
    scope = build_scope(_BY_NAME["dotted"], set(), decorator_names={_RESOLVED})
    assert scope.self_name is None


def test_aliased_import_has_no_receiver():
    scope = build_scope(_BY_NAME["aliased"], set(), decorator_names={_RESOLVED})
    assert scope.self_name is None


def test_bare_spelling_still_has_no_receiver():
    scope = build_scope(_BY_NAME["plain"], set(), decorator_names={_RESOLVED})
    assert scope.self_name is None


def test_an_ordinary_method_keeps_its_receiver():
    scope = build_scope(_BY_NAME["method"], set(), decorator_names=set())
    assert scope.self_name == "self"


def test_unresolved_decorators_fall_back_to_the_written_spelling():
    """Callers that cannot supply resolved names keep the old behaviour."""
    assert build_scope(_BY_NAME["plain"], set()).self_name is None
    assert build_scope(_BY_NAME["method"], set()).self_name == "self"
