from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from enum import Enum


class OutputFormat(str, Enum):
    JSON = "json"
    MSGPACK = "msgpack"


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
    format: OutputFormat = OutputFormat.JSON
    emit: EmitTarget = EmitTarget.JSON
    app_name: Optional[str] = None
    neo4j_uri: Optional[str] = None
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"
    neo4j_database: Optional[str] = None
    using_codeql: bool = False
    using_ray: bool = False
    rebuild_analysis: bool = False
    skip_tests: bool = True
    no_venv: bool = False
    file_name: Optional[Path] = None
    cache_dir: Optional[Path] = None
    clear_cache: bool = False
    verbosity: int = 0
