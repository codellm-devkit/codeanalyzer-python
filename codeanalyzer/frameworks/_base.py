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

"""Framework-independent entrypoint detection — abstract layer.

Ports the JackEE entrypoint-finder architecture (Antoniadis et al., PLDI
2020) that codeanalyzer-java uses under ``com.ibm.cldk.javaee`` for the
JVM. CRUD detection is intentionally out of scope.

Entrypoint-finding is now one *kind* of analysis pass.
``AbstractEntrypointFinder`` is a thin ``AnalysisPass`` (see
``codeanalyzer.analysis._pass``) whose ``run`` iterates every callable
and class in the symbol table, delegating to the two predicates a
concrete finder implements. The transient binding types and the
project-wide context live in the analysis layer and are re-exported here
for finder authors. ``EntrypointContext`` is kept as an alias of
``AnalysisContext`` for continuity.

Finders return ``PyEntrypoint`` schema objects; the registry collects
them into ``PyApplication.entrypoints``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar, List, Optional

from codeanalyzer.analysis._pass import (
    AnalysisContext,
    AnalysisPass,
    AnalysisResult,
    BindingFact,
    BindingKind,
)
from codeanalyzer.schema.py_schema import (
    PyApplication,
    PyCallable,
    PyClass,
    PyEntrypoint,
)
from codeanalyzer.semantic_analysis.call_graph import (
    iter_callables_in_symbol_table,
    iter_classes_in_symbol_table,
)

# Re-exported so finder modules import the whole vocabulary from one place.
EntrypointContext = AnalysisContext

__all__ = [
    "BindingFact",
    "BindingKind",
    "AnalysisContext",
    "EntrypointContext",
    "AbstractEntrypointFinder",
]


class AbstractEntrypointFinder(AnalysisPass):
    """Per-framework entrypoint detector — an ``AnalysisPass``.

    Direct counterpart of codeanalyzer-java's
    ``com.ibm.cldk.javaee.AbstractEntrypointFinder``. Subclasses set
    ``framework_name`` and implement both predicates; ``run`` (provided
    here) iterates the symbol table and collects the results. Concrete
    finders should be cheap to instantiate and free of per-project state
    — all project facts arrive via ``run``'s arguments.

    Finders are pure producers of entrypoints: they declare no
    capabilities by default, so the registry orders them freely relative
    to edge-synthesizing passes.
    """

    framework_name: ClassVar[str] = ""

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.framework_name or type(self).__name__

    def run(self, app: PyApplication, ctx: AnalysisContext) -> AnalysisResult:
        result = AnalysisResult()
        for func in iter_callables_in_symbol_table(app.symbol_table):
            ep = self.find_function(func, ctx)
            if ep is not None:
                result.entrypoints.append(ep)
        for cls in iter_classes_in_symbol_table(app.symbol_table):
            result.entrypoints.extend(self.find_class(cls, ctx))
        return result

    @abstractmethod
    def find_function(
        self,
        func: PyCallable,
        ctx: AnalysisContext,
    ) -> Optional[PyEntrypoint]:
        """Return a ``PyEntrypoint`` if ``func`` is an entrypoint under this
        framework, else ``None``.

        Used for decorator-bound, convention-bound, and binding-table-bound
        entrypoints — Flask ``@app.route``, FastAPI ``@router.get``, Celery
        ``@shared_task``, Click ``@cli.command``, the AWS Lambda
        ``def handler(event, context)`` convention, and Django function
        views resolved via ``urls.py``.
        """

    @abstractmethod
    def find_class(
        self,
        cls: PyClass,
        ctx: AnalysisContext,
    ) -> List[PyEntrypoint]:
        """Return one ``PyEntrypoint`` per framework-dispatched method on
        ``cls``.

        Used for inheritance-based entrypoints where the framework invokes
        specific methods on a subclass — Tornado ``RequestHandler.get``/
        ``post``/..., Django CBV ``get``/``post``/..., gRPC ``Servicer``
        RPC methods. Returns ``[]`` for frameworks where class-level
        detection does not apply (Flask, FastAPI, Celery, Click,
        argparse, AWS Lambda).
        """
