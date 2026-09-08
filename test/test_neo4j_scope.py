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
        if n.key_prop == "id" and n.value.startswith("can://"):
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
        assert n.value.startswith("can://sample-app/python/")
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
        if not v.startswith("pkg:") and "/artifact/" not in v and "/@external/" not in v
    }
    assert shared == set(), f"ids shared across applications: {sorted(shared)[:5]}"
    assert all(v.startswith("can://") for v in a | b if not v.startswith("pkg:")), \
        "every id-keyed node is a can:// node"


def test_prefix_helpers_guard_the_two_ways_this_goes_wrong():
    assert descendant_prefix("can://app/python/src/foo.py") == "can://app/python/src/foo.py/"
    assert application_prefix("app") == "can://app/"
    for bad in ("", None):
        with pytest.raises(ValueError):
            application_prefix(bad)


def test_row_builder_lifts_module_and_marks_every_can_id():
    b = RowBuilder()
    r1 = b.node(["PyModule"], "id", "can://app/python/m.py", {"_module": "m.py", "x": 1})
    b.node(["Artifact"], "id", "can://app/artifact/Dockerfile", {"_module": "Dockerfile"})
    b.node(["PyDecorator"], "name", "functools.lru_cache", {"name": "functools.lru_cache"})
    rows = b.finish()
    by = {n.value: n for n in rows.nodes}
    assert by[r1.value].props == {"x": 1} and by[r1.value].module == "m.py"
    assert CAN_NODE in by[r1.value].labels
    # The artifact id used to be can://artifact/app/... — outside every
    # application prefix, so it never carried the marker and no destructive
    # statement could reach it. Nested under the app it is an ordinary can node.
    assert CAN_NODE in by["can://app/artifact/Dockerfile"].labels
    assert by["can://app/artifact/Dockerfile"].module == "Dockerfile"
    assert CAN_NODE not in by["functools.lru_cache"].labels


def test_two_applications_project_as_two_distinct_roots():
    """The multi-service failure mode: before the app-outermost grammar both
    applications MERGEd onto one :PyApplication keyed on the free-text
    ``--app-name``, with no diagnostic."""
    app, sig_to_id = make_sample_app()
    roots = [
        next(n for n in project(app, name, assign_ids(app, name)).nodes
             if n.labels[0] == "PyApplication")
        for name in ("svc-quotes", "svc-orders")
    ]
    assert [r.key_prop for r in roots] == ["id", "id"], \
        "the root must merge on its id, not on a display name"
    assert [r.value for r in roots] == ["can://svc-quotes", "can://svc-orders"]
    assert [r.props["name"] for r in roots] == ["svc-quotes", "svc-orders"], \
        "name survives as a display property"
    assert roots[0].value != roots[1].value, "two services must not share a root node"


def test_every_projected_id_sits_under_the_application_prefix():
    """The reason for the change: ``can://<app>`` is a prefix of everything the
    application emits, so the prefix-scoped delete reaches all of it."""
    app, sig_to_id = make_sample_app()
    rows = project(app, "sample-app", sig_to_id)
    root = application_prefix("sample-app").rstrip("/")
    for n in rows.nodes:
        if not n.value.startswith("can://"):
            continue  # :PyPackage / :PyDecorator are name-keyed by design
        assert n.value == root or n.value.startswith(root + "/"), \
            f"{n.value} escapes the application prefix, so a scoped delete would miss it"


def test_no_id_carries_the_old_language_first_shape():
    app, sig_to_id = make_sample_app()
    rows = project(app, "sample-app", sig_to_id)
    for n in rows.nodes:
        assert not n.value.startswith("can://python/sample-app"), \
            f"old-shape id survived: {n.value}"
        assert not n.value.startswith("can://artifact/"), \
            f"old-shape artifact id survived: {n.value}"
