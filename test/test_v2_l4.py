import ast
import logging
import sys

import pytest

from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.utils import logger as ca_logger

COPY_CHAIN = "def f(a):\n    b = a\n    c = b\n    return c\n"


class _ListHandler(logging.Handler):
    """Capture records straight off the (non-propagating) codeanalyzer logger."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_make_alias_oracle_falls_back_when_scalpel_absent(monkeypatch):
    """`make_alias_oracle` degrades to TypeBasedAliasOracle behavior and logs
    once when Scalpel cannot be imported — regardless of whether the optional
    dependency is actually installed in the runner."""
    import codeanalyzer.dataflow.scalpel_oracle as so

    # Force `import scalpel...` to raise ImportError even if it is installed:
    # a None entry in sys.modules makes the import machinery halt. Cover the
    # top-level package and every submodule the oracle imports so a previously
    # cached submodule cannot satisfy the import.
    for mod in ("scalpel", "scalpel.cfg", "scalpel.SSA", "scalpel.SSA.const"):
        monkeypatch.setitem(sys.modules, mod, None)

    # Reset the process-wide "logged once" guard and capture on the actual
    # (propagate=False) codeanalyzer logger.
    monkeypatch.setattr(so, "_fallback_logged", False)
    handler = _ListHandler()
    ca_logger.addHandler(handler)
    old_level = ca_logger.level
    ca_logger.setLevel(logging.INFO)
    try:
        func_ast = ast.parse(COPY_CHAIN).body[0]
        oracle = so.make_alias_oracle(None, func_ast, {})
    finally:
        ca_logger.removeHandler(handler)
        ca_logger.setLevel(old_level)

    # Fell back to the type-based oracle (never a ScalpelAliasOracle).
    assert isinstance(oracle, TypeBasedAliasOracle)
    # Frozen fallback behavior: identical paths alias; two distinct bare bases
    # (non-addressable locals) do not.
    assert oracle.may_alias("a", "a") is True
    assert oracle.may_alias("a", "b") is False
    # Exactly the fallback notice was emitted.
    assert any("scalpel" in r.getMessage().lower() for r in handler.records), (
        f"expected a fallback log record, got {[r.getMessage() for r in handler.records]}"
    )


def test_scalpel_oracle_copy_chain():
    """When Scalpel is importable, the copy chain `b = a; c = b` places a, b, c
    in one copy-closure class: a/b may-alias, an unrelated name does not."""
    pytest.importorskip("scalpel")
    from codeanalyzer.dataflow.scalpel_oracle import ScalpelAliasOracle

    func_ast = ast.parse(COPY_CHAIN).body[0]
    oracle = ScalpelAliasOracle.from_function(func_ast)

    assert oracle.may_alias("a", "b") is True
    assert oracle.may_alias("a", "x") is False
