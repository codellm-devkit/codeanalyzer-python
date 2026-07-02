"""A small, hand-built :class:`PyApplication` that exercises every Neo4j
projection path (module, class + inheritance + methods + attributes + inner
class, callable + decorators + call sites + local vars + inner callable, module
variables, imports, and a call graph with a resolved edge and a ghost edge).

Built directly from the schema models so the Neo4j tests need neither Jedi nor a
virtualenv — they stay fast and deterministic.
"""
from __future__ import annotations

from codeanalyzer.schema import (
    PyApplication,
    PyCallable,
    PyCFG,
    PyCFGEdge,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyExternalSymbol,
    PyFunctionGraphs,
    PyGraphNode,
    PyImport,
    PyModule,
    PyParamNode,
    PyPDG,
    PyPDGEdge,
    PyProgramGraphs,
    PySDGEdge,
    PySDGEndpoint,
    PyVariableDeclaration,
)
from codeanalyzer.schema.py_schema import PyCallEdge, PyCallsite


def make_sample_app() -> PyApplication:
    announce = PyCallable(
        name="announce",
        path="src/service.py",
        signature="src.service.Service.announce",
        comments=[PyComment(content="Announce something.", is_docstring=True)],
        return_type="None",
        code="def announce(self):\n    ...",
        start_line=10,
        end_line=12,
        code_start_line=10,
        cyclomatic_complexity=1,
    )
    inner = PyClass(
        name="Inner",
        signature="src.service.Service.Inner",
        code="class Inner:\n    ...",
        start_line=14,
        end_line=15,
    )
    service = PyClass(
        name="Service",
        signature="src.service.Service",
        comments=[PyComment(content="A service.", is_docstring=True)],
        code="class Service(BaseService):\n    ...",
        base_classes=["src.service.BaseService"],
        methods={"announce": announce},
        attributes={
            "name": PyClassAttribute(name="name", type="str", start_line=8, end_line=8)
        },
        inner_classes={"Inner": inner},
        start_line=6,
        end_line=15,
    )
    base_service = PyClass(
        name="BaseService",
        signature="src.service.BaseService",
        code="class BaseService:\n    ...",
        start_line=1,
        end_line=4,
    )
    helper = PyCallable(
        name="helper",
        path="src/service.py",
        signature="src.service.helper",
        decorators=["staticmethod"],
        return_type="int",
        code="def helper():\n    Service().announce()\n    requests.get(url)",
        start_line=17,
        end_line=20,
        code_start_line=17,
        cyclomatic_complexity=2,
        call_sites=[
            PyCallsite(
                method_name="announce",
                receiver_expr="Service()",
                receiver_type="src.service.Service",
                callee_signature="src.service.Service.announce",
                start_line=18,
                start_column=4,
                end_line=18,
                end_column=22,
            )
        ],
        local_variables=[
            PyVariableDeclaration(
                name="url", type="str", initializer="'x'", scope="function",
                start_line=18, end_line=18,
            )
        ],
    )
    service_mod = PyModule(
        file_path="src/service.py",
        module_name="src.service",
        imports=[PyImport(module="os", name="path", alias="p")],
        classes={"Service": service, "BaseService": base_service},
        functions={"helper": helper},
        variables=[
            PyVariableDeclaration(
                name="CONFIG", type="dict", initializer="{}", scope="module",
                start_line=2, end_line=2,
            )
        ],
        content_hash="hash-service-v1",
        last_modified=1.0,
        file_size=100,
    )
    util_mod = PyModule(
        file_path="src/util.py",
        module_name="src.util",
        functions={
            "util_fn": PyCallable(
                name="util_fn",
                path="src/util.py",
                signature="src.util.util_fn",
                return_type="int",
                code="def util_fn():\n    return 1",
                start_line=1,
                end_line=2,
                code_start_line=1,
                cyclomatic_complexity=1,
            )
        },
        content_hash="hash-util-v1",
        last_modified=1.0,
        file_size=40,
    )

    call_graph = [
        # resolved edge — both endpoints live in the symbol table
        PyCallEdge(
            source="src.service.helper",
            target="src.service.Service.announce",
            weight=1,
            provenance=["jedi"],
        ),
        # ghost edge — target is third-party, materialized as an :PyExternal node
        PyCallEdge(
            source="src.service.helper",
            target="requests.get",
            weight=2,
            provenance=["jedi", "pycg"],
        ),
    ]

    # A miniature level-3 section exercising every CPG row family:
    # helper's CFG (entry → callsite stmt → exit), a CDG/DDG pair, its HRB
    # parameter nodes, and PARAM_IN/PARAM_OUT/SUMMARY edges into announce.
    helper_graphs = PyFunctionGraphs(
        cfg=PyCFG(
            nodes=[
                PyGraphNode(id=0, kind="entry", start_line=17, end_line=17),
                PyGraphNode(id=1, kind="statement", start_line=18, end_line=18),
                PyGraphNode(id=2, kind="exit", start_line=20, end_line=20),
            ],
            edges=[
                PyCFGEdge(source=0, target=1, kind="fallthrough"),
                PyCFGEdge(source=1, target=2, kind="return"),
                PyCFGEdge(source=1, target=2, kind="exception"),
            ],
        ),
        pdg=PyPDG(
            edges=[
                PyPDGEdge(source=0, target=1, type="CDG"),
                PyPDGEdge(source=0, target=1, type="DDG", var="url"),
            ]
        ),
        param_nodes=[
            PyParamNode(id=3, kind="formal_out", var="<return>", start_line=20, end_line=20),
            PyParamNode(id=4, kind="actual_in", var="self", call_node=1, start_line=18, end_line=18),
            PyParamNode(id=5, kind="actual_out", var="<return>", call_node=1, start_line=18, end_line=18),
        ],
    )
    announce_graphs = PyFunctionGraphs(
        cfg=PyCFG(
            nodes=[
                PyGraphNode(id=0, kind="entry", start_line=10, end_line=10),
                PyGraphNode(id=1, kind="return", start_line=11, end_line=11),
                PyGraphNode(id=2, kind="exit", start_line=12, end_line=12),
            ],
            edges=[
                PyCFGEdge(source=0, target=1, kind="fallthrough"),
                PyCFGEdge(source=1, target=2, kind="return"),
            ],
        ),
        pdg=PyPDG(edges=[PyPDGEdge(source=0, target=1, type="CDG")]),
        param_nodes=[
            PyParamNode(id=3, kind="formal_in", var="self", start_line=10, end_line=10),
            PyParamNode(id=4, kind="formal_out", var="<return>", start_line=12, end_line=12),
        ],
    )
    program_graphs = PyProgramGraphs(
        schema_version="1.0.0",
        k_limit=3,
        functions={
            "src.service.helper": helper_graphs,
            "src.service.Service.announce": announce_graphs,
        },
        sdg_edges=[
            PySDGEdge(
                source=PySDGEndpoint(signature="src.service.helper", node=1),
                target=PySDGEndpoint(signature="src.service.Service.announce", node=0),
                type="CALL",
            ),
            PySDGEdge(
                source=PySDGEndpoint(signature="src.service.helper", node=4),
                target=PySDGEndpoint(signature="src.service.Service.announce", node=3),
                type="PARAM_IN",
                var="self",
            ),
            PySDGEdge(
                source=PySDGEndpoint(signature="src.service.Service.announce", node=4),
                target=PySDGEndpoint(signature="src.service.helper", node=5),
                type="PARAM_OUT",
                var="<return>",
            ),
            PySDGEdge(
                source=PySDGEndpoint(signature="src.service.helper", node=4),
                target=PySDGEndpoint(signature="src.service.helper", node=5),
                type="SUMMARY",
            ),
        ],
    )

    return PyApplication(
        symbol_table={"src/service.py": service_mod, "src/util.py": util_mod},
        call_graph=call_graph,
        # The ghost edge's target (requests.get) is a library member, recorded as a
        # first-class external symbol so the projection emits a :PyExternal for it.
        external_symbols={"requests.get": PyExternalSymbol(name="get", module="requests")},
        program_graphs=program_graphs,
    )
