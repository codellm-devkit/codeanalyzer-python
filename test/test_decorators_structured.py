"""Decorators are structured records, not source strings (#128).

Covers the four things the flat-string shape could not express: the callee is
separable from its arguments, the callee resolves to a qualified name, classes
carry decorators at all, and the span locates the decorator in the source.
"""
import ast
from pathlib import Path

import pytest

jedi = pytest.importorskip("jedi")

from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder

SRC = '''\
import builtins
from dataclasses import dataclass
from functools import lru_cache


def audit(fn):
    return fn


@dataclass(frozen=True)
class Point:
    x: int

    @builtins.staticmethod
    def make(a, b):
        return a + b


@audit
@lru_cache(maxsize=128)
def risky(cmd):
    return cmd


def plain(x):
    return x
'''


@pytest.fixture
def decorated(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text(SRC)
    script = jedi.Script(path=str(f))
    tree = ast.parse(SRC)
    by_name = {n.name: n for n in tree.body if hasattr(n, "name")}
    by_name["make"] = tree.body[4].body[1]  # method inside Point
    builder = SymbolTableBuilder.__new__(SymbolTableBuilder)
    return builder, script, by_name


def _one(builder, script, node):
    return builder._decorators(node, script, SRC)


def test_class_decorator_is_captured_with_arguments(decorated):
    builder, script, nodes = decorated
    (dec,) = _one(builder, script, nodes["Point"])
    assert dec.name == "dataclass"
    assert dec.qualified_name == "dataclasses.dataclass"
    assert dec.keyword_arguments == {"frozen": "True"}
    assert dec.positional_arguments == []


def test_callee_separates_from_arguments(decorated):
    builder, script, nodes = decorated
    decs = _one(builder, script, nodes["risky"])
    assert [d.name for d in decs] == ["audit", "lru_cache"]
    # The whole point: @lru_cache and @lru_cache(maxsize=128) share a callee.
    assert decs[1].keyword_arguments == {"maxsize": "128"}
    assert decs[1].expression == "lru_cache(maxsize=128)"


def test_local_and_library_callees_both_resolve(decorated):
    builder, script, nodes = decorated
    decs = _one(builder, script, nodes["risky"])
    assert decs[0].qualified_name == "app.audit"
    assert decs[1].qualified_name == "functools.lru_cache"


def test_dotted_spelling_resolves_to_the_same_builtin(decorated):
    """@builtins.staticmethod and @staticmethod are one decorator (see #135)."""
    builder, script, nodes = decorated
    (dec,) = _one(builder, script, nodes["make"])
    assert dec.name == "builtins.staticmethod"
    assert dec.qualified_name == "builtins.staticmethod"


def test_span_locates_the_decorator_in_source(decorated):
    builder, script, nodes = decorated
    (dec,) = _one(builder, script, nodes["Point"])
    lo, hi = dec.span.bytes
    assert SRC.encode()[lo:hi].decode() == "dataclass(frozen=True)"


def test_undecorated_is_empty_not_missing(decorated):
    builder, script, nodes = decorated
    assert _one(builder, script, nodes["plain"]) == []


def test_unresolvable_decorator_yields_none_and_does_not_raise():
    src = "@thing.not_real\ndef f():\n    pass\n"
    builder = SymbolTableBuilder.__new__(SymbolTableBuilder)
    node = ast.parse(src).body[0]
    (dec,) = builder._decorators(node, None, src)
    assert dec.name == "thing.not_real"
    assert dec.qualified_name is None
