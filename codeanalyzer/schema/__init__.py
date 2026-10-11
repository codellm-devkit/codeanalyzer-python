from .py_schema import (
    Analysis,
    BodyNode,
    CdgEdge,
    CfgEdge,
    DdgEdge,
    ParamEdge,
    PyApplication,
    PyCallable,
    PyCallableParameter,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyExternalSymbol,
    PyImport,
    PyModule,
    PyVariableDeclaration,
    Span,
    SummaryEdge,
)

__all__ = [
    "Analysis",
    "PyApplication",
    "PyExternalSymbol",
    "PyImport",
    "PyComment",
    "PyModule",
    "PyClass",
    "PyVariableDeclaration",
    "PyCallable",
    "PyClassAttribute",
    "PyCallableParameter",
    "Span",
    "BodyNode",
    "CfgEdge",
    "CdgEdge",
    "DdgEdge",
    "SummaryEdge",
    "ParamEdge",
]

# Pydantic v2 is the only supported major since Python 3.11 became the floor;
# kept as a constant for importers that still branch on it.
PYDANTIC_V2 = True

# Fields the analyzer keeps in memory (and in the cache) but never emits.
# `call_sites` is the internal record `body{}` call nodes are derived from (#120);
# emitting both shipped the same fact twice under two identity schemes.
INTERNAL_ONLY_FIELDS = frozenset({"call_sites"})


def strip_internal_only(data):
    """Recursively drop `INTERNAL_ONLY_FIELDS` from a dumped payload.

    Applied at emit time rather than as a field-level Pydantic `exclude`, because
    the analysis cache uses the same serializer -- excluding at the field would
    drop these from the cache as well, and the next warm-cache run would rebuild
    from a payload with no call sites.
    """
    if isinstance(data, dict):
        return {
            k: strip_internal_only(v)
            for k, v in data.items()
            if k not in INTERNAL_ONLY_FIELDS
        }
    if isinstance(data, list):
        return [strip_internal_only(v) for v in data]
    return data


# Serialization helpers, kept as the one call site for every dump/copy/load.
def model_dump_json(model, **kwargs):
    """Serialize a model to a JSON string."""
    return model.model_dump_json(**kwargs)

def model_dump(model, **kwargs):
    """Serialize a model to a dict (``mode="json"`` yields JSON-safe primitives)."""
    return model.model_dump(**kwargs)


def model_copy(model):
    """Shallow-copy a model or a dataclass leaf."""
    import copy as _copy, dataclasses as _dc
    if _dc.is_dataclass(model):  # LOW-MEM PATCH: slotted dataclass leaf (shallow copy, like model_copy)
        return _copy.copy(model)
    return model.model_copy()


def model_validate_json(model_class, json_data):
    """Deserialize a model from a JSON string."""
    return model_class.model_validate_json(json_data)

__all__.extend([
    "PYDANTIC_V2",
    "model_dump_json",
    "strip_internal_only",
    "INTERNAL_ONLY_FIELDS",
    "model_validate_json"
])