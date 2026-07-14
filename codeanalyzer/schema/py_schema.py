################################################################################
# Copyright IBM Corporation 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Python schema models module.

This module defines the data models used to represent Python code structures
for static analysis purposes.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import gzip

from pydantic import BaseModel
from typing_extensions import Literal
import msgpack


def msgpk(cls):
    """
    Decorator that adds MessagePack serialization methods to Pydantic models.

    Adds methods:
        - to_msgpack_bytes() -> bytes: Serialize to compact binary format
        - from_msgpack_bytes(data: bytes) -> cls: Deserialize from binary format
        - to_msgpack_dict() -> dict: Convert to msgpack-compatible dict
        - from_msgpack_dict(data: dict) -> cls: Create instance from msgpack dict
    """

    def _prepare_for_serialization(obj: Any) -> Any:
        """Convert objects to serialization-friendly format."""
        if isinstance(obj, Path):
            return str(obj)
        elif isinstance(obj, dict):
            return {
                _prepare_for_serialization(k): _prepare_for_serialization(v)
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [_prepare_for_serialization(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(_prepare_for_serialization(item) for item in obj)
        elif isinstance(obj, set):
            return [_prepare_for_serialization(item) for item in obj]
        elif hasattr(obj, "model_dump"):  # Pydantic model
            return _prepare_for_serialization(obj.model_dump())
        else:
            return obj

    def to_msgpack_bytes(self) -> bytes:
        """Serialize the model to compact binary format using MessagePack + gzip."""
        data = _prepare_for_serialization(self.model_dump())
        msgpack_data = msgpack.packb(data, use_bin_type=True)
        return gzip.compress(msgpack_data)

    @classmethod
    def from_msgpack_bytes(cls_obj, data: bytes):
        """Deserialize from MessagePack + gzip binary format."""
        decompressed_data = gzip.decompress(data)
        obj_dict = msgpack.unpackb(decompressed_data, raw=False)
        return cls_obj.model_validate(obj_dict)

    def to_msgpack_dict(self) -> dict:
        """Convert to msgpack-compatible dictionary format."""
        return _prepare_for_serialization(self.model_dump())

    @classmethod
    def from_msgpack_dict(cls_obj, data: dict):
        """Create instance from msgpack-compatible dictionary."""
        return cls_obj.model_validate(data)

    def get_msgpack_size(self) -> int:
        """Get the size of the msgpack serialization in bytes."""
        return len(self.to_msgpack_bytes())

    def get_compression_ratio(self) -> float:
        """Get compression ratio compared to JSON."""
        json_size = len(self.model_dump_json().encode("utf-8"))
        msgpack_gzip_size = self.get_msgpack_size()
        return msgpack_gzip_size / json_size if json_size > 0 else 1.0

    # Add methods to the class
    cls.to_msgpack_bytes = to_msgpack_bytes
    cls.from_msgpack_bytes = from_msgpack_bytes
    cls.to_msgpack_dict = to_msgpack_dict
    cls.from_msgpack_dict = from_msgpack_dict
    cls.get_msgpack_size = get_msgpack_size
    cls.get_compression_ratio = get_compression_ratio

    return cls


def builder(cls):
    """
    Decorator that generates a builder class for a Pydantic models defined below.

    It creates methods like:
        - <fieldname>(value)
        - build() to instantiate the model

    It supports nested builder patterns and is mypy-compatible.
    """
    cls_name = cls.__name__
    builder_name = f"{cls_name}Builder"

    # Get type hints and default values for the fields in the model.
    # For example, {file_path: Path, module_name: str, imports: List[PyImport], ...}
    annotations = cls.__annotations__
    # Get default values for the fields in the model. `inspect.signature` is
    # unreliable for models carrying forward references (e.g. PyCallable's
    # self-referential ``callables``): Pydantic falls back to a generic
    # ``(**data)`` signature that drops the per-field defaults, so the builder
    # would seed those fields with ``None`` and fail validation. Read the declared
    # defaults straight off the model instead. Required fields are intentionally
    # omitted (seeded ``None``) — the builder chain must set them.
    defaults = {}
    model_fields = getattr(cls, "model_fields", None)  # Pydantic v2
    if model_fields:
        for name, field in model_fields.items():
            if not field.is_required():
                defaults[name] = field.get_default(call_default_factory=True)
    else:  # Pydantic v1
        for name, field in getattr(cls, "__fields__", {}).items():
            if not field.required:
                defaults[name] = field.get_default()
    # Create a namespace for the builder class.
    namespace = {}

    # Create an __init__ method for the builder class that initializes all fields to their default values.
    def __init__(self):
        for field in annotations:
            default = defaults.get(field, None)
            setattr(self, f"_{field}", default)

    namespace["__init__"] = __init__

    # Iterate over all fields in the model and create a method for each field that sets the value and returns the builder instance.
    # This allows for method chaining. The method name will be "<fieldname>".
    for field, field_type in annotations.items():

        def make_method(f=field, t=field_type):
            def method(self, value):
                setattr(self, f"_{f}", value)
                return self

            method.__name__ = f"{f}"
            method.__annotations__ = {"value": t, "return": builder_name}
            # Check if 't' has '__name__' attribute, otherwise use a fallback
            method.__doc__ = f"Set {f} ({getattr(t, '__name__', str(t))})"
            return method

        namespace[f"{field}"] = make_method()

    # Create a build method that constructs the model instance using the values set in the builder.
    def build(self):
        return cls(**{k: getattr(self, f"_{k}") for k in annotations})

    # Add the build method to the namespace.
    namespace["build"] = build

    # Assemble the builder class dynamically
    builder_cls = type(builder_name, (object,), namespace)
    # Attach the builder class to the original class as an attribute so we can now call `MyModel.builder().name(...)`.
    setattr(cls, "builder", builder_cls)
    return cls


def byte_offsets(source: str, start_line: int, start_col: int,
                 end_line: int, end_col: int) -> Tuple[int, int]:
    """Convert (1-based line, 0-based col) ast positions to utf-8 byte offsets
    into `source`. `col` is a character offset within the line (ast semantics);
    we re-encode the line prefix to bytes so multibyte chars are handled."""
    lines = source.splitlines(keepends=True)
    def offset(line: int, col: int) -> int:
        prefix_bytes = len("".join(lines[: line - 1]).encode("utf-8"))
        col_bytes = len(lines[line - 1][:col].encode("utf-8")) if line - 1 < len(lines) else 0
        return prefix_bytes + col_bytes
    return offset(start_line, start_col), offset(end_line, end_col)


@builder
@msgpk
class Span(BaseModel):
    """Where a node lives in source. `start`/`end` are [line, col] (1-based line,
    0-based col, ast semantics); `bytes` are utf-8 offsets into module.source."""
    start: Tuple[int, int]
    end: Tuple[int, int]
    bytes: Tuple[int, int]


@builder
@msgpk
class BodyNode(BaseModel):
    """A node in a callable's `body`: an AST region (statement/call/branch/…) or
    a synthetic analysis vertex (entry/exit/formal_in/out/actual_in/out)."""
    kind: str
    span: Optional[Span] = None
    callee: Optional[str] = None   # only on `call` nodes; the sanctioned null→id slot
    of: Optional[str] = None       # param vertices: the variable/return they carry
    parent: Optional[str] = None   # actuals: owning callsite ordinal id


@builder
@msgpk
class CfgEdge(BaseModel):
    src: str; dst: str; kind: str = "fallthrough"


@builder
@msgpk
class CdgEdge(BaseModel):
    src: str; dst: str


@builder
@msgpk
class DdgEdge(BaseModel):
    src: str; dst: str; var: Optional[str] = None; prov: List[str] = []


@builder
@msgpk
class SummaryEdge(BaseModel):
    src: str; dst: str


@builder
@msgpk
class ParamEdge(BaseModel):
    src: str; dst: str


@builder
@msgpk
class PyImport(BaseModel):
    """Represents a Python import statement."""

    module: str
    name: str
    alias: Optional[str] = None
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


@builder
@msgpk
class PyComment(BaseModel):
    """Represents a Python comment."""

    content: str
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1
    is_docstring: bool = False


@builder
@msgpk
class PySymbol(BaseModel):
    """Represents a symbol used or declared in Python code."""

    name: str
    scope: Literal["local", "nonlocal", "global", "class", "module"]
    kind: Literal["variable", "parameter", "attribute", "function", "class", "module"]
    type: Optional[str] = None
    qualified_name: Optional[str] = None
    is_builtin: bool = False
    lineno: int = -1
    col_offset: int = -1


@builder
@msgpk
class PyVariableDeclaration(BaseModel):
    """Represents a Python variable declaration."""

    name: str
    # Optional WITH a default: emission drops None (exclude_none), so a
    # required-but-nullable field would make the emitted JSON fail its own
    # model's validation whenever the type is uninferred.
    type: Optional[str] = None
    initializer: Optional[str] = None
    value: Optional[Any] = None
    scope: Literal["module", "class", "function"] = "module"
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


@builder
@msgpk
class PyCallableParameter(BaseModel):
    """Represents a parameter of a Python callable (function/method)."""

    name: str
    type: Optional[str] = None
    default_value: Optional[str] = None
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


@builder
@msgpk
class PyCallsite(BaseModel):
    """Represents a Python call site (function or method invocation) with contextual metadata."""

    method_name: str
    receiver_expr: Optional[str] = None
    receiver_type: Optional[str] = None
    argument_types: List[str] = []
    return_type: Optional[str] = None
    callee_signature: Optional[str] = None
    is_constructor_call: bool = False
    start_line: int = -1
    start_column: int = -1
    end_line: int = -1
    end_column: int = -1


@builder
@msgpk
class PyCallable(BaseModel):
    """Represents a Python callable (function/method)."""

    name: str
    path: str
    signature: str  # e.g., module.<class_name>.function_name
    id: str = ""
    kind: str = "function"
    span: Optional[Span] = None
    comments: List[PyComment] = []
    decorators: List[str] = []
    parameters: List[PyCallableParameter] = []
    return_type: Optional[str] = None
    start_line: int = -1
    end_line: int = -1
    code_start_line: int = -1
    accessed_symbols: List[PySymbol] = []
    call_sites: List[PyCallsite] = []
    callables: Dict[str, "PyCallable"] = {}  # nested callables (closures)
    types: Dict[str, "PyClass"] = {}  # nested (local) classes
    local_variables: List[PyVariableDeclaration] = []
    cyclomatic_complexity: int = 0
    body: Dict[str, BodyNode] = {}
    cfg: List[CfgEdge] = []
    cdg: List[CdgEdge] = []
    ddg: List[DdgEdge] = []
    summary: List[SummaryEdge] = []

    def __hash__(self) -> int:
        """Generate a hash based on the callable's signature."""
        return hash(self.signature)
    
    


@builder
@msgpk
class PyClassAttribute(BaseModel):
    """Represents a Python class attribute."""

    name: str
    type: Optional[str] = None
    comments: List[PyComment] = []
    start_line: int = -1
    end_line: int = -1


@builder
@msgpk
class PyClass(BaseModel):
    """Represents a Python class."""

    name: str
    signature: str  # e.g., module.class_name
    id: str = ""
    kind: str = "class"
    span: Optional[Span] = None
    comments: List[PyComment] = []
    base_classes: List[str] = []
    callables: Dict[str, PyCallable] = {}  # methods, keystone containment name
    attributes: Dict[str, PyClassAttribute] = {}
    types: Dict[str, "PyClass"] = {}  # inner classes, keystone containment name
    start_line: int = -1
    end_line: int = -1

    def __hash__(self):
        """Generate a hash based on the class's signature."""
        return hash(self.signature)


@builder
@msgpk
class PyModule(BaseModel):
    """Represents a Python module."""

    file_path: str
    module_name: str
    id: str = ""
    kind: str = "module"
    source: str = ""
    imports: List[PyImport] = []
    comments: List[PyComment] = []
    types: Dict[str, PyClass] = {}  # classes, keystone containment name
    functions: Dict[str, PyCallable] = {}
    variables: List[PyVariableDeclaration] = []
    # Metadata for caching
    content_hash: Optional[str] = None
    last_modified: Optional[float] = None
    file_size: Optional[int] = None


@builder
@msgpk
class PyCallEdge(BaseModel):
    """Identity-only call-graph edge with weight (keystone shape: the list name
    IS the edge type, so there is no ``type`` field).

    ``src`` and ``dst`` are node ids — the caller's ``can://`` id and the
    callee's ``can://`` id (a symbol-table callable or an ``@external`` home).
    Rich per-call metadata (receiver, arguments, location, ...) lives on
    ``PyCallsite`` inside the source ``PyCallable.call_sites``.
    """

    src: str  # caller callable id
    dst: str  # callee callable (or external) id
    weight: int = 1
    prov: List[Literal["jedi", "pycg", "joern"]] = []


@builder
@msgpk
class PyExternalSymbol(BaseModel):
    """A call-graph target outside the analyzed project -- an imported library or
    builtin member. An edge-endpoint id home, not a tree node: keyed in
    ``PyApplication.external_symbols`` by its ``can://…/@external/…`` id."""

    id: str = ""  # can://python/<app>/@external/<module>/<name>
    kind: str = "external"
    name: str  # the member/short name, e.g. "get" for "requests.get"
    module: Optional[str] = None  # best-effort owning module, e.g. "requests"


@builder
@msgpk
class PyApplication(BaseModel):
    """Represents a Python application."""

    symbol_table: Dict[str, PyModule]
    id: str = ""
    kind: str = "application"
    call_graph: List[PyCallEdge] = []
    # Call-graph endpoints not declared in the symbol table (imported library /
    # builtin members), keyed by signature. Populated by the analyzer so every
    # backend (JSON and Neo4j) shares one authoritative external-symbol set.
    external_symbols: Dict[str, PyExternalSymbol] = {}
    # Interprocedural parameter-passing edges (formal↔actual); populated at L4.
    param_in: List[ParamEdge] = []
    param_out: List[ParamEdge] = []


@builder
@msgpk
class PyAnalyzerInfo(BaseModel):
    """Analyzer identity — correlates an ``analysis.json`` with the tool/version
    that emitted it."""
    name: str = "codeanalyzer-python"
    version: str = "unknown"


@builder
@msgpk
class Analysis(BaseModel):
    """v2 payload root: envelope + the application tree node. ``k_limit`` is an
    L3+ envelope key (None below the dataflow levels; exclude_none drops it)."""
    schema_version: str = "2.0.0"
    language: str = "python"
    max_level: int = 1
    k_limit: Optional[int] = None
    analyzer: PyAnalyzerInfo = PyAnalyzerInfo()
    application: PyApplication
