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
    PyClass,
    PyClassAttribute,
    PyComment,
    PyImport,
    PyModule,
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
        # ghost edge — target is third-party, materialized as an :External node
        PyCallEdge(
            source="src.service.helper",
            target="requests.get",
            weight=2,
            provenance=["jedi", "codeql"],
        ),
    ]

    return PyApplication(
        symbol_table={"src/service.py": service_mod, "src/util.py": util_mod},
        call_graph=call_graph,
    )
