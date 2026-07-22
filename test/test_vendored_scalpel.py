"""The vendored, typed_ast-free scalpel slice: it must import and compute SSA
with no external scalpel / typed_ast / graphviz, and never pull in typeinfer."""
import sys
import importlib


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
