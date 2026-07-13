def _assert_no_nulls(obj, path="$"):
    if obj is None:
        raise AssertionError(f"unexpected null at {path} (exclude_none must drop it)")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_nulls(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_nulls(v, f"{path}[{i}]")


def _iter_callables(app):
    def walk_callable(c):
        yield c
        for ic in (c.get("inner_callables") or {}).values():
            yield from walk_callable(ic)
        for cl in (c.get("inner_classes") or {}).values():
            yield from walk_class(cl)

    def walk_class(cl):
        for m in (cl.get("methods") or {}).values():
            yield from walk_callable(m)
        for ic in (cl.get("inner_classes") or {}).values():
            yield from walk_class(ic)

    for mod in app["symbol_table"].values():
        for fn in (mod.get("functions") or {}).values():
            yield mod, fn
        for cl in (mod.get("classes") or {}).values():
            for m in walk_class(cl):
                yield mod, m


def assert_conformant(payload: dict, max_level: int) -> None:
    _assert_no_nulls(payload)
    assert payload["schema_version"] == "2.0.0"
    app = payload["application"]
    for key, mod in app["symbol_table"].items():
        assert not key.startswith("/") and ".." not in key, f"non-relative key {key}"
        assert isinstance(mod.get("source"), str) and mod["source"], f"module {key} missing source"
    for mod, c in _iter_callables(app):
        lo, hi = c["span"]["bytes"]
        text = mod["source"].encode("utf-8")[lo:hi].decode("utf-8")
        assert text.lstrip().startswith(("def ", "async def ", "@")), f"{c['id']} span mismatch"
        for node in c.get("body", {}).values():
            if node.get("kind") == "call" and "callee" in node:
                assert isinstance(node["callee"], str), "resolved callee must be a string id"
    if max_level >= 2:
        for e in app.get("call_graph", []):
            assert isinstance(e["source"], str), f"call_graph edge source must be string: {e}"
            assert isinstance(e["target"], str), f"call_graph edge target must be string: {e}"
    for mod, c in _iter_callables(app):
        node_ids = set(c.get("body", {}).keys())
        for lst in ("cfg", "cdg", "ddg", "summary"):
            for e in c.get(lst, []):
                assert e["src"] in node_ids and e["dst"] in node_ids, f"dangling {lst} in {c['id']}"
    if max_level == 3:
        # L3 is syntactic-only: every def-use edge carries exactly ssa
        # provenance (points-to provenance is the L4 delta). Dangling cfg/cdg/ddg
        # endpoints are already rejected by the check above.
        for mod, c in _iter_callables(app):
            for e in c.get("ddg", []):
                assert e.get("prov") == ["ssa"], (
                    f"L3 ddg edge must have prov ['ssa'], got {e.get('prov')} in {c['id']}"
                )
    elif max_level >= 4:
        # L4 layers an alias-derived (points-to) def-use delta additively on top
        # of the unchanged L3 ssa edges, so every ddg edge carries exactly one of
        # those two provenances — and no other.
        for mod, c in _iter_callables(app):
            for e in c.get("ddg", []):
                assert e.get("prov") in (["ssa"], ["points-to"]), (
                    f"L4 ddg edge must have prov ['ssa'] or ['points-to'], "
                    f"got {e.get('prov')} in {c['id']}"
                )

    if max_level >= 4:
        # Global body-node id space across ALL callables, computed with the
        # `_global_ordinal` formula (callable id + '@' + local key; the synthetic
        # bookends and param vertices already carry the leading '@', bare
        # statements gain it). This is the exact key under which each body node is
        # projected, so app-scope parameter-passing endpoints must resolve into it.
        def _gid(cid: str, k: str) -> str:
            return f"{cid}{k}" if k.startswith("@") else f"{cid}@{k}"

        all_global: set[str] = set()
        kind_of: dict[str, str] = {}
        for mod, c in _iter_callables(app):
            for k, node in c.get("body", {}).items():
                gid = _gid(c["id"], k)
                all_global.add(gid)
                kind_of[gid] = node.get("kind")

        # No dangling app-level parameter-passing edges: every endpoint (a GLOBAL
        # ordinal, already resolved through the endpoint functions' identity maps)
        # lands on an emitted body node.
        for e in app.get("param_in", []) + app.get("param_out", []):
            assert e["src"] in all_global, f"dangling param edge src {e['src']}"
            assert e["dst"] in all_global, f"dangling param edge dst {e['dst']}"

        # PARAM edge typing + structural arity: orientation encodes the
        # actual↔formal pairing. A param_in flows a caller actual_in into a callee
        # formal_in; a param_out flows a callee formal_out back to a caller
        # actual_out. Checking both kinds asserts each actual is paired with a
        # formal (and never actual↔actual or formal↔formal) — a structural arity
        # check across the two callables' node kinds.
        for e in app.get("param_in", []):
            assert kind_of.get(e["src"]) == "actual_in", (
                f"param_in src must be an actual_in vertex, "
                f"got {kind_of.get(e['src'])} for {e['src']}"
            )
            assert kind_of.get(e["dst"]) == "formal_in", (
                f"param_in dst must be a formal_in vertex, "
                f"got {kind_of.get(e['dst'])} for {e['dst']}"
            )
        for e in app.get("param_out", []):
            assert kind_of.get(e["src"]) == "formal_out", (
                f"param_out src must be a formal_out vertex, "
                f"got {kind_of.get(e['src'])} for {e['src']}"
            )
            assert kind_of.get(e["dst"]) == "actual_out", (
                f"param_out dst must be an actual_out vertex, "
                f"got {kind_of.get(e['dst'])} for {e['dst']}"
            )
