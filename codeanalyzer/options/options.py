from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from enum import Enum


class EmitTarget(str, Enum):
    """Output target selected by ``--emit``.

    - ``json``   : the canonical ``analysis.json`` (symbol table + call graph).
    - ``neo4j``  : project the analysis into a labeled property graph — a
                   ``graph.cypher`` snapshot, or a live Bolt push with ``--neo4j-uri``.
    - ``schema`` : the machine-readable, version-stamped Neo4j schema contract.
    """

    JSON = "json"
    NEO4J = "neo4j"
    SCHEMA = "schema"


@dataclass
class AnalysisOptions:
    input: Path
    output: Optional[Path] = None
    emit: EmitTarget = EmitTarget.JSON
    app_name: Optional[str] = None
    neo4j_uri: Optional[str] = None
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"
    neo4j_database: Optional[str] = None
    analysis_level: int = 1
    # Level-3 dataflow knobs: which program graphs to emit (csv of
    # cfg|dfg|pdg|sdg) and the access-path k-limit.
    graphs: str = "cfg,dfg,pdg,sdg"
    graph_field_depth: int = 3
    using_ray: bool = False
    rebuild_analysis: bool = False
    skip_tests: bool = True
    no_venv: bool = False
    file_name: Optional[Path] = None
    cache_dir: Optional[Path] = None
    clear_cache: bool = False
    verbosity: int = 0
    entrypoint_rules: Tuple[Path, ...] = ()
