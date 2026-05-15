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
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional
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
    # Get default values for the fields in the model.
    defaults = {
        f.name: f.default
        for f in inspect.signature(cls).parameters.values()
        if f.default is not inspect.Parameter.empty
    }
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
    type: Optional[str]
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
    comments: List[PyComment] = []
    decorators: List[str] = []
    parameters: List[PyCallableParameter] = []
    return_type: Optional[str] = None
    code: str = None
    start_line: int = -1
    end_line: int = -1
    code_start_line: int = -1
    accessed_symbols: List[PySymbol] = []
    call_sites: List[PyCallsite] = []
    inner_callables: Dict[str, "PyCallable"] = {}
    inner_classes: Dict[str, "PyClass"] = {}
    local_variables: List[PyVariableDeclaration] = []
    cyclomatic_complexity: int = 0

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
    comments: List[PyComment] = []
    code: str = None
    base_classes: List[str] = []
    methods: Dict[str, PyCallable] = {}
    attributes: Dict[str, PyClassAttribute] = {}
    inner_classes: Dict[str, "PyClass"] = {}
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
    imports: List[PyImport] = []
    comments: List[PyComment] = []
    classes: Dict[str, PyClass] = {}
    functions: Dict[str, PyCallable] = {}
    variables: List[PyVariableDeclaration] = []
    # Metadata for caching
    content_hash: Optional[str] = None
    last_modified: Optional[float] = None
    file_size: Optional[int] = None


# ============================================================================
# Taint Analysis Models (Analysis Level 3)
# ============================================================================

@builder
@msgpk
class TaintSourceConfig(BaseModel):
    """Configuration entry that tells the CodeQL query generator where
    untrusted data can enter the application.

    Each entry is turned into a predicate clause inside the generated
    ``isConfiguredSource`` CodeQL predicate.
    """

    name: str
    """Unique identifier for this source entry (used for logging and deduplication)."""

    description: str
    """Human-readable explanation of what this source represents."""

    pattern: str
    """CodeQL API-graph expression that matches the source call site.

    Must be a valid CodeQL expression that evaluates to a ``DataFlow::Node``,
    e.g. ``API::builtin("input").getACall()`` or
    ``API::moduleImport("flask").getMember("request").getMember("args").asSource()``.
    All string literals inside the pattern must use double quotes (CodeQL
    does not support single-quoted strings).
    """

    source_type: str
    """Logical category label attached to every flow that originates here.

    Examples: ``"user_input"``, ``"web_request"``, ``"environment_variable"``,
    ``"file_read"``, ``"http_request"``.  The label is propagated to
    ``PyTaintSource.source_type`` in the analysis results.
    """

    enabled: bool = True
    """When ``False`` this entry is filtered out before query generation."""


@builder
@msgpk
class TaintSinkConfig(BaseModel):
    """Configuration entry that tells the CodeQL query generator where
    tainted data reaching this call site would be dangerous.

    Each entry is turned into a predicate clause inside the generated
    ``isConfiguredSink`` CodeQL predicate.
    """

    name: str
    """Unique identifier for this sink entry (used for logging and deduplication)."""

    description: str
    """Human-readable explanation of what this sink represents."""

    pattern: str
    """CodeQL API-graph expression that matches the sink call site.

    Must be a valid CodeQL expression that evaluates to a ``DataFlow::Node``,
    e.g. ``API::moduleImport("sqlite3").getMember("execute").getACall()``.
    All string literals inside the pattern must use double quotes.
    """

    sink_type: str
    """Logical category label attached to every flow that terminates here.

    Examples: ``"sql_execution"``, ``"command_execution"``, ``"code_execution"``,
    ``"file_access"``, ``"template_rendering"``.  The label is propagated to
    ``PyTaintSink.sink_type`` in the analysis results.
    """

    vulnerability_type: str
    """Human-readable vulnerability class reported in the analysis results.

    Examples: ``"SQL Injection"``, ``"Command Injection"``, ``"Path Traversal"``,
    ``"Cross-Site Scripting (XSS)"``, ``"Code Injection"``.
    """

    severity: Literal["critical", "high", "medium", "low"]
    """Risk level of a confirmed taint flow reaching this sink.

    Propagated verbatim to ``PyTaintSink.severity`` and ``PyTaintFlow.severity``.
    """

    enabled: bool = True
    """When ``False`` this entry is filtered out before query generation."""

    argument_index: Optional[int] = None
    """Zero-based index of the argument that must be tainted for the sink to fire.

    When set, the generated predicate uses
    ``pattern.getParameter(argument_index).asSink()`` so that only the
    specific argument position is tracked (e.g. index ``0`` for the query
    string in ``cursor.execute(query, params)``).  When ``None`` the call
    itself is used as the sink node.
    """


