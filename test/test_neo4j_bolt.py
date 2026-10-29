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
from codeanalyzer.neo4j import bolt
from codeanalyzer.neo4j.bolt import BoltConfig, bolt_writer_streaming
from codeanalyzer.schema import PyApplication, PyCallable, PyModule
from codeanalyzer.schema.assign_ids import assign_ids

from sample_graph_app import make_sample_app


def _single_module_app(file_key: str = "appb/main.py"):
    """A minimal second application (with its own distinct module file_key) and
    its ``sig_to_id`` map — returns ``(app, sig_to_id)`` like ``make_sample_app``."""
    fn = PyCallable(
        name="main", path=file_key, signature="appb.main", return_type="None",
        code="def main():\n    ...", start_line=1, end_line=2,
        code_start_line=1, cyclomatic_complexity=1,
    )
    mod = PyModule(
        file_path=file_key, module_name="appb.main", functions={"main": fn},
        content_hash="h-b", last_modified=1.0, file_size=10,
    )
    app = PyApplication(symbol_table={file_key: mod}, call_graph=[])
    return app, assign_ids(app, "app-b")

def _push(app, app_name, sig_to_id, cfg, **kwargs):
    """The live writer ``--emit neo4j --neo4j-uri`` uses."""
    bolt_writer_streaming(app, app_name, sig_to_id, None, cfg, **kwargs)


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
    app, sig_to_id = make_sample_app()
    rows = project(app, "sample-app", sig_to_id)
    _push(app, "sample-app", sig_to_id, cfg, full_run=True)

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


def test_full_run_does_not_prune_another_applications_modules(driver, cfg):
    """Regression for #45: a full-run push for one application must not prune the
    modules of a *different* application sharing the database."""
    app_a, _ = make_sample_app()
    # Re-stamp under the pushed name: the writer refuses a module whose can:// id
    # is not under the application it is asked to purge for (#173).
    sig_to_id_a = assign_ids(app_a, "app-a")
    _push(app_a, "app-a", sig_to_id_a, cfg, full_run=True, eager=True)
    before = _num(driver, "MATCH (:PyApplication {name:'app-a'})-[:PY_HAS_MODULE]->(m) RETURN count(m)")
    assert before > 0

    # A full-run push for a different application must leave app-a untouched.
    app_b, sig_to_id_b = _single_module_app()
    _push(app_b, "app-b", sig_to_id_b, cfg, full_run=True, eager=True)

    after = _num(driver, "MATCH (:PyApplication {name:'app-a'})-[:PY_HAS_MODULE]->(m) RETURN count(m)")
    assert after == before, "full-run push for app-b pruned app-a's modules (#45)"
    assert _num(driver, "MATCH (:PyApplication {name:'app-b'})-[:PY_HAS_MODULE]->(m) RETURN count(m)") == 1


def test_re_pushing_identical_analysis_is_idempotent(driver, cfg):
    app, sig_to_id = make_sample_app()
    rows = project(app, "sample-app", sig_to_id)
    _push(app, "sample-app", sig_to_id, cfg, full_run=True)
    _push(app, "sample-app", sig_to_id, cfg, full_run=True)
    assert _num(driver, "MATCH (n) RETURN count(n)") == len(rows.nodes)
    assert _num(driver, "MATCH ()-[r]->() RETURN count(r)") == len(rows.edges)


