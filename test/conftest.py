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
def whole_applications__xarray() -> Path:
    """The xarray application directory."""
    return Path(__file__).parent.resolve().joinpath("fixtures", "whole_applications", "xarray")

@pytest.fixture
def single_functionalities__stuff_nested_in_functions() -> Path:
    """Returns the path to the 'single_functionalities/stuff_nested_in_functions' directory."""
    return Path(__file__).parent.resolve().joinpath("fixtures", "single_functionalities", "stuff_nested_in_functions_test")
