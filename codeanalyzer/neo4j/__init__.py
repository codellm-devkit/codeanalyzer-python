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

"""Neo4j output: a pure projection of the :class:`PyApplication` IR to graph rows,
plus the two writers (cypher snapshot / bolt incremental). Nothing here runs
unless ``--emit neo4j`` (or ``--emit schema``) is selected.
"""
from codeanalyzer.neo4j.bolt import BoltConfig, bolt_writer
from codeanalyzer.neo4j.catalog import (
    MARKER_LABELS,
    NODE_LABELS,
    REL_TYPES,
    SCHEMA_VERSION,
    build_schema_document,
)
from codeanalyzer.neo4j.cypher import render_cypher
from codeanalyzer.neo4j.project import project
from codeanalyzer.neo4j.rows import EdgeRow, GraphRows, NodeRow

__all__ = [
    "project",
    "render_cypher",
    "bolt_writer",
    "BoltConfig",
    "build_schema_document",
    "SCHEMA_VERSION",
    "NODE_LABELS",
    "REL_TYPES",
    "MARKER_LABELS",
    "GraphRows",
    "NodeRow",
    "EdgeRow",
]
