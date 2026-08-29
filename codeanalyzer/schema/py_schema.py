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
from pydantic import BaseModel
from typing_extensions import Literal


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
class Span(BaseModel):
    """Where a node lives in source. `start`/`end` are [line, col] (1-based line,
    0-based col, ast semantics); `bytes` are utf-8 offsets into module.source."""
    start: Tuple[int, int]
    end: Tuple[int, int]
    bytes: Tuple[int, int]


@builder
class BodyNode(BaseModel):
    """A node in a callable's `body`: an AST region (statement/call/branch/…) or
    a synthetic analysis vertex (entry/exit/formal_in/out/actual_in/out)."""
    kind: str
    span: Optional[Span] = None
    callee: Optional[str] = None   # only on `call` nodes; the sanctioned null→id slot
    of: Optional[str] = None       # param vertices: the variable/return they carry
    parent: Optional[str] = None   # actuals: owning callsite ordinal id
    # Call-site detail (#120). Previously reachable only through the parallel
    # `PyCallable.call_sites` list, which emitted the same fact a second time under
    # an unrelated id scheme. `method_name` and `is_constructor_call` are carried
    # rather than derived from `callee`: measured across `requests` and `flask`,
    # 20-28% of call sites never resolve a callee, so deriving them would lose them
    # on one call in four.
    method_name: Optional[str] = None
    receiver_expr: Optional[str] = None
    receiver_type: Optional[str] = None
    return_type: Optional[str] = None
    is_constructor_call: Optional[bool] = None
    arguments: List["PyCallArgument"] = []


@builder
class CfgEdge(BaseModel):
    src: str; dst: str; kind: str = "fallthrough"


@builder
class CdgEdge(BaseModel):
    src: str; dst: str


@builder
class DdgEdge(BaseModel):
    src: str; dst: str; var: Optional[str] = None; prov: List[str] = []


@builder
class SummaryEdge(BaseModel):
    src: str; dst: str


@builder
class ParamEdge(BaseModel):
    src: str; dst: str


@builder
class PyImport(BaseModel):
    """Represents a Python import statement."""

    module: str
    name: str
    alias: Optional[str] = None
    resolved_module: Optional[str] = None
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


@builder
class PyComment(BaseModel):
    """Represents a Python comment."""

    content: str
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1
    is_docstring: bool = False


@builder
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
class PyDecorator(BaseModel):
    """One decorator application, structured rather than a source string (#128).

    ``name`` is the spelling as written (``lru_cache``, ``builtins.staticmethod``);
    ``qualified_name`` is Jedi's resolution of it (``functools.lru_cache``) and is
    absent when it cannot be resolved. ``expression`` keeps the full unparsed source
    so nothing is lost for decorators too complex to decompose.
    """

    name: str
    qualified_name: Optional[str] = None
    positional_arguments: List[str] = []
    keyword_arguments: Dict[str, str] = {}
    expression: str = ""
    span: Optional[Span] = None


@builder
class PyEntrypoint(BaseModel):
    """One way a callable or class is invoked from outside the application (#27).

    A node may hold several: two ``@app.route`` decorators, or a function that
    is both a Celery task and a CLI command. ``confidence`` lets a consumer
    threshold on evidence quality rather than inheriting this analyzer's
    judgement.
    """

    framework: str
    confidence: str = "certain"   # "declared" | "certain" | "heuristic"
    rule: str = ""                # rules.yml `id:`, or an engine name
    ruleset: str = "shipped"      # "shipped" | "user:<path>"
    evidence: Optional[str] = None
    route: Optional[str] = None
    http_methods: List[str] = []
    via: Optional[str] = None     # can:// id of the routed node dispatching here


@builder
class PyEntrypointReport(BaseModel):
    """Coverage and failure record for the entrypoint pass (#27).

    The pass under-approximates by design, so silence is its failure mode.
    This is what makes a gap visible instead of indistinguishable from
    "this project has no entrypoints".
    """

    frameworks_detected: List[str] = []
    rulesets: List[str] = []
    unresolved: Dict[str, int] = {}
    errors: List[str] = []


@builder
class PyCallableParameter(BaseModel):
    """Represents a parameter of a Python callable (function/method)."""

    name: str
    type: Optional[str] = None
    default_value: Optional[str] = None
    decorators: List[PyDecorator] = []
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