def test_a_full_run_prunes_a_module_whose_source_vanished(driver, cfg):
    """Pruning is destructive, so it is an --eager behaviour (#171)."""
    app0, sig_to_id0 = make_sample_app()
    _push(app0, "sample-app", sig_to_id0, cfg, full_run=True, eager=True)

    # Drop one module from a fresh app and re-push as a full run.
    app, sig_to_id = make_sample_app()
    victim = sorted(app.symbol_table.keys())[0]
    del app.symbol_table[victim]
    rows = project(app, "sample-app", sig_to_id)
    _push(app, "sample-app", sig_to_id, cfg, full_run=True, eager=True)

    # The victim's subtree is gone: nothing under its can:// prefix remains.
    victim_id = app0.symbol_table[victim].id
    assert _num(driver, "MATCH (n:PyCanNode) WHERE n.id = $mid OR n.id STARTS WITH $pre RETURN count(n)",
                mid=victim_id, pre=victim_id + "/") == 0

    # The surviving graph matches the reduced projection exactly. :PyExternal is
    # MERGE-only and never pruned, so it is excluded; :PyPackage / :PyDecorator are
    # name-keyed and are not :PyCanNode at all. Since the app moved outermost, the
    # application root and the app's :Artifact / :ConfigKey rows ARE can nodes —
    # module-less, MERGE-only, and correctly still here — so the expectation is
    # every can node the reduced projection emits, not just the module-owned ones.
    expected = sum(
        1 for n in rows.nodes
        if n.value.startswith("can://") and "/@external/" not in n.value
    )
    assert _num(driver, "MATCH (n:PyCanNode) WHERE NOT n.id CONTAINS '/@external/' RETURN count(n)") == expected
    assert sum(1 for n in rows.nodes if n.module is not None) == 0, \
        "the sample app has one module; dropping it must leave no module-owned rows"


def test_a_push_never_touches_a_sibling_analyzers_nodes(driver, cfg):
    """Regression for #171: `_module` is a shared convention — codeanalyzer-java and
    codeanalyzer-typescript set it on their nodes too. A python push for a module whose
    file key collides with a sibling analyzer's must leave that analyzer's graph intact."""
    file_key = "appb/main.py"
    with driver.session() as session:
        session.run(
            "CREATE (a:TsModule {_module: $m, id: 'ts-1'})"
            "-[:TS_HAS_METHOD]->(b:TsCallable {_module: $m, id: 'ts-2'})",
            m=file_key,
        )

    app_b, sig_to_id_b = _single_module_app(file_key)
    _push(app_b, "app-b", sig_to_id_b, cfg, full_run=True, eager=True)

    assert _num(driver, "MATCH (n:TsModule) RETURN count(n)") == 1
    assert _num(driver, "MATCH (n:TsCallable) RETURN count(n)") == 1
    assert _num(driver, "MATCH (:TsModule)-[r:TS_HAS_METHOD]->(:TsCallable) RETURN count(r)") == 1


