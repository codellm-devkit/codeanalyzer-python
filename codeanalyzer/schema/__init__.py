from .py_schema import (
    PyApplication,
    PyCallable,
    PyCallableParameter,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyImport,
    PyModule,
    PyVariableDeclaration,
)

__all__ = [
    "PyApplication",
    "PyImport",
    "PyComment",
    "PyModule",
    "PyClass",
    "PyVariableDeclaration",
    "PyCallable",
    "PyClassAttribute",
    "PyCallableParameter",
]

# Resolve forward references
PyCallable.update_forward_refs(PyClass=PyClass)
PyClass.update_forward_refs(PyCallable=PyCallable)
PyModule.update_forward_refs(PyCallable=PyCallable, PyClass=PyClass)
PyApplication.update_forward_refs(
    PyCallable=PyCallable,
    PyClass=PyClass,
    PyModule=PyModule
)