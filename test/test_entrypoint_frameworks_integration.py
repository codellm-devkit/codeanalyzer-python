"""Integration coverage: shipped `rules.yml` decorator patterns against the
REAL frameworks they target (#27, #122 review).

The shipped rules match on ``PyDecorator.qualified_name`` -- Jedi's resolved
DEFINITION path, not the public import path the rules read as if they were
written against (e.g. ``@app.route`` resolves to
``flask.sansio.scaffold.Scaffold.route``, not ``flask.Flask.route``). Every
other entrypoint test either hand-crafts a ``qualified_name`` or uses a local
in-repo decorator, so none of them would notice a real framework's actual
resolution path drifting out from under the shipped patterns. This is the
test that would have caught it: it drives the real CLI over a tiny app built
on the real, installed package.

Each framework is an optional test-only dependency (see the `test`
dependency-group in ``pyproject.toml``); guarded with ``importorskip`` so the
suite still runs where one is absent (e.g. below the ``python_version >=
'3.11'`` floor those pins carry, to dodge a ``ray``/``celery`` click-version
conflict below that -- see the comment in ``pyproject.toml``).
"""
import json
import subprocess
from pathlib import Path

import pytest


def _run(fixture_dir: Path, out_dir: Path) -> dict:
    subprocess.run(
        [
            "uv", "run", "canpy",
            "-i", str(fixture_dir),
            "-a", "1",
            "-o", str(out_dir),
            "--no-venv",
            # Cache defaults to the input dir; keep it in out_dir so the
            # fixture directory is never mutated and each run starts cold.
            "--cache-dir", str(out_dir / "cache"),
        ],
        check=True,
    )
    return json.loads((out_dir / "analysis.json").read_text())


def test_flask_route_and_verb_decorators_are_flagged(tmp_path):
    pytest.importorskip("flask")
    app_dir = tmp_path / "src"
    app_dir.mkdir()
    (app_dir / "app.py").write_text(
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "\n"
        "@app.route('/products', methods=['POST'])\n"
        "def create_product():\n"
        "    return 'ok'\n"
        "\n"
        "\n"
        "@app.get('/products')\n"
        "def list_products():\n"
        "    return []\n"
    )
    data = _run(app_dir, tmp_path / "out")
    fns = data["application"]["symbol_table"]["app.py"]["functions"]

    (ep,) = fns["create_product"]["entrypoints"]
    assert ep["framework"] == "flask" and ep["rule"] == "flask.route"
    assert ep["route"] == "/products" and ep["http_methods"] == ["POST"]

    (ep,) = fns["list_products"]["entrypoints"]
    assert ep["framework"] == "flask" and ep["rule"] == "flask.bp-verb"
    assert ep["route"] == "/products" and ep["http_methods"] == ["GET"]

    assert "flask" in data["application"]["entrypoint_report"]["frameworks_detected"]
    assert data["application"]["entrypoint_report"]["errors"] == []


def test_fastapi_get_and_router_post_decorators_are_flagged(tmp_path):
    pytest.importorskip("fastapi")
    app_dir = tmp_path / "src"
    app_dir.mkdir()
    (app_dir / "app.py").write_text(
        "from fastapi import APIRouter, FastAPI\n"
        "\n"
        "api = FastAPI()\n"
        "router = APIRouter()\n"
        "\n"
        "\n"
        "@api.get('/items')\n"
        "def read_items():\n"
        "    return []\n"
        "\n"
        "\n"
        "@router.post('/items')\n"
        "def create_item():\n"
        "    return {}\n"
    )
    data = _run(app_dir, tmp_path / "out")
    fns = data["application"]["symbol_table"]["app.py"]["functions"]

    (ep,) = fns["read_items"]["entrypoints"]
    assert ep["framework"] == "fastapi" and ep["rule"] == "fastapi.verb"
    assert ep["route"] == "/items" and ep["http_methods"] == ["GET"]

    (ep,) = fns["create_item"]["entrypoints"]
    assert ep["framework"] == "fastapi" and ep["rule"] == "fastapi.router-verb"
    assert ep["route"] == "/items" and ep["http_methods"] == ["POST"]

    assert "fastapi" in data["application"]["entrypoint_report"]["frameworks_detected"]
    assert data["application"]["entrypoint_report"]["errors"] == []


def test_celery_shared_task_and_app_task_decorators_are_flagged(tmp_path):
    pytest.importorskip("celery")
    app_dir = tmp_path / "src"
    app_dir.mkdir()
    (app_dir / "app.py").write_text(
        "from celery import Celery, shared_task\n"
        "\n"
        "cel = Celery('x')\n"
        "\n"
        "\n"
        "@shared_task\n"
        "def add(x, y):\n"
        "    return x + y\n"
        "\n"
        "\n"
        "@cel.task\n"
        "def mul(x, y):\n"
        "    return x * y\n"
    )
    data = _run(app_dir, tmp_path / "out")
    fns = data["application"]["symbol_table"]["app.py"]["functions"]

    (ep,) = fns["add"]["entrypoints"]
    assert ep["framework"] == "celery" and ep["rule"] == "celery.shared-task"

    (ep,) = fns["mul"]["entrypoints"]
    assert ep["framework"] == "celery" and ep["rule"] == "celery.task"

    assert "celery" in data["application"]["entrypoint_report"]["frameworks_detected"]
    assert data["application"]["entrypoint_report"]["errors"] == []


def test_click_command_decorator_is_flagged(tmp_path):
    pytest.importorskip("click")
    app_dir = tmp_path / "src"
    app_dir.mkdir()
    (app_dir / "app.py").write_text(
        "import click\n"
        "\n"
        "\n"
        "@click.command()\n"
        "def cli():\n"
        "    pass\n"
    )
    data = _run(app_dir, tmp_path / "out")
    fns = data["application"]["symbol_table"]["app.py"]["functions"]

    (ep,) = fns["cli"]["entrypoints"]
    assert ep["framework"] == "click" and ep["rule"] == "click.command"

    assert "click" in data["application"]["entrypoint_report"]["frameworks_detected"]
    assert data["application"]["entrypoint_report"]["errors"] == []
