from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.neo4j.project import project, _global_ordinal
from codeanalyzer.schema.py_schema import (
    PyApplication, PyModule, PyClass, PyCallable, PyCallsite,
)
from codeanalyzer.dataflow.builder import build_function_pdgs, emit_l3_body
from codeanalyzer.dataflow.syntactic import SyntacticOracle
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.l2_callees import backfill_callees
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder


def test_neo4j_callable_key_equals_json_id():
    fn = PyCallable(name="f", path="m.py", signature="m.f", parameters=[])
    mod = PyModule(file_path="m.py", module_name="m", source="def f():\n    pass\n",
                   functions={"f": fn})
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "myapp")
    rows = project(app, "myapp", sig_to_id)
    keys = {n.value for n in rows.nodes}
    assert fn.id in keys          # the callable node is keyed by its can:// id
    assert app.symbol_table["m.py"].id in keys


def test_py_resolves_to_edge_targets_declared_callee_by_can_id():
    callee = PyCallable(name="g", path="m.py", signature="m.g", parameters=[])
    cs = PyCallsite(method_name="g", start_line=2, start_column=4, end_line=2,
                    end_column=7, callee_signature="m.g")
    caller = PyCallable(name="f", path="m.py", signature="m.f", parameters=[],
                        call_sites=[cs])
    mod = PyModule(file_path="m.py", module_name="m", source="def f():\n    g()\n",
                   functions={"f": caller, "g": callee})
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "myapp")
    # #120: PY_RESOLVES_TO now sources from the `body{}` call node's resolved
    # `callee`, not from `call_sites[].callee_signature`. Drive the same pipeline
    # the analyzer runs so the fixture has a body node to project from.
    populate_l1_body(app)
    backfill_callees(app, sig_to_id)
    rows = project(app, "myapp", sig_to_id)
    resolves = [e for e in rows.edges if e.type == "PY_RESOLVES_TO"]
    # the callsite must resolve to g's can:// id — edge kept, not dropped
    assert any(e.to_ref.value == sig_to_id["m.g"] for e in resolves), \
        "PY_RESOLVES_TO must target the declared callee by can:// id"


# ----------------------------------------------------------------------------------------------
# Level-3 CPG overlay: PyBodyNode merge keys are the callable can:// id + local body key,
# proving the Neo4j projection and the JSON `body`/`cfg` land on the same node identity.
# ----------------------------------------------------------------------------------------------


def _build_l3_app(tmp_path):
    """A one-function project carried through the real L1 → L3 pipeline (mirrors
    test_v2_l3) so each callable has a populated ``body``/``cfg``/``cdg``/``ddg``."""
    f = tmp_path / "m.py"
    f.write_text("def f(a):\n    b = a\n    g(b)\n    return b\n", encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "app")
    populate_l1_body(app)
    infos, _func_asts = build_function_pdgs(
        app, k=3, oracle_factory=lambda c, fast: SyntacticOracle()
    )
    emit_l3_body(app, infos, sig_to_id, graphs={"cfg", "dfg", "pdg"})
    return app, sig_to_id, mod


def test_cpg_overlay_pycfgnode_keys_equal_callable_id_plus_body_key(tmp_path):
    app, sig_to_id, mod = _build_l3_app(tmp_path)
    c = next(iter(mod.functions.values()))
    assert c.body, "precondition: L3 must populate the callable body"

    rows = project(app, "app", sig_to_id)

    # (a) two-projection agreement: the set of PyBodyNode merge values for this
    # callable == { <callable can:// id> @ <JSON local body key> }.
    expected = {_global_ordinal(c.id, k) for k in c.body}
    emitted = {
        n.value
        for n in rows.nodes
        if n.labels[0] == "PyBodyNode" and n.value.startswith(c.id + "@")
    }
    assert emitted == expected, f"PyBodyNode keys {emitted} != body ordinals {expected}"

    # (b) a PY_CFG_NEXT edge whose endpoints are two of those global ids.
    cfg_next = [e for e in rows.edges if e.type == "PY_CFG_NEXT"]
    assert any(
        e.from_ref.value in expected and e.to_ref.value in expected for e in cfg_next
    ), "expected a PY_CFG_NEXT edge over the callable's CFG-node ids"

    # (c) PY_HAS_BODY_NODE from the callable to its @entry CFG node.
    entry_id = _global_ordinal(c.id, "@entry")
    assert any(
        e.type == "PY_HAS_BODY_NODE"
        and e.from_ref.value == c.id
        and e.to_ref.value == entry_id
        for e in rows.edges
    ), "expected PY_HAS_BODY_NODE from the callable to its @entry CFG node"


