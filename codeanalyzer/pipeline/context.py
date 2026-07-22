from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import PyApplication, PyModule


@dataclass
class AnalysisContext:
    """Mutable carrier threaded through the AnalysisPipeline passes.

    Inputs are set at construction; produced artifacts start as ``None`` and are
    filled in chain order: ``symbol_table`` -> ``app``/``sig_to_id`` ->
    ``infos`` -> ``ir``. ``infos`` (L3 PDGs) is deliberately reused by the L4
    pass, so its ordering in the chain matters.
    """

    # inputs
    options: AnalysisOptions
    project_dir: Path
    virtualenv: Optional[Path]
    analysis_level: int
    app_name: str
    cached_symbol_table: Dict[str, PyModule] = field(default_factory=dict)

    # produced by passes (loosely typed to avoid importing dataflow types here)
    symbol_table: Optional[Dict[str, PyModule]] = None
    app: Optional[PyApplication] = None
    sig_to_id: Optional[Dict[str, str]] = None
    infos: Optional[Dict[str, Any]] = None   # Dict[str, FunctionInfo]
    ir: Optional[Any] = None                 # ProgramGraphsIR
