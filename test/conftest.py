# conftest.py
from pathlib import Path

import pytest
from typer.testing import CliRunner
import logging
from rich.console import Console
from rich.logging import RichHandler
from codeanalyzer.utils import logger

# Ensure the test logger emits DEBUG
console = Console()
handler = RichHandler(console=console, show_time=True, show_level=True, show_path=False)

logger.setLevel(logging.DEBUG)
logger.addHandler(handler)
logger.propagate = False  # Avoid duplicated logs

@pytest.fixture
def cli_runner() -> CliRunner:
    """
    Pytest fixture that provides a Typer CliRunner instance
    to simulate CLI calls for testing.
    """
    return CliRunner()


@pytest.fixture
def project_root() -> Path:
    """Returns the grandparent directory of this conftest file — typically the project root."""
    return Path(__file__).resolve().parents[1]
