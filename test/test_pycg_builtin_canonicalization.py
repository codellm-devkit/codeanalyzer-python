"""PyCG builtin module spelling is canonicalized before edges leave the backend (#132).

PyCG spells the builtins module ``<builtin>``; Jedi spells it ``builtins``. Left
unnormalized, one builtin gets two ``@external`` ``can://`` homes and the two
backends' edges can never coalesce into ``prov: ["jedi", "pycg"]``.
"""
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.semantic_analysis.call_graph import merge_edges
from codeanalyzer.semantic_analysis.pycg.pycg_analysis import (
    _canonical_endpoint,
    _canonicalize_edges,
)


def test_builtin_module_is_rewritten():
    assert _canonical_endpoint("<builtin>.isinstance") == "builtins.isinstance"
    assert _canonical_endpoint("<builtin>.len") == "builtins.len"


def test_already_canonical_and_unrelated_names_are_untouched():
    for sig in (
        "builtins.isinstance",     # Jedi's spelling, already canonical
        "builtins.str.format",     # dotted builtin type -- module is `builtins.str`
        "requests.api.get",        # ordinary first-party signature
        "isinstance",              # no module segment at all
        "<builtin>",               # bare, no dot -> not an endpoint we rewrite
    ):
        assert _canonical_endpoint(sig) == sig


def test_colliding_spellings_coalesce_with_summed_weight():
    edges = [
        PyCallEdge(src="a.f", dst="<builtin>.len", weight=3, prov=["pycg"]),
        PyCallEdge(src="a.f", dst="builtins.len", weight=2, prov=["pycg"]),
    ]
    out = _canonicalize_edges(edges)
    assert len(out) == 1
    assert (out[0].src, out[0].dst) == ("a.f", "builtins.len")
    assert out[0].weight == 5


def test_canonicalization_lets_provenance_merge_across_backends():
    """The point of #132: without this, a builtin can never reach prov=[jedi,pycg]."""
    pycg = _canonicalize_edges(
        [PyCallEdge(src="a.f", dst="<builtin>.len", weight=1, prov=["pycg"])]
    )
    jedi = [PyCallEdge(src="a.f", dst="builtins.len", weight=1, prov=["jedi"])]
    merged = merge_edges(jedi, pycg)
    assert len(merged) == 1
    assert merged[0].prov == ["jedi", "pycg"]


def test_source_edges_are_not_mutated():
    original = PyCallEdge(src="a.f", dst="<builtin>.len", weight=1, prov=["pycg"])
    _canonicalize_edges([original])
    assert original.dst == "<builtin>.len"