# ----------------------------------------------------------------------------------------------
# L4 interprocedural overlay: param vertices ride the same PyBodyNode label (props
# var/call_node), PY_PARAM_IN/OUT connect actual↔formal across callables, PY_SUMMARY
# rides each callable's pass-throughs, and PY_DDG carries points-to provenance.
# ----------------------------------------------------------------------------------------------

# id_ passes its formal through; caller passes an argument in and the return out,
# so the SDG has PARAM_IN / PARAM_OUT / SUMMARY edges and every function has a
# def-use ddg edge (var/prov) — exactly the L4 delta this projection must carry.
_L4_FIXTURE = "def id_(x):\n    y = x\n    return y\n\n\ndef caller(a):\n    b = id_(a)\n    return b\n"


def _sig_to_id_from_tree(app) -> dict:
    """Reconstruct signature→can:// id straight off the already-stamped tree, so
    the reprojection uses the very ids emit_l4 baked into ``param_in``/``param_out``
    (app_name-independent — no re-stamp that could drift from the analyze() run)."""
    m: dict = {}
    for mod in app.symbol_table.values():
        for cl in (mod.types or {}).values():
            m[cl.signature] = cl.id
            for meth in (cl.callables or {}).values():
                m[meth.signature] = meth.id
        for fn in (mod.functions or {}).values():
            m[fn.signature] = fn.id
    return m


def _build_l4_app(tmp_path):
    """One pass-through call carried through the whole analyzer at ``-a 4`` (the
    real interprocedural path — call graph, param vertices, SDG), so ``param_in``/
    ``param_out``/``summary`` and the L4 body vertices are all populated."""
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.options import AnalysisOptions

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "m.py").write_text(_L4_FIXTURE, encoding="utf-8")
    opts = AnalysisOptions(
        input=proj,
        analysis_level=4,
        graph_field_depth=3,
        no_venv=True,
        cache_dir=tmp_path / "cache",
    )
    with Codeanalyzer(opts) as an:
        app = an.analyze().application
    by_name = {}
    for mod in app.symbol_table.values():
        for fn in mod.functions.values():
            by_name[fn.name] = fn
    return app, _sig_to_id_from_tree(app), by_name["id_"], by_name["caller"]