@builder
@msgpk
class TaintSanitizerConfig(BaseModel):
    """Configuration entry that tells the CodeQL query generator which
    call sites act as sanitizers, blocking taint propagation.

    Each entry is turned into a predicate clause inside the generated
    ``isConfiguredSanitizer`` CodeQL predicate.
    """

    name: str
    """Unique identifier for this sanitizer entry."""

    description: str
    """Human-readable explanation of what this sanitizer does."""

    pattern: str
    """CodeQL API-graph expression that matches the sanitizing call site.

    Must be a valid CodeQL expression that evaluates to a ``DataFlow::Node``,
    e.g. ``API::moduleImport("html").getMember("escape").getACall()``.
    All string literals inside the pattern must use double quotes.
    """

    sanitizes: List[str] = []
    """Informational list of vulnerability types this sanitizer mitigates.

    Not used by the CodeQL query generator (all enabled sanitizers block all
    flows); present for documentation and future fine-grained filtering.
    Examples: ``["xss", "template_injection"]``, ``["command_injection"]``.
    """

    enabled: bool = True
    """When ``False`` this entry is filtered out before query generation."""


@builder
@msgpk
class TaintAnalysisConfig(BaseModel):
    """Complete, self-contained configuration for a taint analysis run.

    Passed to ``TaintQueryGenerator.generate_query()`` which turns it into a
    single executable CodeQL query.  All three lists are filtered to remove
    disabled entries before query generation.
    """

    sources: List[TaintSourceConfig] = []
    """Ordered list of taint source definitions.  At least one enabled source
    is required for the analysis to produce results."""

    sinks: List[TaintSinkConfig] = []
    """Ordered list of taint sink definitions.  At least one enabled sink is
    required for the analysis to produce results."""

    sanitizers: List[TaintSanitizerConfig] = []
    """Ordered list of sanitizer definitions.  May be empty; when non-empty
    the generated query will not report flows that pass through a sanitizer."""

    max_path_length: int = 10
    """Maximum number of intermediate steps in a reported taint path.
    Longer paths are still detected but truncated in the output."""

    include_implicit_flows: bool = False
    """Whether to track implicit (control-flow) taint in addition to explicit
    (data-flow) taint.  Enabling this increases recall but also false positives."""

    confidence_threshold: Literal["high", "medium", "low"] = "medium"
    """Minimum confidence level for a flow to be included in the results.
    Currently informational; all flows are reported regardless of this value."""

    exclude_files: List[str] = []
    """Glob patterns for source files to exclude from analysis (e.g. test files)."""

    exclude_functions: List[str] = []
    """Qualified function names to exclude as sources or sinks."""

    include_safe_flows: bool = False
    """When ``True``, also report flows that pass through a sanitizer.
    Useful for auditing sanitizer coverage."""

    group_by_vulnerability: bool = True
    """When ``True``, results are grouped by vulnerability type in log output."""


@builder
@msgpk
class PyTaintSource(BaseModel):
    """Represents a taint source - where untrusted data enters the system.

    Sources are always call sites (e.g. ``input()``, ``request.args.get()``,
    ``os.getenv()``).  The ``call_site`` field captures the full call-site
    metadata from the symbol table so that downstream tasks can access
    receiver type, argument types, callee signature, and precise location
    without duplicating that information here.
    """

    source_type: str
    """Logical category of the source (e.g. ``"user_input"``, ``"web_request"``)."""

    call_site: PyCallsite
    """The call-site in the symbol table where tainted data originates."""

    description: Optional[str] = None


@builder
@msgpk
class PyTaintSink(BaseModel):
    """Represents a taint sink - where tainted data could cause harm.

    Sinks are always call sites (e.g. ``cursor.execute()``, ``os.system()``,
    ``eval()``).  The ``call_site`` field captures the full call-site metadata
    from the symbol table so that downstream tasks can access receiver type,
    argument types, callee signature, and precise location without duplicating
    that information here.
    """

    sink_type: str
    """Logical category of the sink (e.g. ``"sql_execution"``, ``"command_execution"``)."""

    call_site: PyCallsite
    """The call-site in the symbol table where tainted data is consumed."""

    severity: Literal["critical", "high", "medium", "low"] = "medium"
    description: Optional[str] = None


@builder
@msgpk
class PyTaintFlowStep(BaseModel):
    """Represents a single intermediate step in a taint flow path.

    A path is the ordered sequence of program points through which tainted
    data travels from a source to a sink.  Each step records the location
    and role of one such program point.

    Note: the current CodeQL query does not populate intermediate path steps
    (``path`` is always empty in ``PyTaintFlow``).  This model is reserved
    for future path-step extraction.
    """

    location: str
    """Absolute file path of the source file containing this step."""

    function_name: str
    """Simple name of the enclosing function or method (``"<module>"`` at
    module level)."""

    start_line: int = -1
    """1-based line number where this step begins; ``-1`` if unknown."""

    end_line: int = -1
    """1-based line number where this step ends; ``-1`` if unknown."""

    start_column: int = -1
    """0-based column offset where this step begins; ``-1`` if unknown."""

    end_column: int = -1
    """0-based column offset where this step ends; ``-1`` if unknown."""

    expression: Optional[str] = None
    """Source-code expression at this step as a string, if available."""

    step_type: Literal["source", "propagation", "sink"] = "propagation"
    """Role of this step in the flow path.

    * ``"source"`` — the first step; tainted data originates here.
    * ``"propagation"`` — an intermediate step; tainted data passes through.
    * ``"sink"`` — the last step; tainted data reaches a dangerous operation.
    """

    description: Optional[str] = None
    """Optional human-readable description of what happens at this step."""


