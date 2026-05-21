################################################################################
# Copyright IBM Corporation 2026
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

"""Analysis-pass superset abstraction.

A whole-application pass that runs after the symbol table and the base
(Jedi/CodeQL) call graph are built. A pass may contribute *entrypoints*
(framework-dispatched roots) and/or *synthetic call edges* (dispatch the
static call graph cannot see — e.g. Odoo ORM ``write()`` -> a
``@api.depends`` compute method).

Entrypoint-finding is one kind of pass: ``AbstractEntrypointFinder``
(in ``codeanalyzer.frameworks._base``) is a thin ``AnalysisPass``
subclass. Out-of-tree packages register their own passes via the
``codeanalyzer.analysis_passes`` entry-point group; the registry orders
all passes by declared ``requires``/``provides`` capabilities.

Core never interprets pass-defined vocabulary. ``PyEntrypoint`` and
``PyCallEdge`` carry it in their open ``detection_source``/``provenance``
fields and free-form ``tags`` dicts so a persisted ``analysis.json``
round-trips regardless of which passes were installed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, FrozenSet, List, Optional

from typing_extensions import Literal

from codeanalyzer.schema.py_schema import (
    PyApplication,
    PyCallEdge,
    PyEntrypoint,
)

#: Entry-point group out-of-tree packages declare in their ``pyproject.toml``
#: under ``[project.entry-points."codeanalyzer.analysis_passes"]``. Each
#: entry point must resolve to an ``AnalysisPass`` subclass.
ANALYSIS_PASS_ENTRYPOINT_GROUP = "codeanalyzer.analysis_passes"


BindingKind = Literal[
    "url_resolver",  # Django path() / re_path() / url() / include()
    "router_mount",  # FastAPI app.include_router / app.mount
    "blueprint",  # Flask register_blueprint
    # --- command-line bindings. conceptually similar to web frameworks ---
    "lambda_template",  # AWS SAM / serverless.yml
    "typer_subapp",  # Typer app.add_typer
    "click_add_command",  # Click cli.add_command(my_func)
    "argparse_dispatch",  # argparse parser.set_defaults(func=my_handler)
]


@dataclass(frozen=True)
class BindingFact:
    """One external->internal binding resolved by a routing pre-pass.

    Stored in ``AnalysisContext.external_bindings`` keyed by the target
    callable's ``PyCallable.signature``. Multiple facts per signature are
    permitted (one function bound under several routes).
    """

    framework: str
    binding_kind: BindingKind
    source_file: str
    route_path: Optional[str] = None
    http_methods: List[str] = field(default_factory=list)
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisContext:
    """Project-wide context handed to every pass.

    Built once by the registry after the symbol table and base call graph
    are ready; immutable thereafter so passes cannot mutate global state
    mid-pipeline. The current ``PyApplication`` is passed separately to
    ``AnalysisPass.run`` (it accumulates upstream passes' results), so the
    context only carries derived helpers.

    * ``external_bindings`` — output of a routing pre-pass; keyed by the
      target callable's ``PyCallable.signature``. Empty for non-web /
      non-CLI projects (no routing pre-pass is wired in core yet).
    * ``resolve_base_chain`` — given a class's fully-qualified name,
      returns the transitive FQCN inheritance chain starting with the
      class itself. Used by inheritance-based finders (Tornado, Django
      CBV, gRPC ``Servicer``); decorator/convention finders ignore it.
    * ``shared`` — inter-pass handoff scratch space. This is the channel
      that makes ``provides``/``requires`` meaningful: a pass declaring
      ``provides={"odoo.model_identity"}`` writes its derived facts to
      ``shared["odoo.model_identity"]``; a pass declaring
      ``requires={"odoo.model_identity"}`` reads them back. Keyed by
      capability token. The dataclass is frozen (passes cannot rebind
      the field) but this dict is intentionally mutable. Never
      serialized; never interpreted by core.
    """

    external_bindings: Dict[str, List[BindingFact]]
    resolve_base_chain: Callable[[str], List[str]]
    shared: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisResult:
    """What a single pass contributed.

    Mutable and cheap: the registry merges each result into the running
    ``PyApplication`` before the next pass runs, so a downstream pass sees
    upstream entrypoints and synthetic edges.
    """

    entrypoints: List[PyEntrypoint] = field(default_factory=list)
    call_edges: List[PyCallEdge] = field(default_factory=list)

    def extend(self, other: "AnalysisResult") -> None:
        self.entrypoints.extend(other.entrypoints)
        self.call_edges.extend(other.call_edges)


class AnalysisPass(ABC):
    """A whole-application analysis pass.

    Concrete passes must set ``name`` and implement ``run``.
    ``provides``/``requires`` are capability tokens (free-form strings)
    the registry topologically sorts on: a pass declaring
    ``requires={"odoo.model_identity"}`` is ordered after whichever pass
    declares ``provides={"odoo.model_identity"}``. An unsatisfied
    requirement or a cycle is a hard error.

    Passes should be cheap to instantiate and free of per-project state —
    all project facts arrive via ``run``'s arguments.
    """

    #: Stable identifier, used in ordering errors and logs.
    name: ClassVar[str] = ""
    #: Capability tokens this pass makes available to later passes.
    provides: ClassVar[FrozenSet[str]] = frozenset()
    #: Capability tokens this pass needs satisfied before it runs.
    requires: ClassVar[FrozenSet[str]] = frozenset()

    @abstractmethod
    def run(self, app: PyApplication, ctx: AnalysisContext) -> AnalysisResult:
        """Analyze ``app`` and return contributed entrypoints / edges.

        ``app`` already contains the symbol table, the base call graph,
        and the results of every pass ordered before this one. Treat it
        as read-only — return contributions in an ``AnalysisResult``; the
        registry is responsible for merging them in.
        """
