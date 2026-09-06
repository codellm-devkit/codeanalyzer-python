"""#173: destructive Neo4j statements scope on the ``can://`` id prefix; ``_module`` retires.
No container needed — these pin the row shape the writers rely on."""
import pytest

from codeanalyzer.neo4j import NODE_LABELS, project
from codeanalyzer.neo4j.rows import (
    CAN_NODE, RowBuilder, application_prefix, descendant_prefix,
)
from codeanalyzer.neo4j.schema import INDEXES, MARKER_LABELS, SCHEMA_VERSION
from codeanalyzer.schema.assign_ids import assign_ids

from sample_graph_app import make_sample_app


def test_module_is_lifted_off_emitted_props_and_can_nodes_carry_the_marker():
    app, sig_to_id = make_sample_app()
    rows = project(app, "sample-app", sig_to_id)
    for n in rows.nodes:
        assert "_module" not in n.props, f"_module still emitted on {n.labels} {n.value}"
        if n.key_prop == "id" and n.value.startswith("can://python/"):
            assert CAN_NODE in n.labels, f"{n.value} lacks {CAN_NODE}"
        else:
            assert CAN_NODE not in n.labels, f"{n.value} wrongly carries {CAN_NODE}"
    owned = [n for n in rows.nodes if n.module is not None]
    assert owned, "module-owned rows must keep their owning module in memory"
    assert all(n.module in app.symbol_table for n in owned)
    assert not any("_module" in lbl.properties for lbl in NODE_LABELS)
    assert MARKER_LABELS == [CAN_NODE]
    assert any(f"FOR (n:{CAN_NODE}) ON (n.id)" in stmt for stmt in INDEXES)
    assert not any("_module" in stmt for stmt in INDEXES)
    assert SCHEMA_VERSION == "2.0.0"  # the v2 line is unreleased; no consumer pins the graph contract


def test_attribute_and_variable_ids_hang_under_their_owner_can_id():
    app, sig_to_id = make_sample_app()
    rows = project(app, "sample-app", sig_to_id)
    attrs = [n for n in rows.nodes if n.labels[0] == "PyAttribute"]
    variables = [n for n in rows.nodes if n.labels[0] == "PyVariable"]
    assert attrs and variables, "sample app must have an attribute and a variable"
    owners = {n.value for n in rows.nodes if n.labels[0] in ("PySymbol", "PyModule")}
    for n in attrs + variables:
        owner, _, leaf = n.value.rpartition("/")
        assert owner in owners, f"{n.value} is not under a declared owner"
        assert n.value.startswith("can://python/sample-app/")
    assert all("@" in n.value.rsplit("/", 1)[1] for n in variables)  # <name>@<line>


def test_two_application_names_never_share_an_id_keyed_node():
    app, sig_to_id = make_sample_app()
    a = {n.value for n in project(app, "app-a", assign_ids(app, "app-a")).nodes if n.key_prop == "id"}
    b = {n.value for n in project(app, "app-b", assign_ids(app, "app-b")).nodes if n.key_prop == "id"}
    # Ids minted at ANALYSIS time carry that run's app name, not the projection's:
    # artifacts (`discover_artifacts(project_dir, app_name)`) and the externals
    # `_home_external_symbols` registers. Everything `project()` mints must differ.
    shared = {
        v for v in a & b
        if not v.startswith(("pkg:", "can://artifact/")) and "/@external/" not in v
    }
    assert shared == set(), f"ids shared across applications: {sorted(shared)[:5]}"
    assert all(v.startswith("can://") for v in a | b if not v.startswith("pkg:")), \
        "every id-keyed node is a can:// node"


def test_prefix_helpers_guard_the_two_ways_this_goes_wrong():
    assert descendant_prefix("can://python/app/src/foo.py") == "can://python/app/src/foo.py/"
    assert application_prefix("app") == "can://python/app/"
    for bad in ("", None):
        with pytest.raises(ValueError):
            application_prefix(bad)


def test_row_builder_lifts_module_and_marks_only_python_can_ids():
    b = RowBuilder()
    r1 = b.node(["PyModule"], "id", "can://python/app/m.py", {"_module": "m.py", "x": 1})
    b.node(["Artifact"], "id", "can://artifact/app/Dockerfile", {"_module": "Dockerfile"})
    b.node(["PyDecorator"], "name", "functools.lru_cache", {"name": "functools.lru_cache"})
    rows = b.finish()
    by = {n.value: n for n in rows.nodes}
    assert by[r1.value].props == {"x": 1} and by[r1.value].module == "m.py"
    assert CAN_NODE in by[r1.value].labels
    assert CAN_NODE not in by["can://artifact/app/Dockerfile"].labels
    assert by["can://artifact/app/Dockerfile"].module == "Dockerfile"
    assert CAN_NODE not in by["functools.lru_cache"].labels
