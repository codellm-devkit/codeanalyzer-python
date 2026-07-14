"""Provenance capture: repository (git) and analyzer identity for a snapshot.

Runs git only as a subprocess query against the analyzed project directory —
never mutates anything. Absence of git (no repo, no binary) degrades to None.
"""
from __future__ import annotations

import subprocess
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Optional, Union

from codeanalyzer.schema.py_schema import PyAnalyzerInfo, PyRepositoryInfo


def _git(project_dir: Union[Path, str], *args: str) -> Optional[str]:
    """One git query; None on any failure (no repo, no git, timeout)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), *args],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def repository_info(project_dir: Union[Path, str]) -> Optional[PyRepositoryInfo]:
    """Git provenance of ``project_dir``, or None when it isn't a git checkout."""
    revision = _git(project_dir, "rev-parse", "HEAD")
    if not revision:
        return None
    uri = _git(project_dir, "remote", "get-url", "origin") or None
    status = _git(project_dir, "status", "--porcelain", "--untracked-files=no")
    return PyRepositoryInfo(uri=uri, revision=revision, dirty=bool(status))


def analyzer_info(analysis_level: int) -> PyAnalyzerInfo:
    """Identity + configuration of this analyzer run."""
    try:
        version = _pkg_version("codeanalyzer-python")
    except PackageNotFoundError:
        version = "unknown"
    return PyAnalyzerInfo(
        name="codeanalyzer-python",
        version=version,
        config={"analysis_level": analysis_level},
    )