def test_l4_param_summary_overlay_projects_onto_pycfgnode(tmp_path):
    app, sig_to_id, id_fn, caller_fn = _build_l4_app(tmp_path)
    assert app.param_in and app.param_out, "precondition: L4 must emit param edges"

    rows = project(app, "app", sig_to_id)
    cfg_nodes = {n.value: n for n in rows.nodes if n.labels[0] == "PyBodyNode"}

    # (a) the param-vertex GLOBAL ids are among the emitted PyBodyNode merge values.
    formal_in_gid = _global_ordinal(id_fn.id, "@formal_in:0")
    formal_out_gid = _global_ordinal(id_fn.id, "@formal_out")
    assert formal_in_gid in cfg_nodes
    assert formal_out_gid in cfg_nodes

    # two-projection agreement over the L4 param vertices: every param-kind body
    # key maps to its <callable id>@<local key> and lands on that PyBodyNode.
    param_kinds = {"formal_in", "formal_out", "actual_in", "actual_out"}
    for fn in (id_fn, caller_fn):
        for k, node in fn.body.items():
            if node.kind in param_kinds:
                assert _global_ordinal(fn.id, k) in cfg_nodes, (
                    f"param vertex {fn.id}@{k} missing from projected PyBodyNodes"
                )

    # param-vertex props: var (from BodyNode.of) rides the formal_in node; the
    # actual_in node carries call_node (from BodyNode.parent, the callsite local).
    assert cfg_nodes[formal_in_gid].props["kind"] == "formal_in"
    assert cfg_nodes[formal_in_gid].props["var"] == "x"
    actual_in_nodes = [
        n for n in cfg_nodes.values() if n.props.get("kind") == "actual_in"
    ]
    assert actual_in_nodes, "expected an actual_in PyBodyNode in the caller"
    assert all("call_node" in n.props for n in actual_in_nodes)

    # (b) a PY_PARAM_IN edge connects a caller actual_in to id_'s formal_in.
    param_in = [e for e in rows.edges if e.type == "PY_PARAM_IN"]
    assert any(
        e.to_ref.value == formal_in_gid and "actual_in" in e.from_ref.value
        for e in param_in
    ), f"PY_PARAM_IN must reach {formal_in_gid} from an actual_in"
    # PY_PARAM_OUT mirrors: id_'s formal_out → a caller actual_out.
    param_out = [e for e in rows.edges if e.type == "PY_PARAM_OUT"]
    assert any(
        e.from_ref.value == formal_out_gid and "actual_out" in e.to_ref.value
        for e in param_out
    ), f"PY_PARAM_OUT must originate at {formal_out_gid} into an actual_out"

    # PY_PARAM_IN/OUT endpoints are not dangling — each is an emitted PyBodyNode.
    for e in param_in + param_out:
        assert e.from_ref.value in cfg_nodes and e.to_ref.value in cfg_nodes

    # (c) at least one PY_SUMMARY edge, both endpoints projected PyBodyNodes.
    summary = [e for e in rows.edges if e.type == "PY_SUMMARY"]
    assert summary, "expected a PY_SUMMARY edge over the caller's pass-through"
    assert all(
        e.from_ref.value in cfg_nodes and e.to_ref.value in cfg_nodes for e in summary
    )

    # (d) a PY_DDG edge carries the L4 `prov` provenance prop.
    assert any(
        e.type == "PY_DDG" and "prov" in e.props for e in rows.edges
    ), "expected a PY_DDG edge carrying a prov prop at -a 4"


# ----------------------------------------------------------------------------------------------
# Regression for #104: schema v2 dropped the per-node `code` field (source lives once on
# PyModule.source, sliced by spans), but the graph schema still declares `code` on
# :PyClass / :PyCallable and indexes it (py_code_fts). The projection must therefore
# derive `code` at projection time — module source sliced by the node's byte span —
# or code search in the graph and the SDK's `RETURN c.code` go silently null.
# ----------------------------------------------------------------------------------------------


def test_projected_code_property_is_the_module_source_span_slice():
    from sample_graph_app import make_sample_app

    app, sig_to_id = make_sample_app()
    rows = project(app, "sample-app", sig_to_id)
    by_id = {n.value: n for n in rows.nodes}

    checked = 0
    for mod in app.symbol_table.values():
        src = mod.source.encode("utf-8")
        stack = list((mod.functions or {}).values()) + list((mod.types or {}).values())
        while stack:
            decl = stack.pop()
            stack += list((decl.callables or {}).values())
            stack += list((decl.types or {}).values())
            assert decl.span is not None, f"{decl.signature}: span missing at L1+"
            lo, hi = decl.span.bytes
            expected = src[lo:hi].decode("utf-8")
            got = by_id[decl.id].props.get("code")
            assert got == expected, (
                f"{decl.signature}: projected code must be the span slice of "
                f"module.source, got {got!r}"
            )
            checked += 1
    # the sample app has functions, methods, an inner class and a subclass —
    # if we checked fewer than that, the walk itself is broken.
    assert checked >= 6


