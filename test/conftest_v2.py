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
            if node["kind"] == "call" and max_level >= 2:
                assert node.get("callee") is None or isinstance(node["callee"], str)
    for mod, c in _iter_callables(app):
        node_ids = set(c.get("body", {}).keys())
        for lst in ("cfg", "cdg", "ddg", "summary"):
            for e in c.get(lst, []):
                assert e["src"] in node_ids and e["dst"] in node_ids, f"dangling {lst} in {c['id']}"
