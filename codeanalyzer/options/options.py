from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from enum import Enum


class OutputFormat(str, Enum):
    JSON = "json"
    MSGPACK = "msgpack"


@dataclass
class AnalysisOptions:
    input: Path
    output: Optional[Path] = None
    format: OutputFormat = OutputFormat.JSON
    analysis_level: int = 1
    using_codeql: bool = False
    using_ray: bool = False
    rebuild_analysis: bool = False
    skip_tests: bool = True
    file_name: Optional[Path] = None
    cache_dir: Optional[Path] = None
    clear_cache: bool = False
    verbosity: int = 0
