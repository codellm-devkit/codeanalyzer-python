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

"""The facade between the CLI and the Neo4j backend. Two entry points:

- :func:`emit_schema` — serialize the static, version-stamped schema contract
  (``schema.json``). Needs no analyzed project.
- :func:`emit_neo4j` — project a :class:`PyApplication` to a graph and either
  write a ``graph.cypher`` snapshot or push it to a live Neo4j over Bolt.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from codeanalyzer.neo4j.bolt import BoltConfig, bolt_writer
from codeanalyzer.neo4j.catalog import build_schema_document
from codeanalyzer.neo4j.cypher import render_cypher
from codeanalyzer.neo4j.project import project
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import PyApplication
from codeanalyzer.utils import logger


def emit_schema(output: Optional[Path]) -> None:
    """Emit the Neo4j schema contract (``schema.json``) — a static artifact derived
    from the in-repo catalog, independent of any analyzed project. With no
    ``output`` it prints to stdout."""
    doc = json.dumps(build_schema_document(), indent=2) + "\n"
    if output is None:
        print(doc, end="")
        return
    output.mkdir(parents=True, exist_ok=True)
    (output / "schema.json").write_text(doc)
    logger.info(f"Neo4j schema written to {output / 'schema.json'}")


def emit_neo4j(app: PyApplication, options: AnalysisOptions) -> None:
    """Project the analysis to a graph and write it: a live Bolt push when
    ``--neo4j-uri`` is set, otherwise a self-contained ``graph.cypher`` snapshot."""
    app_name = options.app_name or Path(options.input).resolve().name
    rows = project(app, app_name)

    if options.neo4j_uri:
        cfg = BoltConfig(
            uri=options.neo4j_uri,
            user=options.neo4j_user,
            password=options.neo4j_password,
            database=options.neo4j_database,
        )
        # A full run (no single-file restriction) makes orphan pruning safe.
        full_run = options.file_name is None
        bolt_writer(rows, cfg, full_run)
        return

    out_dir = options.output if options.output is not None else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "graph.cypher"
    target.write_text(render_cypher(rows, app_name))
    logger.info(f"Neo4j graph written to {target}")
