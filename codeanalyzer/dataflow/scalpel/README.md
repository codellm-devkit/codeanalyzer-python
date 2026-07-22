# Vendored Scalpel (typed_ast-free slice)

Vendored from **[SMAT-Lab/Scalpel](https://github.com/SMAT-Lab/Scalpel)**,
package `python-scalpel==1.0b0`, licensed **Apache-2.0** (see `LICENSE`).

## Why vendored

`python-scalpel` hard-depends on `typed_ast`, whose last release (1.5.5) has no
wheel for Python 3.12+ and fails to build from source on modern compilers — so
`pip install python-scalpel` fails on 3.12/3.13/3.14. `typed_ast` is imported by
exactly one scalpel module, `typeinfer/analysers.py`, which codeanalyzer does not
use. Vendoring the small slice the L4 may-alias oracle needs removes the
`typed_ast` dependency and makes scalpel the default oracle on every supported
Python.

## What is vendored

Exactly the 9-module closure that `scalpel.SSA.const` + `scalpel.cfg` load
(verified via `sys.modules`; provably free of `typeinfer`/`typed_ast`):

    __init__.py
    SSA/__init__.py, SSA/const.py
    cfg/__init__.py, cfg/builder.py, cfg/model.py
    core/__init__.py, core/func_call_visitor.py, core/vars_visitor.py

Copied verbatim except **one patch**: `cfg/model.py`'s top-level
`import graphviz as gv` is guarded (`try/except ImportError: gv = None`) so the
module imports without the `graphviz` package — the graphviz-using
`build_visual()` methods are unused here.

Runtime deps of this slice: `astor`, `networkx` (both core dependencies of
codeanalyzer). `typed_ast` and `graphviz` are NOT required.

To refresh: re-run the vendoring in `docs/superpowers/plans/2026-07-22-vendored-scalpel-default-oracle.md` Task 1.
