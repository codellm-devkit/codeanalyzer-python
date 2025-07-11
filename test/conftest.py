# conftest.py
from pathlib import Path
import pytest
from typer.testing import CliRunner


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
    return Path(__file__).resolve().parents[2]
