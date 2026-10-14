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
