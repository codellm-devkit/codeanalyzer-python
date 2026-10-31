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

from codeanalyzer.neo4j.bolt import BoltConfig, bolt_writer_streaming
from codeanalyzer.neo4j.schema import build_schema_document
from codeanalyzer.neo4j.cypher import render_cypher, write_cypher_streaming
from codeanalyzer.options import GRAPH_CYPHER, AnalysisOptions
from codeanalyzer.schema import Analysis
from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.utils import logger


def emit_schema(output: Optional[Path]) -> None:
    """Emit the Neo4j schema contract (``schema.json``) — a static artifact derived
    from the in-repo schema, independent of any analyzed project. With no
    ``output`` it prints to stdout."""
    doc = json.dumps(build_schema_document(), indent=2) + "\n"
    if output is None:
        print(doc, end="")
        return
    output.mkdir(parents=True, exist_ok=True)
    (output / "schema.json").write_text(doc)
    logger.info(f"Neo4j schema written to {output / 'schema.json'}")


def emit_neo4j(analysis: Analysis, options: AnalysisOptions) -> None:
    """Project the analysis to a graph and write it: a live Bolt push when
    ``--neo4j-uri`` is set, otherwise a self-contained ``graph.cypher`` snapshot."""
    app_name = options.app_name if options.app_name is not None else Path(options.input).resolve().name
    # Ownership changes require regeneration from source: never re-key an existing
    # analysis tree while its body/edge/artifact references still carry old IDs.
    from codeanalyzer.schema.ids import application_id
    expected = application_id(app_name, options.namespace)
    app = analysis.application
    if (analysis.identity_version != 2 or app.identity_version != 2
            or app.id != expected or app.application_name != app_name
            or app.identity_namespace != (app_name if options.namespace is None else options.namespace)):
        raise ValueError("analysis identity does not match requested scope; regenerate from source")
    sig_to_id = assign_ids(app, app_name, options.namespace)

    if options.neo4j_uri:
        cfg = BoltConfig(
            uri=options.neo4j_uri,
            user=options.neo4j_user,
            password=options.neo4j_password,
            database=options.neo4j_database,
        )
        # A full run (no single-file restriction) makes orphan pruning safe; --eager
        # is what permits any deletion at all (#171).
        full_run = options.file_name is None
        bolt_writer_streaming(analysis.application, app_name, sig_to_id, analysis.analyzer, cfg,
                              full_run, eager=options.rebuild_analysis, namespace=options.namespace)
        return

    out_dir = options.output if options.output is not None else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / GRAPH_CYPHER
    with target.open("w") as fh:  # LOW-MEM PATCH: streamed, see cypher.write_cypher_streaming
        write_cypher_streaming(fh, analysis.application, app_name, sig_to_id,
                               analyzer=analysis.analyzer, namespace=options.namespace)
    logger.info(f"Neo4j graph written to {target}")