# ----------------------------------------------------------------------------------------------
# #176: the JSON `body` nodes and `parameters` carry the same id the projection merges on.
# ----------------------------------------------------------------------------------------------


def test_l3_body_node_ids_equal_projected_pybodynode_keys(tmp_path):
    app, sig_to_id, mod = _build_l3_app(tmp_path)
    c = next(iter(mod.functions.values()))
    rows = project(app, "app", sig_to_id)
    emitted = {
        n.value for n in rows.nodes
        if n.labels[0] == "PyBodyNode" and n.value.startswith(c.id + "@")
    }
    assert {n.id for n in c.body.values()} == emitted
    for k, n in c.body.items():
        assert n.id == _global_ordinal(c.id, k)
    # a call node's `id` is a body id, its `callee` a callable id — never the same string
    for n in c.body.values():
        if n.kind == "call":
            assert n.id != n.callee


def test_l4_param_vertex_ids_and_parameter_ids_agree(tmp_path):
    app, sig_to_id, id_fn, caller_fn = _build_l4_app(tmp_path)
    rows = project(app, "app", sig_to_id)
    keys = {n.value for n in rows.nodes if n.labels[0] == "PyBodyNode"}
    for fn in (id_fn, caller_fn):
        for k, n in fn.body.items():
            assert n.id == _global_ordinal(fn.id, k)
            assert n.id in keys
        # parameters[i].id names the formal_in vertex at position i, in list order
        for i, p in enumerate(fn.parameters):
            assert p.id == f"{fn.id}@formal_in:{i}"
            vertex = fn.body[f"@formal_in:{i}"]
            assert vertex.id == p.id
            assert vertex.of == p.name


# ----------------------------------------------------------------------------------------------
# #178: PY_EXTENDS resolves the written base to the declared class's can:// id, or to an
# @external ghost — never to a bare spelling nothing emits (which RowBuilder drops).
# ----------------------------------------------------------------------------------------------

_EXTENDS_FIXTURE = (
    "from lib.core import Thing\n"
    "import lib.views as views\n"
    "class Base:\n    pass\n"
    "class Child(Base):\n    pass\n"
    "class Ext(Thing):\n    pass\n"
    "class Dotted(views.View):\n    pass\n"
)