def test_a_lazy_push_deletes_nothing(driver, cfg):
    """#171: a push is additive by default. Re-pushing an app with a module and a
    callable removed must leave both in the graph — only --eager reconciles them."""
    app0, sig_to_id0 = make_sample_app()
    _push(app0, "sample-app", sig_to_id0, cfg, full_run=True)
    before = _num(driver, "MATCH (n:PyCanNode) RETURN count(n)")

    app, sig_to_id = make_sample_app()
    victim = sorted(app.symbol_table.keys())[0]
    victim_pre = app.symbol_table[victim].id + "/"
    del app.symbol_table[victim]
    _push(app, "sample-app", sig_to_id, cfg, full_run=True)

    assert _num(driver, "MATCH (n:PyCanNode) RETURN count(n)") == before
    assert _num(driver, "MATCH (n:PyCanNode) WHERE n.id STARTS WITH $pre RETURN count(n)", pre=victim_pre) > 0

    # ...and --eager still reconciles it.
    _push(app, "sample-app", sig_to_id, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (n:PyCanNode) WHERE n.id STARTS WITH $pre RETURN count(n)", pre=victim_pre) == 0


def test_eager_push_of_a_second_python_app_with_a_colliding_module_path_leaves_the_first_intact(driver, cfg):
    """#173: two python applications sharing a module path carry identical labels, so only
    the id prefix separates them. Both the per-module purge and the orphan prune must
    stay inside `can://<app>/`."""
    file_key = "appb/main.py"
    app_a, sig_a = _single_module_app(file_key)
    _push(app_a, "app-a", assign_ids(app_a, "app-a"), cfg, full_run=True, eager=True)
    a_nodes = _num(driver, "MATCH (n:PyCanNode) WHERE n.id STARTS WITH 'can://app-a/python/' RETURN count(n)")
    assert a_nodes > 0

    app_b, sig_b = _single_module_app(file_key)
    _push(app_b, "app-b", sig_b, cfg, full_run=True, eager=True)
    # ...and again, so app-b's purge runs against a graph that already holds app-b too.
    _push(app_b, "app-b", sig_b, cfg, full_run=True, eager=True)

    assert _num(driver, "MATCH (n:PyCanNode) WHERE n.id STARTS WITH 'can://app-a/python/' RETURN count(n)") == a_nodes
    assert _num(driver, "MATCH (:PyApplication {name:'app-a'})-[:PY_HAS_MODULE]->(m) RETURN count(m)") == 1
    assert _num(driver, "MATCH (:PyApplication {name:'app-b'})-[:PY_HAS_MODULE]->(m) RETURN count(m)") == 1


def test_external_nodes_sit_inside_the_application_prefix(driver, cfg):
    """#179: `:PyExternal` ids carry the application segment, so an external→app call
    edge is reachable by the same prefix predicate as every other node. The
    `@external` home is language-NEUTRAL, so the prefix is the application's, with
    no language segment — the same shape as `artifact`."""
    app, sig_to_id = make_sample_app()
    _push(app, "sample-app", sig_to_id, cfg, full_run=True, eager=True)
    total = _num(driver, "MATCH (e:PyExternal) RETURN count(e)")
    assert total >= 1
    inside = _num(driver, "MATCH (e:PyExternal:PyCanNode) WHERE e.id STARTS WITH 'can://sample-app/@external/' RETURN count(e)")
    assert inside == total


def test_eager_push_refuses_an_empty_application_id(driver, cfg):
    app, sig_to_id = make_sample_app()
    with pytest.raises(ValueError):
        _push(app, "", sig_to_id, cfg, full_run=True, eager=True)


def test_a_failed_push_publishes_no_module_hash(driver, cfg, monkeypatch):
    """Hashes mark modules unchanged, so they are written only after a push succeeds;
    otherwise a retry would skip a partly written module forever."""
    app, sig_to_id = make_sample_app()
    upsert_edges = bolt._upsert_edges
    def fail(*args):
        raise RuntimeError("connection lost")
    monkeypatch.setattr(bolt, "_upsert_edges", fail)
    with pytest.raises(RuntimeError):
        _push(app, "sample-app", sig_to_id, cfg, full_run=True)
    assert _num(driver, "MATCH (m:PyModule) WHERE m.content_hash IS NOT NULL RETURN count(m)") == 0

    monkeypatch.setattr(bolt, "_upsert_edges", upsert_edges)
    _push(app, "sample-app", sig_to_id, cfg, full_run=True)
    rows = project(app, "sample-app", sig_to_id)
    assert _num(driver, "MATCH ()-[r]->() RETURN count(r)") == len(rows.edges)
    assert _num(driver, "MATCH (m:PyModule) WHERE m.content_hash IS NOT NULL RETURN count(m)") == \
        len(app.symbol_table)


# --- artifact reconciliation (--eager full runs) -------------------------------------------------

from codeanalyzer.schema.ids import artifact_id, config_key_id, purl_pypi

_ART = "can://sample-app/artifact/"


def _rekeyed(prefix: str):
    """The sample app with every artifact re-keyed under ``prefix`` — what toggling
    --workspace-root does to an existing graph."""
    app, sig_to_id = make_sample_app()
    old_to_new = {}
    arts = {}
    for path, art in app.artifacts.items():
        new_id = artifact_id("sample-app", prefix + path)
        old_to_new[art.id] = new_id
        for ck in art.config_keys:
            new_ck = config_key_id(new_id, ck.key)
            old_to_new[ck.id] = new_ck
            ck.id = new_ck
        art.id, art.path = new_id, prefix + path
        arts[prefix + path] = art
    app.artifacts = arts
    for d in app.dependencies:
        d.declared_in = old_to_new[d.declared_in]
    for u in app.config_uses:
        u.dst = old_to_new[u.dst]
    return app, sig_to_id


def test_toggling_workspace_root_leaves_no_old_keyed_artifacts(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (a:Artifact {id: $id}) RETURN count(a)", id=_ART + "pyproject.toml") == 1

    app, s = _rekeyed("services/app/")
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)

    rooted = _ART + "services/app/"
    assert _num(driver, "MATCH (n) WHERE (n:Artifact OR n:ConfigKey) AND n.id STARTS WITH $p "
                "AND NOT n.id STARTS WITH $r RETURN count(n)", p=_ART, r=rooted) == 0
    assert _num(driver, "MATCH (:PyApplication)-[r:HAS_ARTIFACT]->() RETURN count(r)") == len(app.artifacts)
    assert _num(driver, "MATCH ()-[r:DECLARES_DEPENDENCY]->(:Package {id: $p}) RETURN count(r)",
                p=purl_pypi("acme")) == 1
    assert _num(driver, "MATCH ()-[r:PY_USES_CONFIG]->(k) WHERE k.id STARTS WITH $r RETURN count(r)",
                r=rooted) == 1
    assert _num(driver, "MATCH ()-[r:PY_USES_CONFIG]->() RETURN count(r)") == 1


def test_a_lazy_push_reconciles_no_artifacts(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True)
    app, s = _rekeyed("services/app/")
    _push(app, "sample-app", s, cfg, full_run=True)
    assert _num(driver, "MATCH (a:Artifact {id: $id}) RETURN count(a)", id=_ART + "pyproject.toml") == 1


def test_a_targeted_eager_push_reconciles_no_artifacts(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True, eager=True)
    app, s = _rekeyed("services/app/")
    _push(app, "sample-app", s, cfg, full_run=False, eager=True)
    assert _num(driver, "MATCH (a:Artifact {id: $id}) RETURN count(a)", id=_ART + "pyproject.toml") == 1


def test_a_changed_manifest_drops_its_removed_dependency_config_key_and_reads(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True, eager=True)
    gone_key = app0.artifacts["pyproject.toml"].config_keys[0].id
    assert _num(driver, "MATCH ()-[r:PY_USES_CONFIG]->(:ConfigKey {id: $k}) RETURN count(r)", k=gone_key) == 1

    app, s = make_sample_app()
    app.dependencies = []
    app.artifacts["pyproject.toml"].config_keys = []
    app.config_uses = []
    app.config_reads_unresolved = []
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)

    assert _num(driver, "MATCH ()-[r:DECLARES_DEPENDENCY|LOCKS]->(:Package {id: $p}) RETURN count(r)",
                p=purl_pypi("acme")) == 0
    assert _num(driver, "MATCH (k:ConfigKey {id: $k}) RETURN count(k)", k=gone_key) == 0
    assert _num(driver, "MATCH ()-[r:PY_USES_CONFIG]->() RETURN count(r)") == 0
    assert _num(driver, "MATCH ()-[r:PY_READS_CONFIG_UNRESOLVED]->() RETURN count(r)") == 0


