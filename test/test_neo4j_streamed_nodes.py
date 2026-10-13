"""The streamed ``graph.cypher`` writes node rows in emission order (unsorted,
one row per emission) and still yields the graph of the in-memory merged rows."""
import io
import random

from codeanalyzer.neo4j import cypher, project
from codeanalyzer.neo4j.cypher import BATCH, _NodeStreamBuilder, write_cypher_streaming
from codeanalyzer.neo4j.rows import RowBuilder

from sample_graph_app import make_sample_app


def _capture_batches(monkeypatch):
    """Record each node batch the writer renders, in write order."""
    batches = []
    original = cypher._node_statements

    def capture(nodes):
        batches.append(list(nodes))
        return original(nodes)

    monkeypatch.setattr(cypher, "_node_statements", capture)
    return batches


def _replay(batches):
    """Neo4j's ``MERGE (n:L {key}) SET n += row.p, n:Extra``, row by row."""
    graph = {}
    for batch in batches:
        for n in batch:
            labels, props = graph.setdefault((n.labels[0], n.value), (set(), {}))
            labels.update(n.labels)
            props.update(n.props)
    return graph


def _merged(rows):
    return {(n.labels[0], n.value): (set(n.labels), n.props) for n in rows}


def test_reemitted_nodes_replay_to_merged_rows(monkeypatch):
    """Conflicting re-emissions, inside one pending batch and across groups, end
    in RowBuilder's merged node (last write wins, labels union)."""
    batches = _capture_batches(monkeypatch)
    rnd = random.Random(7)
    memory, streamed = RowBuilder(), _NodeStreamBuilder(io.StringIO())
    for i in range(4 * BATCH):
        labels = rnd.choice([["PySymbol"], ["PySymbol", "PyClass"], ["Artifact"], ["Artifact", "Extra"]])
        props = {"n": i, f"p{i % 5}": True, "_module": "m.py"}
        args = (labels, "id", f"can://a/{rnd.randrange(BATCH)}", props)
        assert streamed.node(*args) == memory.node(*args)
    streamed.finish()
    assert _replay(batches) == _merged(memory.finish().nodes)
    assert streamed._keys == {k for k in memory._keys if k[0] == "PySymbol"}


def test_no_statement_repeats_a_node(monkeypatch):
    batches = _capture_batches(monkeypatch)
    b = _NodeStreamBuilder(io.StringIO())
    for i in range(3):
        b.node(["PySymbol"], "id", "can://a/x", {"n": i})
        b.node(["PySymbol"], "id", f"can://a/y{i}", {})
    b.finish()
    for batch in batches:
        ids = [n.value for n in batch]
        assert len(ids) == len(set(ids))


def test_sample_app_snapshot_matches_in_memory_projection(monkeypatch):
    app, sig_to_id = make_sample_app()
    rows = project(app, "sample-app", sig_to_id)
    expected = cypher.render_cypher(rows, "sample-app")

    batches = _capture_batches(monkeypatch)
    out = io.StringIO()
    write_cypher_streaming(out, app, "sample-app", sig_to_id)
    actual = out.getvalue()

    assert _replay(batches) == _merged(rows.nodes)
    head = "// ── nodes ──\n"
    assert actual[:actual.index(head)] == expected[:expected.index(head)]

    def edge_rows(text):
        return sorted(line.rstrip(",") for line in text.splitlines() if line.startswith("  {f: "))

    assert edge_rows(actual) == edge_rows(expected)
