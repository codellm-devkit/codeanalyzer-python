"""Non-code artifact capture and dependency extraction (spec 2026-08-27).

Capture never drops a file (every non-`.py` file becomes a
:class:`~codeanalyzer.schema.py_schema.PyArtifact`, rule-matched or not,
text or binary -- issue #157 follow-up); extraction is narrow (only
dependency manifests are parsed for meaning in this unit)."""

from codeanalyzer.artifacts.config_keys import extract_config_keys, is_config_eligible
from codeanalyzer.artifacts.config_use import detect_config_reads, resolve_uses
from codeanalyzer.artifacts.dependencies import build_dependency_view
from codeanalyzer.artifacts.discovery import discover_artifacts

__all__ = [
    "discover_artifacts", "build_dependency_view",
    "extract_config_keys", "is_config_eligible",
    "detect_config_reads", "resolve_uses",
]