def test_a_lock_edge_follows_a_manifest_change_with_an_unchanged_lockfile(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True, eager=True)

    app, s = make_sample_app()
    app.dependencies[0].locked_version = None  # the manifest no longer pins acme; poetry.lock bytes unchanged
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (:Artifact {id: $l})-[r:LOCKS]->() RETURN count(r)", l=_ART + "poetry.lock") == 0


def test_an_unchanged_eager_repush_is_idempotent(driver, cfg):
    app, s = make_sample_app()
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    shape = ("MATCH (n) OPTIONAL MATCH (n)-[r]-() "
             "RETURN count(DISTINCT n) * 1000000 + count(DISTINCT r)")
    before = _num(driver, shape)
    app, s = make_sample_app()
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    assert _num(driver, shape) == before


def test_a_sibling_analyzers_artifacts_and_edges_survive_a_python_push(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True, eager=True)
    # What codeanalyzer-java writes: a claim on the shared pyproject.toml with a maven
    # dependency, plus an artifact and config key python never emits.
    with driver.session() as session:
        session.run(
            "MATCH (a:Artifact {id: $pp}) SET a:JCanNode "
            "MERGE (j:JApplication {id: 'can://sample-app'}) MERGE (j)-[:HAS_ARTIFACT]->(a) "
            "MERGE (m:Package {id: 'pkg:maven/org.example/lib'}) MERGE (a)-[:DECLARES_DEPENDENCY {_k: 'compile'}]->(m) "
            "MERGE (o:Artifact:JCanNode {id: $pom}) MERGE (j)-[:HAS_ARTIFACT]->(o) "
            "MERGE (k:ConfigKey:JCanNode {id: $key}) MERGE (o)-[:DEFINES_CONFIG]->(k)",
            pp=_ART + "pyproject.toml", pom=_ART + "pom.xml", key=config_key_id(_ART + "pom.xml", "x"),
        ).consume()

    app, s = make_sample_app()
    del app.artifacts["pyproject.toml"]
    app.dependencies, app.config_uses = [], []
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)

    pp = _ART + "pyproject.toml"
    assert _num(driver, "MATCH (a:Artifact:JCanNode {id: $id}) WHERE NOT a:PyCanNode RETURN count(a)", id=pp) == 1
    assert _num(driver, "MATCH (:PyApplication)-[r:HAS_ARTIFACT]->(:Artifact {id: $id}) RETURN count(r)", id=pp) == 0
    assert _num(driver, "MATCH (:Artifact {id: $id})-[r:DECLARES_DEPENDENCY]->(:Package {id: 'pkg:maven/org.example/lib'}) "
                "RETURN count(r)", id=pp) == 1
    assert _num(driver, "MATCH (:Artifact {id: $id})-[:DEFINES_CONFIG]->(:ConfigKey) RETURN count(*)",
                id=_ART + "pom.xml") == 1


