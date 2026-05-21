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

"""Pluggable whole-application analysis-pass layer.

A single ``AnalysisPass`` superset abstraction (``_pass``) plus a registry
that discovers in-tree built-ins and out-of-tree extensions via
``importlib.metadata`` entry points, orders them by declared
``requires``/``provides`` capabilities, and runs them over the
``PyApplication`` (``registry``).
"""

from codeanalyzer.analysis._pass import (
    ANALYSIS_PASS_ENTRYPOINT_GROUP,
    AnalysisContext,
    AnalysisPass,
    AnalysisResult,
    BindingFact,
    BindingKind,
)
from codeanalyzer.analysis.registry import (
    discover_passes,
    order_passes,
    run_pipeline,
)

__all__ = [
    "ANALYSIS_PASS_ENTRYPOINT_GROUP",
    "AnalysisContext",
    "AnalysisPass",
    "AnalysisResult",
    "BindingFact",
    "BindingKind",
    "discover_passes",
    "order_passes",
    "run_pipeline",
]