def test_py_extends_targets_declared_base_by_can_id_and_external_by_ghost(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(_EXTENDS_FIXTURE, encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    app = PyApplication(symbol_table={"m.py": mod})
    sig_to_id = assign_ids(app, "app")
    by_name = {c.name: c for c in mod.types.values()}
    assert by_name["Child"].base_classes == ["Base"], "precondition: written spelling"

    rows = project(app, "app", sig_to_id)
    ext = {(e.from_ref.value, e.to_ref.value) for e in rows.edges if e.type == "PY_EXTENDS"}
    assert (by_name["Child"].id, by_name["Base"].id) in ext
    assert (by_name["Ext"].id, "can://app/@external/lib.core/Thing") in ext
    assert (by_name["Dotted"].id, "can://app/@external/lib.views/View") in ext
    assert len(ext) == 3
    ghosts = {n.value for n in rows.nodes if "PyExternal" in n.labels}
    assert "can://app/@external/lib.core/Thing" in ghosts


def test_pyapplication_carries_the_entrypoint_report():
    # #177: a Neo4j consumer can tell "no entrypoints" from "the pass found nothing".
    from codeanalyzer.schema.py_schema import PyEntrypointReport
    import json as _json
    app = PyApplication(symbol_table={}, entrypoint_report=PyEntrypointReport(
        frameworks_detected=["flask"], rulesets=["shipped"], unresolved={"x.y": 2}, errors=[]))
    rows = project(app, "app", {})
    node = next(n for n in rows.nodes if n.labels[0] == "PyApplication")
    assert node.props["entrypoint_frameworks"] == ["flask"]
    assert _json.loads(node.props["entrypoint_report_json"]) == {
        "frameworks_detected": ["flask"], "rulesets": ["shipped"], "unresolved": {"x.y": 2}, "errors": []}
    # an empty report is still present — absence would be indistinguishable from silence
    rows = project(PyApplication(symbol_table={}), "app", {})
    node = next(n for n in rows.nodes if n.labels[0] == "PyApplication")
    assert "entrypoint_report_json" in node.props


# ----------------------------------------------------------------------------------------------
# JSON == Graph parity (#202, #203); spec docs/design/specs/2026-09-10-neo4j-json-parity.md
# ----------------------------------------------------------------------------------------------

_SPAN_KEYS = ("start_line", "end_line", "start_column", "end_column", "start_byte", "end_byte")


def _sample_rows():
    from sample_graph_app import make_sample_app

    app, sig_to_id = make_sample_app()
    return app, project(app, "sample-app", sig_to_id)


def test_module_node_carries_source_and_its_hash_matches_content_hash():
    """#202: the module's whole-file text is on the graph, intact. The hash check is
    what proves it survived serialization rather than merely being present."""
    import hashlib

    app, rows = _sample_rows()
    mods = [n for n in rows.nodes if "PyModule" in n.labels]
    assert mods, "no :PyModule rows projected"
    by_id = {m.id: m for m in app.symbol_table.values()}
    for row in mods:
        mod = by_id.get(row.value)
        if mod is None:
            continue  # a :PyModule ref minted for an unresolved import target
        assert "source" in row.props, f"{row.value} carries no source"
        assert row.props["source"] == mod.source
        assert (
            hashlib.sha256(row.props["source"].encode("utf-8")).hexdigest()
            == mod.content_hash
        )


def test_source_is_present_even_when_empty():
    """An empty file yields "", never an absent property — otherwise a consumer
    cannot tell "not carried" from "empty"."""
    mod = PyModule(file_path="e.py", module_name="e", source="")
    app = PyApplication(symbol_table={"e.py": mod})
    sig_to_id = assign_ids(app, "myapp")
    rows = project(app, "myapp", sig_to_id)
    row = next(n for n in rows.nodes if "PyModule" in n.labels)
    assert row.props["source"] == ""


def test_every_span_bearing_node_carries_all_six_span_properties():
    """#202: `_SPAN` means one thing on every label that spreads it — a consumer
    never has to know which labels are sliceable."""
    _, rows = _sample_rows()
    spanned = [n for n in rows.nodes if any(k in n.props for k in _SPAN_KEYS)]
    assert spanned, "no span-bearing rows projected"
    for row in spanned:
        # :PyAttribute is the declared exception -- PyClassAttribute carries no
        # columns in JSON, so no byte offsets are derivable. Asserted explicitly so
        # the exception cannot widen silently.
        if "PyAttribute" in row.labels:
            assert set(row.props) & set(_SPAN_KEYS) == {"start_line", "end_line"}
            continue
        missing = [k for k in _SPAN_KEYS if k not in row.props]
        assert not missing, f"{row.labels} {row.value} missing {missing}"
    # the computed-byte label must actually be exercised by the fixture
    labels = {lbl for row in spanned for lbl in row.labels}
    assert "PyVariable" in labels and "PyAttribute" in labels


def test_byte_offsets_slice_the_module_source_to_the_nodes_own_text():
    """#202: proof the offsets are real rather than declared."""
    app, rows = _sample_rows()
    source_by_module = {m.id: m.source for m in app.symbol_table.values()}
    checked = 0
    for row in rows.nodes:
        code = row.props.get("code")
        if code is None or "start_byte" not in row.props:
            continue
        source = next(
            (s for mid, s in source_by_module.items() if row.value.startswith(mid)), None
        )
        assert source is not None, f"no owning module source for {row.value}"
        sliced = source.encode("utf-8")[
            row.props["start_byte"] : row.props["end_byte"]
        ].decode("utf-8")
        assert sliced == code, f"{row.value}: byte span does not slice to its code"
        checked += 1
    assert checked, "no node carried both code and byte offsets"


def test_body_node_callee_signature_equals_the_matching_callsite():
    """#203: carried by a positional join from `call_sites`, asserted per call site
    rather than by presence — a positional join can miss silently."""
    from codeanalyzer.semantic_analysis.call_graph import _walk_module_callables

    app, rows = _sample_rows()
    by_id = {n.value: n for n in rows.nodes if "PyBodyNode" in n.labels}
    expected = 0
    for mod in app.symbol_table.values():
        for c in _walk_module_callables(mod):
            for cs in c.call_sites or []:
                if not cs.callee_signature:
                    continue
                expected += 1
                row = by_id.get(_global_ordinal(c.id, f"{cs.start_line}:{cs.start_column}"))
                assert row is not None, (
                    f"no body node at {cs.start_line}:{cs.start_column} in {c.id}"
                )
                assert row.props.get("callee_signature") == cs.callee_signature
    assert expected, "fixture has no call site with a callee_signature"


def test_variable_value_json_round_trips_to_the_json_value():
    """#203: `value` is Optional[Any] and Neo4j takes scalars or arrays of scalars,
    so it is always JSON-encoded — one shape for every value, dict included. Built
    directly rather than off the shared fixture, whose literal evaluator captures
    only `ast.Constant` and so never populates a dict `value`."""
    import json as _json
    from codeanalyzer.schema.py_schema import PyVariableDeclaration

    scalar = PyVariableDeclaration(name="PORT", initializer="8080", value=8080,
                                   start_line=1, start_column=0, end_line=1, end_column=11)
    nested = PyVariableDeclaration(name="CONF", initializer="{'a': [1, 2]}",
                                   value={"a": [1, 2]},
                                   start_line=2, start_column=0, end_line=2, end_column=20)
    mod = PyModule(file_path="m.py", module_name="m",
                   source="PORT = 8080\nCONF = {'a': [1, 2]}\n",
                   variables=[scalar, nested])
    app = PyApplication(symbol_table={"m.py": mod})
    rows = project(app, "myapp", assign_ids(app, "myapp"))
    by_name = {n.props["name"]: n for n in rows.nodes if "PyVariable" in n.labels}
    assert _json.loads(by_name["PORT"].props["value_json"]) == 8080
    assert _json.loads(by_name["CONF"].props["value_json"]) == {"a": [1, 2]}
    # initializer stays the raw source text, value_json the evaluated result
    assert by_name["CONF"].props["initializer"] == "{'a': [1, 2]}"


def test_decorator_span_rides_the_relationship_not_the_shared_node():
    """#203: :PyDecorator is merged on qualified_name and never pruned, so a
    per-application fact on it would accumulate across every project in the DB."""
    _, rows = _sample_rows()
    decorated = [e for e in rows.edges if e.type == "PY_DECORATED_BY"]
    assert decorated, "fixture projects no PY_DECORATED_BY edge"
    for e in decorated:
        missing = [k for k in _SPAN_KEYS if k not in (e.props or {})]
        assert not missing, f"PY_DECORATED_BY missing {missing}"
    for n in rows.nodes:
        if "PyDecorator" in n.labels:
            assert not any(k in n.props for k in _SPAN_KEYS), \
                "the shared :PyDecorator node must carry no per-application span"


def test_py_imports_carries_a_position_for_every_spelling():
    """#203: the edge pre-aggregates per (module, target) and emits `spellings`
    sorted, so positions key on the spelling — index alignment is already lost."""
    import json as _json

    _, rows = _sample_rows()
    imports = [e for e in rows.edges if e.type == "PY_IMPORTS"]
    assert imports, "fixture projects no PY_IMPORTS edge"
    for e in imports:
        positions = _json.loads(e.props["positions_json"])
        assert set(positions) == set(e.props["spellings"]), \
            "every spelling on the edge needs a position"
        for pos in positions.values():
            assert len(pos) == 4  # start_line, start_column, end_line, end_column
