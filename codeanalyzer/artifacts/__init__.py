"""Non-code artifact capture and dependency extraction (spec 2026-08-27).

Capture is broad (every rule-matched config-shaped file becomes a
:class:`~codeanalyzer.schema.py_schema.PyArtifact`); extraction is narrow
(only dependency manifests are parsed for meaning in this unit)."""

from codeanalyzer.artifacts.discovery import discover_artifacts

__all__ = ["discover_artifacts"]
