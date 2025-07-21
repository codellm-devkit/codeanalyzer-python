from importlib.metadata import version, PackageNotFoundError
from packaging.version import parse as parse_version

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

try:
    pydantic_version = version("pydantic")
except PackageNotFoundError:
    pydantic_version = "0.0.0"  # fallback or raise if appropriate

PYDANTIC_V2 = parse_version(pydantic_version) >= parse_version("2.0.0")

if not PYDANTIC_V2:
    # Safe to pass localns
    PyCallable.update_forward_refs(PyClass=PyClass)
    PyClass.update_forward_refs(PyCallable=PyCallable)
    PyModule.update_forward_refs(PyCallable=PyCallable, PyClass=PyClass)
    PyApplication.update_forward_refs(
        PyCallable=PyCallable,
        PyClass=PyClass,
        PyModule=PyModule
    )
    
# Compatibility helpers for Pydantic v1/v2
def model_dump_json(model, **kwargs):
    """Compatibility helper for JSON serialization."""
    if PYDANTIC_V2:
        return model.model_dump_json(**kwargs)
    else:
        # Map Pydantic v2 parameters to v1 equivalents
        v1_kwargs = {}
        if 'indent' in kwargs:
            v1_kwargs['indent'] = kwargs['indent']
        if 'separators' in kwargs:
            # In v1, separators is passed to dumps_kwargs
            v1_kwargs['separators'] = kwargs['separators']
        return model.json(**v1_kwargs)

def model_validate_json(model_class, json_data):
    """Compatibility helper for JSON deserialization."""
    if PYDANTIC_V2:
        return model_class.model_validate_json(json_data)
    else:
        return model_class.parse_raw(json_data)

__all__.extend([
    "PYDANTIC_V2",
    "model_dump_json",
    "model_validate_json"
])