def test_an_artifact_an_unmarked_sibling_inventories_survives(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True, eager=True)
    with driver.session() as session:
        session.run("MATCH (a:Artifact {id: $id}) MERGE (t:TSApplication {id: 'can://sample-app'}) "
                    "MERGE (t)-[:HAS_ARTIFACT]->(a)", id=_ART + "poetry.lock").consume()
    app, s = make_sample_app()
    del app.artifacts["poetry.lock"]
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (:TSApplication)-[:HAS_ARTIFACT]->(a:Artifact {id: $id}) WHERE NOT a:PyCanNode "
                "RETURN count(a)", id=_ART + "poetry.lock") == 1


def test_a_config_key_another_analyzer_claims_survives(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True, eager=True)
    key = app0.artifacts["pyproject.toml"].config_keys[0].id
    with driver.session() as session:
        session.run("MATCH (k:ConfigKey {id: $k}) SET k:TSCanNode", k=key).consume()
    app, s = make_sample_app()
    app.artifacts["pyproject.toml"].config_keys = []
    app.config_uses = []
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (:Artifact)-[:DEFINES_CONFIG]->(k:ConfigKey:TSCanNode {id: $k}) "
                "WHERE NOT k:PyCanNode RETURN count(k)", k=key) == 1


def test_an_app_whose_name_extends_anothers_is_not_reconciled_by_it(driver, cfg):
    app_b, s_b = make_sample_app()
    s_b = assign_ids(app_b, "sample-apps")
    for path, art in app_b.artifacts.items():
        art.id = artifact_id("sample-apps", path)
    app_b.dependencies, app_b.config_uses, app_b.config_reads_unresolved = [], [], []
    for art in app_b.artifacts.values():
        art.config_keys = []
    _push(app_b, "sample-apps", s_b, cfg, full_run=True, eager=True)
    before = _num(driver, "MATCH (a:Artifact) WHERE a.id STARTS WITH 'can://sample-apps/' RETURN count(a)")
    assert before > 0

    app, s = make_sample_app()
    app.artifacts = {}
    app.dependencies, app.config_uses = [], []
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (a:Artifact:PyCanNode) WHERE a.id STARTS WITH 'can://sample-apps/' "
                "RETURN count(a)") == before


