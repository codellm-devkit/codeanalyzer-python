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

"""Level-3 native dataflow graphs: CFG, PDG (CDG + DDG), and the SDG.

One pass per module, mirroring the construction ladder:

- :mod:`cfg` — stage 1, exceptional statement-level CFG per callable;
- :mod:`dominance` — stage 2, post-dominators and control dependence;
- :mod:`access_paths` — stage 3a, the k-limited access-path variable model;
- :mod:`defuse` — stage 3b, reaching definitions → DDG edges;
- :mod:`alias` — stage 5, the type-based may-alias oracle (MVP stub);
- :mod:`scc` — stage 5, Tarjan SCC condensation of the call graph;
- :mod:`summaries` — stage 6, bottom-up formal-in → formal-out summaries;
- :mod:`sdg` — stage 7, parameter nodes and CALL/PARAM_IN/PARAM_OUT/SUMMARY
  edges;
- :mod:`slicing` — stage 8, the two-phase context-sensitive backward slice;
- :mod:`builder` — the orchestrator ``build_program_graphs`` wired into
  ``Codeanalyzer.analyze`` at ``-a 3``.
"""

from codeanalyzer.dataflow.cfg import build_cfg  # noqa: F401