@builder
class PyCallArgument(BaseModel):
    """One call-site argument: AST category + inferred type, kept separate.

    The legacy ``PyCallsite.argument_types`` mixed these two vocabularies
    in one list; this model is the disambiguated replacement (#86).
    """

    ast_kind: str
    inferred_type: Optional[str] = None


# BodyNode.arguments forward-references PyCallArgument (defined later);
# pydantic v1 resolves string annotations only when told to, while v2
# rebuilds automatically (and its update_forward_refs shim rejects localns).
if not hasattr(BodyNode, "model_rebuild"):  # pydantic v1
    BodyNode.update_forward_refs(PyCallArgument=PyCallArgument)


@builder
class PyCallsite(BaseModel):
    """Represents a Python call site (function or method invocation) with contextual metadata."""

    method_name: str
    receiver_expr: Optional[str] = None
    receiver_type: Optional[str] = None
    argument_types: List[str] = []
    arguments: List[PyCallArgument] = []
    return_type: Optional[str] = None
    callee_signature: Optional[str] = None
    is_constructor_call: bool = False
    start_line: int = -1
    start_column: int = -1
    end_line: int = -1
    end_column: int = -1


@builder
class PyCallable(BaseModel):
    """Represents a Python callable (function/method)."""

    name: str
    path: str
    signature: str  # e.g., module.<class_name>.function_name
    id: str = ""
    kind: str = "function"
    span: Optional[Span] = None
    comments: List[PyComment] = []
    decorators: List[PyDecorator] = []
    # Language-level modifiers on the declaration itself (#130). `async` is the
    # only one Python has today. It lives here rather than in `kind` because it
    # is orthogonal to every kind -- an async method is both -- so encoding it
    # in the discriminant would need async_function, async_method,
    # async_generator and so on, combinatorially.
    modifiers: List[str] = []
    entrypoints: List[PyEntrypoint] = []
    is_entrypoint: bool = False
    parameters: List[PyCallableParameter] = []
    return_type: Optional[str] = None
    start_line: int = -1
    end_line: int = -1
    code_start_line: int = -1
    accessed_symbols: List[PySymbol] = []
    # Internal (#120): the Jedi-produced record that `l1_body` derives `body{}`
    # call nodes from, and that `call_graph.py`, `l2_callees.py` and the dataflow
    # builder all read. It is stripped at EMIT time (see `wire_json`), not with a
    # field-level `exclude`: the analysis cache round-trips through the same
    # serializer, so excluding it would drop it from the cache too and a warm-cache
    # run would rebuild with no call sites at all.
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
class PyClassAttribute(BaseModel):
    """Represents a Python class attribute."""

    name: str
    type: Optional[str] = None
    initializer: Optional[str] = None
    comments: List[PyComment] = []
    decorators: List[PyDecorator] = []
    start_line: int = -1
    end_line: int = -1


@builder
class PyClass(BaseModel):
    """Represents a Python class."""

    name: str
    signature: str  # e.g., module.class_name
    id: str = ""
    kind: str = "class"
    span: Optional[Span] = None
    comments: List[PyComment] = []
    base_classes: List[str] = []
    decorators: List[PyDecorator] = []
    entrypoints: List[PyEntrypoint] = []
    is_entrypoint: bool = False
    callables: Dict[str, PyCallable] = {}  # methods, keystone containment name
    attributes: Dict[str, PyClassAttribute] = {}
    types: Dict[str, "PyClass"] = {}  # inner classes, keystone containment name
    start_line: int = -1
    end_line: int = -1

    def __hash__(self):
        """Generate a hash based on the class's signature."""
        return hash(self.signature)


@builder
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
    prov: List[Literal["jedi", "defuse"]] = []


@builder
class PyExternalSymbol(BaseModel):
    """A call-graph target outside the analyzed project -- an imported library or
    builtin member. An edge-endpoint id home, not a tree node: keyed in
    ``PyApplication.external_symbols`` by its ``can://…/@external/…`` id."""

    id: str = ""  # can://python/<app>/@external/<module>/<name>
    kind: str = "external"
    name: str  # the member/short name, e.g. "get" for "requests.get"
    module: Optional[str] = None  # best-effort owning module, e.g. "requests"


