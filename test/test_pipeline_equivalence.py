"""Byte-for-byte characterization gate for the analysis pipeline refactor.

Runs the CLI on copies of fixtures placed OUTSIDE any git tree (so
`repository_info` returns None and the output is deterministic), normalizes the
one volatile field (`analyzer.version`), and compares against committed goldens.
Regenerate with `REGEN=1 pytest test/test_pipeline_equivalence.py`.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = [
    "class_hierarchy",
    "decorators_and_hof",
    "async_patterns",
    "method_call_resolution",
]
GOLDEN_DIR = Path(__file__).parent / "golden" / "pipeline_equivalence"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "single_functionalities"


def _scalpel_available() -> bool:
    try:
        import scalpel  # noqa: F401
        return True
    except Exception:
        return False


def _strip_volatile_paths(node) -> None:
    """Recursively drop filesystem bookkeeping fields (`PyModule.file_path`,
    `PyCallable.path`, `PyModule.last_modified`) that record where a fixture
    copy happened to live and when it was last touched on disk, not anything
    the analyzer computed. `tmp_path` mints a fresh, uniquely-numbered
    directory every pytest invocation, so these fields differ run to run even
    when the analysis content is identical — they must be normalized away for
    the golden comparison to be meaningful."""
    if isinstance(node, dict):
        for key in ("file_path", "path", "last_modified"):
            node.pop(key, None)
        for value in node.values():
            _strip_volatile_paths(value)
    elif isinstance(node, list):
        for item in node:
            _strip_volatile_paths(item)


def _normalize(payload: dict) -> dict:
    """Drop the environment-volatile fields so the gate is stable across
    version bumps and non-git run locations."""
    payload.get("application", {}).pop("repository", None)
    analyzer = payload.get("analyzer")
    if isinstance(analyzer, dict):
        analyzer.pop("version", None)
    _strip_volatile_paths(payload.get("application", {}))
    return payload


def _run(proj: Path, level: int) -> dict:
    out = subprocess.run(
        [sys.executable, "-m", "codeanalyzer", "-i", str(proj), "-a", str(level), "--no-venv"],
        capture_output=True, text=True, check=True,
    ).stdout
    return _normalize(json.loads(out))


@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_pipeline_output_matches_golden(tmp_path, fixture, level):
    if level == 4 and not _scalpel_available():
        pytest.skip("L4 golden requires python-scalpel (optional soft dependency)")
    proj = tmp_path / fixture
    shutil.copytree(FIXTURE_ROOT / fixture, proj)
    got = _run(proj, level)
    golden_path = GOLDEN_DIR / f"{fixture}.a{level}.json"
    if os.environ.get("REGEN"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(got, indent=2, sort_keys=True), encoding="utf-8")
        return
    assert golden_path.exists(), f"missing golden {golden_path}; regenerate with REGEN=1"
    want = _normalize(json.loads(golden_path.read_text(encoding="utf-8")))
    assert got == want, f"{fixture} @ -a {level} diverged from golden"
