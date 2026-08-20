"""Module-scope calls to library targets are not dropped as lib->lib (#131).

`filter_external_edges` keeps an edge when either endpoint is an app symbol, but
it built `app_symbols` from callables and classes only. PyCG attributes a call in
module scope to the MODULE (`app -> functools.reduce`), and a module name was in
neither set — so every module-level call to a library target was discarded as if
both ends were third-party.

Surfaced as "decorators with arguments are dropped", but decorators are only how
it was noticed: a plain `TOTAL = functools.reduce(...)` at module scope was lost
the same way.
"""
from codeanalyzer.schema.py_schema import PyCallable, PyCallEdge, PyModule
from codeanalyzer.semantic_analysis.call_graph import filter_external_edges


def _symbol_table() -> dict:
    fn = PyCallable(name="in_a_function", path="app.py", signature="app.in_a_function")
    return {
        "app.py": PyModule(
            file_path="app.py",
            module_name="app",
            functions={"in_a_function": fn},
        )
    }


def test_module_scope_call_to_a_library_target_is_kept():
    st = _symbol_table()
    edges = [PyCallEdge(src="app", dst="functools.reduce", weight=1, prov=["pycg"])]
    assert filter_external_edges(edges, st) == edges


def test_module_scope_decorator_application_is_kept():
    """The symptom in the issue title: a decorator applied at module scope."""
    st = _symbol_table()
    edges = [
        PyCallEdge(src="app", dst="functools.lru_cache", weight=1, prov=["pycg"]),
        PyCallEdge(src="app", dst="functools.cache", weight=1, prov=["pycg"]),
    ]
    assert filter_external_edges(edges, st) == edges


def test_a_genuine_library_to_library_edge_is_still_dropped():
    """The filter's actual purpose must survive the fix."""
    st = _symbol_table()
    edges = [PyCallEdge(src="os.path.join", dst="os.sep", weight=1, prov=["pycg"])]
    assert filter_external_edges(edges, st) == []


def test_callable_endpoints_are_unaffected():
    st = _symbol_table()
    edges = [
        PyCallEdge(src="app.in_a_function", dst="functools.reduce", weight=1, prov=["jedi"]),
        PyCallEdge(src="app", dst="app.in_a_function", weight=1, prov=["pycg"]),
    ]
    assert filter_external_edges(edges, st) == edges


def test_a_module_named_like_a_library_is_still_an_app_symbol():
    """Module names come from the analyzed symbol table, so shadowing is fine."""
    st = {
        "functools.py": PyModule(
            file_path="functools.py", module_name="functools", functions={}
        )
    }
    edges = [PyCallEdge(src="functools", dst="os.getcwd", weight=1, prov=["pycg"])]
    assert filter_external_edges(edges, st) == edges
