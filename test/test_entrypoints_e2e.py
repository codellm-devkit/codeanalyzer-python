"""End-to-end coverage: entrypoint detection driven through the real CLI,
including the ``--entrypoint-rules`` user-rules path (#27).

A local decorator (not Flask) so matching resolves deterministically via
Jedi without a venv/network dependency -- see the fixture's ``rules.yml``
for why. The fixture also carries a ``requirements.txt`` naming ``app`` so
Stage 0 detection has a real manifest signal to key off of (the fixture
module is not imported by anything, so an import scan alone would never
see it).
"""
import json
import subprocess
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "single_functionalities" / "entrypoints_local"


def test_decorated_function_flagged_and_helper_not(tmp_path):
    subprocess.run(
        [
            "uv", "run", "canpy",
            "-i", str(FIXTURE),
            "-a", "1",
            "-o", str(tmp_path),
            "--no-venv",
            # Cache defaults to the input dir; keep it in tmp_path so the
            # checked-in fixture directory is never mutated by a test run
            # and each run starts from a clean (entrypoint-free) cache.
            "--cache-dir", str(tmp_path / "cache"),
            "--entrypoint-rules", str(FIXTURE / "rules.yml"),
        ],
        check=True,
    )
    data = json.loads((tmp_path / "analysis.json").read_text())
    fns = data["application"]["symbol_table"]["app.py"]["functions"]

    create = fns["create_product"]
    assert create["is_entrypoint"] is True
    (ep,) = create["entrypoints"]
    assert ep["framework"] == "inhouse" and ep["rule"] == "inhouse.route"
    assert ep["route"] == "/products" and ep["http_methods"] == ["POST"]
    assert ep["ruleset"].startswith("user:")

    assert fns["helper"]["is_entrypoint"] is False
    assert fns["helper"]["entrypoints"] == []

    report = data["application"]["entrypoint_report"]
    assert "inhouse" in report["frameworks_detected"]
    assert report["errors"] == []
