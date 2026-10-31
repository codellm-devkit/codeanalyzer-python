"""The live Bolt writer streams bounded batches instead of retaining the graph
(the behavioural suite against a real Neo4j is ``test_neo4j_bolt.py``)."""
import tracemalloc

from codeanalyzer.neo4j import bolt
from codeanalyzer.neo4j.rows import NodeRef, application_prefix


def test_batches_do_not_retain_the_graph(monkeypatch):
    sizes = []
    monkeypatch.setattr(bolt, "_upsert_nodes", lambda session, neo4j, rows: sizes.append(len(rows)))
    monkeypatch.setattr(bolt, "_upsert_edges", lambda session, neo4j, rows: sizes.append(len(rows)))
    prefix = application_prefix("a")
    tracemalloc.start()
    try:
        nodes = bolt._NodeBatches(None, None, set(), prefix, eager=False)
        edges = bolt._EdgeBatches(None, None, set(), set(), {})
        for i in range(50_000):
            nodes.node(["PyBodyNode"], "id", f"{prefix}python/m.py/f()@{i}:0", {"kind": "statement"})
            edges.edge("PY_DDG", NodeRef("PyBodyNode", "id", f"{prefix}s{i}"),
                       NodeRef("PyBodyNode", "id", f"{prefix}t{i}"), {"var": "x"}, key="x|ssa")
        nodes.finish()
        edges.finish()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert sum(sizes) == 100_000 and max(sizes) <= bolt.BATCH
    assert peak < 4 * 1024 * 1024, f"streaming retained {peak} bytes for 100k rows"


import sys
from types import SimpleNamespace
import pytest
from codeanalyzer.neo4j.rows import GraphRows, NodeRow


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("metadata", [
    {}, {"identity_version": 2, "application_name": "app"},
    {"identity_version": 2, "identity_namespace": "team"},
    {"identity_version": 2.0, "application_name": "app", "identity_namespace": "team"},
    {"identity_version": True, "application_name": "app", "identity_namespace": "team"},
    {"identity_version": 2, "application_name": "other", "identity_namespace": "team"},
    {"identity_version": 2, "application_name": "app", "identity_namespace": "other"},
])
def test_stored_identity_rejected_before_any_write(monkeypatch, streaming, metadata):
    queries = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def run(self, query, **params):
            queries.append(query)
            assert query.startswith("MATCH (a:PyApplication"), "write or unrelated read before identity guard"
            assert params == {"id": "can://team/app"}
            return SimpleNamespace(single=lambda: {"identity": metadata})
    driver = SimpleNamespace(session=lambda **kw: Session(), close=lambda: None)
    monkeypatch.setitem(sys.modules, "neo4j", SimpleNamespace(GraphDatabase=SimpleNamespace(driver=lambda *a, **kw: driver)))
    cfg = bolt.BoltConfig("bolt://unused", "user", "password")
    with pytest.raises(ValueError, match="identity"):
        if streaming:
            bolt.bolt_writer_streaming(SimpleNamespace(symbol_table={}), "app", {}, None, cfg, True, namespace="team")
        else:
            rows = GraphRows([NodeRow(["PyApplication"], "id", "can://team/app", {"name": "app", "identity_namespace": "team"})], [])
            bolt.bolt_writer(rows, cfg, True)
    assert len(queries) == 1


def test_stored_identity_allows_fresh_or_matching_anchor():
    for metadata in [None, {"identity_version": 2, "application_name": "app", "identity_namespace": "team"}]:
        result = None if metadata is None else {"identity": metadata}
        session = SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(single=lambda: result))
        bolt._validate_stored_identity(session, "app", "team")
