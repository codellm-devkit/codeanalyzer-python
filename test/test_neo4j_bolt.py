"""Integration test for the Neo4j bolt writer. Spins up a real Neo4j via
Testcontainers, projects the sample app to graph rows, pushes them, and asserts
the graph in the database — including the incremental behaviours (idempotent
re-push, vanished-declaration cleanup, and full-run orphan pruning).

This suite needs a container runtime reachable by Testcontainers (Docker, or
Podman via DOCKER_HOST), so it is OPT-IN: it is skipped by default (CI release
gate, and contributors without a runtime) and runs only with
``RUN_CONTAINER_TESTS=1`` set. The no-container schema conformance test always
runs (see ``test_neo4j_schema.py``).
"""
import os

import pytest

from codeanalyzer.neo4j import project
from codeanalyzer.neo4j.bolt import BoltConfig, bolt_writer

from sample_graph_app import make_sample_app

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_CONTAINER_TESTS"),
    reason="opt-in: set RUN_CONTAINER_TESTS=1 (needs Docker/Podman) to run the Neo4j bolt test",
)

# Imported lazily so a machine without the extras doesn't error at collection time.
Neo4jContainer = pytest.importorskip("testcontainers.neo4j").Neo4jContainer
neo4j = pytest.importorskip("neo4j")

_PASSWORD = "testpassword123"


@pytest.fixture(scope="module")
def neo4j_container():
    with Neo4jContainer("neo4j:5", password=_PASSWORD) as container:
        yield container


@pytest.fixture(scope="module")
def driver(neo4j_container):
    uri = neo4j_container.get_connection_url()
    drv = neo4j.GraphDatabase.driver(uri, auth=("neo4j", _PASSWORD))
    yield drv
    drv.close()


@pytest.fixture
def cfg(neo4j_container):
    return BoltConfig(
        uri=neo4j_container.get_connection_url(),
        user="neo4j",
        password=_PASSWORD,
        database=None,
    )


def _num(driver, cypher, **params):
    with driver.session() as session:
        rec = session.run(cypher, **params).single()
        if rec is None:
            return 0
        value = rec[0]
        return value if value is not None else 0


@pytest.fixture(autouse=True)
def _clean_db(driver):
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    yield


def test_full_push_materializes_the_whole_graph_and_schema(driver, cfg):
    rows = project(make_sample_app(), "sample-app")
    bolt_writer(rows, cfg, full_run=True)

    # Every projected node/edge lands.
    assert _num(driver, "MATCH (n) RETURN count(n)") == len(rows.nodes)
    assert _num(driver, "MATCH ()-[r]->() RETURN count(r)") == len(rows.edges)

    # Shared :PySymbol label spans the signature-keyed declaration kinds.
    symbol = _num(driver, "MATCH (s:PySymbol) RETURN count(s)")
    kinds = _num(
        driver,
        "MATCH (s:PySymbol) WHERE s:PyCallable OR s:PyClass OR s:PyExternal RETURN count(s)",
    )
    assert symbol > 0
    assert kinds == symbol

    # Constraints + indexes were created up front.
    assert _num(driver, "SHOW CONSTRAINTS YIELD name RETURN count(*)") >= 8
    assert _num(driver, "SHOW INDEXES YIELD name RETURN count(*)") >= 3

    # The known resolved call edge from the fixture (helper -> Service.announce).
    assert (
        _num(
            driver,
            "MATCH (:PyCallable {name:$c})-[:PY_CALLS]->(t:PyCallable {name:$n}) RETURN count(*)",
            c="helper",
            n="announce",
        )
        > 0
    )
    # The ghost edge resolved to an :PyExternal node.
    assert _num(driver, "MATCH (e:PyExternal) RETURN count(e)") >= 1


def test_re_pushing_identical_analysis_is_idempotent(driver, cfg):
    rows = project(make_sample_app(), "sample-app")
    bolt_writer(rows, cfg, full_run=True)
    bolt_writer(rows, cfg, full_run=True)
    assert _num(driver, "MATCH (n) RETURN count(n)") == len(rows.nodes)
    assert _num(driver, "MATCH ()-[r]->() RETURN count(r)") == len(rows.edges)


def test_a_full_run_prunes_a_module_whose_source_vanished(driver, cfg):
    bolt_writer(project(make_sample_app(), "sample-app"), cfg, full_run=True)

    # Drop one module from a fresh app and re-push as a full run.
    app = make_sample_app()
    victim = sorted(app.symbol_table.keys())[0]
    del app.symbol_table[victim]
    rows = project(app, "sample-app")
    bolt_writer(rows, cfg, full_run=True)

    # The victim's module-scoped nodes are gone.
    assert _num(driver, "MATCH (n {_module:$m}) RETURN count(n)", m=victim) == 0

    # The surviving module-scoped graph matches the reduced projection. (Shared
    # :PyExternal/:PyPackage/:PyDecorator nodes are MERGE-only and never pruned, so we
    # compare only _module-tagged nodes.)
    module_scoped = sum(1 for n in rows.nodes if "_module" in n.props)
    assert _num(driver, "MATCH (n) WHERE n._module IS NOT NULL RETURN count(n)") == module_scoped
