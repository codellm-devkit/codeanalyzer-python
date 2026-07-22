"""The vendored, typed_ast-free scalpel slice: it must import and compute SSA
with no external scalpel / typed_ast / graphviz, and never pull in typeinfer."""
import ast
import sys

import pytest


def test_vendored_scalpel_imports_and_computes_ssa_typed_ast_free():
    # No external scalpel shadowing the vendored copy.
    assert "scalpel" not in sys.modules or not sys.modules["scalpel"].__file__.endswith(
        "site-packages/scalpel/__init__.py"
    ), "external python-scalpel is installed; the test must exercise the vendored copy"

    from codeanalyzer.dataflow.scalpel.cfg import CFGBuilder
    from codeanalyzer.dataflow.scalpel.SSA.const import SSA

    src = "def f(a):\n    b = a\n    c = b\n    return c\n"
    module_cfg = CFGBuilder().build_from_src("m", src)
    func_cfg = list(module_cfg.functioncfgs.values())[0]
    ssa_results, const_dict = SSA().compute_SSA(func_cfg)
    # The copy chain + return pseudo-name scalpel is known to produce.
    assert ("b", 0) in const_dict and ("c", 0) in const_dict and ("<ret>", 0) in const_dict

    # The whole point: no typed_ast, and typeinfer was never vendored/loaded.
    assert "typed_ast" not in sys.modules
    loaded = [m for m in sys.modules if m.startswith("codeanalyzer.dataflow.scalpel")]
    assert not any("typeinfer" in m for m in loaded), f"typeinfer leaked: {loaded}"


def test_make_alias_oracle_defaults_to_scalpel_without_typed_ast():
    import sys
    assert "typed_ast" not in sys.modules
    from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle, ScalpelAliasOracle

    src = "def f(a):\n    b = a\n    c = b\n    return c\n"
    func_ast = ast.parse(src).body[0]
    oracle = make_alias_oracle(pycallable=None, func_ast=func_ast, base_types={})
    # The whole point: scalpel is the default, not the type-based fallback.
    assert isinstance(oracle, ScalpelAliasOracle), type(oracle).__name__
    # It answers queries (copies alias; unrelated locals do not).
    assert oracle.may_alias("b", "c") is True


def test_make_alias_oracle_is_deterministic():
    import ast as _ast
    from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle
    src = "def f(a):\n    b = a\n    c = b\n    return c\n"
    fa = _ast.parse(src).body[0]
    o1 = make_alias_oracle(None, _ast.parse(src).body[0], {})
    o2 = make_alias_oracle(None, _ast.parse(src).body[0], {})
    pairs = [("a", "b"), ("b", "c"), ("a", "c"), ("b", "b")]
    assert [o1.may_alias(x, y) for x, y in pairs] == [o2.may_alias(x, y) for x, y in pairs]


def _pip_scalpel_available():
    try:
        import importlib.util
        # the EXTERNAL package, not our vendored copy
        return importlib.util.find_spec("scalpel") is not None and \
            not importlib.util.find_spec("scalpel").origin.endswith(
                "codeanalyzer/dataflow/scalpel/__init__.py")
    except Exception:
        return False


@pytest.mark.skipif(not _pip_scalpel_available(),
                    reason="upstream python-scalpel not installed (3.12+ can't build typed_ast)")
def test_vendored_ssa_matches_upstream():
    """On a Python where pip python-scalpel installs (<=3.11), the vendored copy
    must produce identical SSA const_dict keys as upstream on the same source."""
    import scalpel.SSA.const as up_ssa
    import scalpel.cfg as up_cfg
    from codeanalyzer.dataflow.scalpel.SSA.const import SSA as VSSA
    from codeanalyzer.dataflow.scalpel.cfg import CFGBuilder as VCFG

    src = "def f(a):\n    b = a\n    if a:\n        b = 2\n    return b\n"
    up_c = up_cfg.CFGBuilder().build_from_src("m", src)
    v_c = VCFG().build_from_src("m", src)
    up_fn = list(up_c.functioncfgs.values())[0]
    v_fn = list(v_c.functioncfgs.values())[0]
    _, up_const = up_ssa.SSA().compute_SSA(up_fn)
    _, v_const = VSSA().compute_SSA(v_fn)
    assert sorted(map(str, up_const)) == sorted(map(str, v_const))
