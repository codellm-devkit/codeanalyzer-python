"""Re-identify call-graph edge endpoints onto canonical can:// ids so the JSON
call_graph agrees with the Neo4j PY_CALLS projection. Declared endpoints map
through sig_to_id; external/library endpoints keep their dotted signature
(they have no can:// id)."""
from __future__ import annotations
from codeanalyzer.schema.py_schema import PyApplication


def reidentify_call_graph(app: PyApplication, sig_to_id: dict) -> None:
    for edge in app.call_graph or []:
        edge.src = sig_to_id.get(edge.src, edge.src)
        edge.dst = sig_to_id.get(edge.dst, edge.dst)