@builder
class PyConfigKey(BaseModel):
    """A configuration key flattened out of a config-bearing ``PyArtifact``
    (#152). Graph vocabulary stays neutral (label ``ConfigKey``, edge
    ``DEFINES_CONFIG``) -- the ``Py`` prefix here is only the ``PyArtifact``
    naming precedent, not a Python-specific claim. L1 data, identical at
    every analysis level; nested under the owning artifact, containment
    mirrors ``DEFINES_CONFIG``."""

    id: str = ""  # <artifact-id>@key/<dotted.key>
    key: str  # dotted path; numeric segments for arrays, e.g. "services.web.ports.0"
    namespace: str  # env|yaml|json|toml|ini|properties
    value: Optional[str] = None  # populated only when options.artifact_text is on
    span: Optional[Span] = None  # into the artifact's source; best-effort for yaml/json/toml
    references: List[str] = []  # raw recognized tokens, order of appearance, deduplicated


@builder
class PyArtifact(BaseModel):
    """Any non-`.py` project file (config, manifest, CI, container spec, or
    plain data/binary) -- never dropped from the walk. Captured broadly (node
    + verbatim ``source``); *meaning* is extracted narrowly -- only
    ``dependency-manifest`` roles feed ``dependencies`` today. ``id`` is
    language-neutral (``can://artifact/<app>/<path>``)."""

    id: str = ""
    kind: str = "artifact"
    path: str  # repo-relative POSIX path (also the map key)
    format: str  # toml|yaml|json|ini|properties|requirements|dockerfile|text|binary
    roles: List[str] = []
    size_bytes: int = 0
    sha256: str = ""  # always the full file's hash, even when source is truncated/empty
    source: str = ""  # verbatim by default; "" for binary or when capture is disabled
    text_truncated: bool = False  # True when `source` is a prefix, not the full file
    extraction: str = "none"  # none|partial|full
    config_keys: List[PyConfigKey] = []  # flattened config keys (#152); [] when not namespace-eligible


@builder
class PyDependency(BaseModel):
    """One declared third-party dependency, evidence-tagged via ``prov``."""

    name: str  # PEP 503 normalized
    ecosystem: str = "pypi"  # SDK symmetry with purl (#152 rider); the only ecosystem this analyzer emits
    spec: str = ""
    kind: str = "runtime"  # runtime|dev|optional|build
    extras: List[str] = []
    declared_in: str = ""  # PyArtifact id
    # False for lockfile-only (transitive) dependencies -- pinned in a lock
    # with no manifest declaration (#152 reconciliation).
    direct: bool = True
    locked_version: Optional[str] = None
    provides_imports: List[str] = []
    prov: List[str] = []  # declared|lockfile|installed-metadata|heuristic


@builder
class PyImportBinding(BaseModel):
    """A top-level import no declared dependency accounts for."""

    module: str
    bound_to: Optional[str] = None  # best-effort distribution name
    prov: List[str] = []


@builder
class PyRepositoryInfo(BaseModel):
    """Where the analyzed source came from: git provenance captured at analysis time."""

    uri: Optional[str] = None
    revision: str
    dirty: bool = False


@builder
class PyAnalyzerInfo(BaseModel):
    """Which analyzer produced this snapshot, and how it was configured.
    Lives on the ``Analysis`` envelope (keystone ``analyzer{name,version}``;
    ``config`` rides additively)."""

    name: str = "codeanalyzer-python"
    version: str = "unknown"
    config: Dict[str, Any] = {}


@builder
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
    # Non-code artifacts, declared dependencies, and undeclared imports
    # (spec 2026-08-27). L1 data: identical at every analysis level.
    artifacts: Dict[str, PyArtifact] = {}
    dependencies: List[PyDependency] = []
    unresolved_imports: List[PyImportBinding] = []
    # Coverage/failure record for the entrypoint pass; see PyEntrypointReport (#27).
    entrypoint_report: PyEntrypointReport = PyEntrypointReport()
    # Git provenance of the analyzed checkout, captured at analysis time.
    repository: Optional[PyRepositoryInfo] = None
    # Interprocedural parameter-passing edges (formal↔actual); populated at L4.
    param_in: List[ParamEdge] = []
    param_out: List[ParamEdge] = []


@builder
class Analysis(BaseModel):
    """v2 payload root: envelope + the application tree node. ``k_limit`` is an
    L3+ envelope key (None below the dataflow levels; exclude_none drops it)."""
    schema_version: str = "2.0.0"
    language: str = "python"
    max_level: int = 1
    k_limit: Optional[int] = None
    analyzer: PyAnalyzerInfo = PyAnalyzerInfo()
    application: PyApplication