@builder
@msgpk
class PyTaintFlow(BaseModel):
    """Represents a complete, confirmed taint flow from a source to a sink.

    A taint flow means that data originating at ``source`` (an untrusted
    input call site) can reach ``sink`` (a dangerous operation call site)
    without passing through a sanitizer, as determined by CodeQL's
    inter-procedural dataflow analysis.
    """

    flow_id: str
    """Stable identifier for this flow, derived from source and sink locations.

    Format: ``"<source_file>:<source_line>-><sink_file>:<sink_line>"``.
    Used for deduplication across incremental analysis runs.
    """

    source: PyTaintSource
    """The call site where untrusted data enters the application.

    Carries a ``PyCallsite`` that links back to the symbol table entry
    (when the symbol table was available during analysis).
    """

    sink: PyTaintSink
    """The call site where tainted data reaches a dangerous operation.

    Carries a ``PyCallsite`` that links back to the symbol table entry
    (when the symbol table was available during analysis).
    """

    path: List[PyTaintFlowStep] = []
    """Ordered list of intermediate steps between source and sink.

    Currently always empty — reserved for future path-step extraction.
    """

    vulnerability_type: str
    """Human-readable vulnerability class, e.g. ``"SQL Injection"``,
    ``"Command Injection"``, ``"Path Traversal"``.

    Derived from the matching ``TaintSinkConfig.vulnerability_type``.
    """

    severity: Literal["critical", "high", "medium", "low"] = "medium"
    """Risk level of this flow, inherited from ``TaintSinkConfig.severity``."""

    confidence: Literal["high", "medium", "low"] = "medium"
    """Confidence in the reported flow.  Currently always ``"medium"``
    (CodeQL's dataflow analysis is sound but the sink patterns may
    over-approximate)."""

    description: Optional[str] = None
    """Human-readable summary of the flow, e.g.
    ``"Tainted data from user_input flows to SQL Injection"``."""


@builder
@msgpk
class PyTaintAnalysisResult(BaseModel):
    """Container for all taint analysis results for a project.

    Source and sink information is embedded in each ``PyTaintFlow`` via
    ``flow.source`` and ``flow.sink`` (both of which carry a ``PyCallsite``),
    so there is no need for separate top-level source/sink lists.
    """

    project_path: str
    """Absolute path to the root of the analysed project."""

    flows: List[PyTaintFlow] = []
    """All confirmed taint flows detected in the project.

    Each flow represents a path from an untrusted source to a dangerous sink
    that was not blocked by a sanitizer.  An empty list means no
    vulnerabilities were detected with the current configuration.
    """

    analysis_timestamp: Optional[str] = None
    """ISO-8601 UTC timestamp of when the analysis completed, e.g.
    ``"2025-05-15T14:00:00+00:00"``."""

    codeql_database_path: Optional[str] = None
    """Absolute path to the CodeQL database used for this analysis run.
    Useful for reproducing or extending the analysis."""


# ============================================================================
# Application Model (combines all analysis levels)
# ============================================================================

@builder
@msgpk
class PyCallEdge(BaseModel):
    """Identity-only call-graph edge with weight.

    Mirrors Java's ``CallDependency``. ``source`` and ``target`` are
    ``PyCallable.signature`` strings — nodes of the graph are the existing
    ``PyCallable`` entries in the symbol table, not a separate vertex type.
    Rich per-call metadata (receiver, arguments, location, ...) lives on
    ``PyCallsite`` inside the source ``PyCallable.call_sites``.
    """

    source: str  # caller's PyCallable.signature
    target: str  # callee's PyCallable.signature
    type: Literal["CALL_DEP"] = "CALL_DEP"
    weight: int = 1
    provenance: List[Literal["jedi", "codeql", "joern"]] = []


@builder
@msgpk
class PyApplication(BaseModel):
    """Represents a Python application with multi-level analysis results.
    
    Analysis Levels:
    - Level 1: symbol_table (syntactic analysis)
    - Level 2: call_graph (control flow analysis) - TODO: implement storage
    - Level 3: taint_analysis (data flow security analysis)
    """

    symbol_table: Dict[str, PyModule]
    call_graph: List[PyCallEdge] = []
    taint_analysis: Optional[PyTaintAnalysisResult] = None