def test_pre_rekey_artifacts_are_released_too(driver, cfg):
    legacy = "can://artifact/sample-app/pyproject.toml"
    with driver.session() as session:
        session.run("MERGE (a:Artifact:PyCanNode {id: $id}) MERGE (k:ConfigKey:PyCanNode {id: $k}) "
                    "MERGE (a)-[:DEFINES_CONFIG]->(k)", id=legacy, k=config_key_id(legacy, "x")).consume()
    app, s = make_sample_app()
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (n) WHERE n.id STARTS WITH 'can://artifact/sample-app/' RETURN count(n)") == 0


def test_a_removed_dependency_and_a_resolved_import_drop_their_import_edges(driver, cfg):
    app0, s0 = make_sample_app()
    _push(app0, "sample-app", s0, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH ()-[r:PY_PROVIDES]->() RETURN count(r)") == 1
    assert _num(driver, "MATCH ()-[r:PY_UNRESOLVED_IMPORT]->() RETURN count(r)") == 1

    app, s = make_sample_app()
    app.dependencies, app.unresolved_imports = [], []
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)

    assert _num(driver, "MATCH ()-[r:PY_PROVIDES]->() RETURN count(r)") == 0
    assert _num(driver, "MATCH ()-[r:PY_UNRESOLVED_IMPORT]->() RETURN count(r)") == 0


def test_the_rows_writer_reconciles_artifacts_too(driver, cfg):
    app0, s0 = make_sample_app()
    bolt.bolt_writer(project(app0, "sample-app", s0), cfg, full_run=True, eager=True)
    app, s = _rekeyed("services/app/")
    bolt.bolt_writer(project(app, "sample-app", s), cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (n) WHERE (n:Artifact OR n:ConfigKey) AND n.id STARTS WITH $p "
                "AND NOT n.id STARTS WITH $r RETURN count(n)", p=_ART, r=_ART + "services/app/") == 0
    assert _num(driver, "MATCH ()-[r:PY_USES_CONFIG]->() RETURN count(r)") == 1


def test_a_sibling_owned_pre_rekey_artifact_survives(driver, cfg):
    legacy = "can://artifact/sample-app/pom.xml"
    with driver.session() as session:
        session.run("MERGE (a:Artifact:JCanNode {id: $id}) "
                    "MERGE (k:ConfigKey:JCanNode {id: $k}) MERGE (a)-[:DEFINES_CONFIG]->(k)",
                    id=legacy, k=config_key_id(legacy, "x")).consume()
    app, s = make_sample_app()
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (n:JCanNode) WHERE n.id STARTS WITH 'can://artifact/sample-app/' "
                "RETURN count(n)") == 2


def test_reconciliation_spans_several_batches(driver, cfg):
    n = 2500
    with driver.session() as session:
        session.run("UNWIND range(1, $n) AS i "
                    "CREATE (:Artifact:PyCanNode {id: $p + 'stale/' + toString(i) + '.txt'})",
                    n=n, p=_ART).consume()
    app, s = make_sample_app()
    _push(app, "sample-app", s, cfg, full_run=True, eager=True)
    assert _num(driver, "MATCH (a:Artifact) WHERE a.id STARTS WITH $p RETURN count(a)", p=_ART + "stale/") == 0
    assert _num(driver, "MATCH (a:Artifact) WHERE a.id STARTS WITH $p RETURN count(a)", p=_ART) == len(app.artifacts)
