# conftest.py
import shutil
import subprocess
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


# ============================================================================
# Taint Analysis CodeQL Database Fixtures
# ============================================================================

_TAINT_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "taint_analysis"

_TAINT_FIXTURE_APPS = {
    "sql_injection": _TAINT_FIXTURES_DIR / "sql_injection_app",
    "command_injection": _TAINT_FIXTURES_DIR / "command_injection_app",
    "path_traversal": _TAINT_FIXTURES_DIR / "path_traversal_app",
    "xss": _TAINT_FIXTURES_DIR / "xss_app",
    "flask": _TAINT_FIXTURES_DIR / "flask_app",
    "sanitizer": _TAINT_FIXTURES_DIR / "sanitizer_app",
}


def _codeql_available() -> bool:
    """Check if CodeQL CLI is available."""
    return shutil.which("codeql") is not None


def _create_codeql_database(source_dir: Path, db_path: Path) -> bool:
    """Create a CodeQL database for a Python source directory."""
    cmd = [
        "codeql", "database", "create", str(db_path),
        f"--source-root={source_dir}",
        "--language=python",
        "--overwrite",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


@pytest.fixture(scope="session")
def codeql_databases(tmp_path_factory):
    """Session-scoped fixture that creates CodeQL databases for all taint fixture apps.

    Databases are created once per test session and shared across all tests.
    If CodeQL is not available, returns None and dependent tests will be skipped.

    Returns:
        dict: Map of fixture name -> database path, or None if CodeQL unavailable
    """
    if not _codeql_available():
        return None

    db_base = tmp_path_factory.mktemp("codeql_dbs")
    databases = {}

    for name, source_dir in _TAINT_FIXTURE_APPS.items():
        db_path = db_base / f"{name}_db"
        if _create_codeql_database(source_dir, db_path):
            databases[name] = db_path
        else:
            databases[name] = None

    return databases


@pytest.fixture(scope="session")
def sql_injection_db(codeql_databases):
    """Session-scoped CodeQL database for SQL injection fixture."""
    if codeql_databases is None:
        pytest.skip("CodeQL not available")
    db = codeql_databases.get("sql_injection")
    if db is None:
        pytest.skip("Failed to create SQL injection CodeQL database")
    return db


@pytest.fixture(scope="session")
def command_injection_db(codeql_databases):
    """Session-scoped CodeQL database for command injection fixture."""
    if codeql_databases is None:
        pytest.skip("CodeQL not available")
    db = codeql_databases.get("command_injection")
    if db is None:
        pytest.skip("Failed to create command injection CodeQL database")
    return db


@pytest.fixture(scope="session")
def path_traversal_db(codeql_databases):
    """Session-scoped CodeQL database for path traversal fixture."""
    if codeql_databases is None:
        pytest.skip("CodeQL not available")
    db = codeql_databases.get("path_traversal")
    if db is None:
        pytest.skip("Failed to create path traversal CodeQL database")
    return db


@pytest.fixture(scope="session")
def xss_db(codeql_databases):
    """Session-scoped CodeQL database for XSS fixture."""
    if codeql_databases is None:
        pytest.skip("CodeQL not available")
    db = codeql_databases.get("xss")
    if db is None:
        pytest.skip("Failed to create XSS CodeQL database")
    return db


@pytest.fixture(scope="session")
def flask_db(codeql_databases):
    """Session-scoped CodeQL database for Flask fixture."""
    if codeql_databases is None:
        pytest.skip("CodeQL not available")
    db = codeql_databases.get("flask")
    if db is None:
        pytest.skip("Failed to create Flask CodeQL database")
    return db


@pytest.fixture(scope="session")
def sanitizer_db(codeql_databases):
    """Session-scoped CodeQL database for sanitizer fixture."""
    if codeql_databases is None:
        pytest.skip("CodeQL not available")
    db = codeql_databases.get("sanitizer")
    if db is None:
        pytest.skip("Failed to create sanitizer CodeQL database")
    return db


@pytest.fixture(scope="session")
def codeql_packs_dir(tmp_path_factory):
    """Session-scoped fixture that installs a qlpack with codeql/python-all once.

    Returns the pack directory path, or None if CodeQL is unavailable.
    Tests that need this should skip when it returns None.
    """
    if not _codeql_available():
        return None

    pack_dir = tmp_path_factory.mktemp("codeql_qlpack")
    qlpack_yml = pack_dir / "qlpack.yml"
    qlpack_yml.write_text(
        "name: codeanalyzer-test-pack\n"
        "version: 1.0.0\n"
        "dependencies:\n"
        '  "codeql/python-all": "*"\n'
    )
    result = subprocess.run(
        ["codeql", "pack", "install", str(pack_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return pack_dir
