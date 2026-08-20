"""`async def` is recorded as a modifier, not a kind (#130).

`kind` is the specific callable kind — function/method/constructor/lambda — and
async is orthogonal to every one of them: encoding it there would need
`async_function`, `async_method`, `async_generator`… combinatorially. The
keystone reserves `modifiers: string[]` for exactly this and says `kind` is not
a place for `is_*` flags, so `async` goes in `modifiers` and `kind` is untouched.
"""
from pathlib import Path

import pytest

jedi = pytest.importorskip("jedi")

from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder

SRC = '''\
async def top_level():
    return 1


def plain():
    return 2


async def agen():
    yield 3


def gen():
    yield 4


class C:
    async def method(self):
        return 5

    def sync_method(self):
        return 6


def outer():
    async def nested():
        return 7
    return nested
'''


@pytest.fixture
def module(tmp_path: Path):
    f = tmp_path / "m.py"
    f.write_text(SRC)
    return SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)


def _fn(module, name):
    if name in module.functions:
        return module.functions[name]
    for cls in module.types.values():
        if name in cls.callables:
            return cls.callables[name]
    for fn in module.functions.values():
        if name in (fn.callables or {}):
            return fn.callables[name]
    raise KeyError(name)


@pytest.mark.parametrize("name", ["top_level", "agen", "method", "nested"])
def test_async_callables_carry_the_modifier(module, name):
    assert "async" in _fn(module, name).modifiers


@pytest.mark.parametrize("name", ["plain", "gen", "sync_method", "outer"])
def test_sync_callables_do_not(module, name):
    assert "async" not in _fn(module, name).modifiers


def test_kind_is_untouched(module):
    """async is a modifier; the kind discriminant must not gain a variant."""
    assert _fn(module, "top_level").kind == _fn(module, "plain").kind


def test_an_async_generator_is_not_confused_with_a_plain_one(module):
    assert "async" in _fn(module, "agen").modifiers
    assert "async" not in _fn(module, "gen").modifiers
