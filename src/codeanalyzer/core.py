import hashlib
from pdb import set_trace
import shutil
import subprocess
from pathlib import Path
import sys
from typing import Any, Dict, Union, Optional
from loguru import logger

from codeanalyzer.schema.py_schema import PyApplication, PyModule
from codeanalyzer.semantic_analysis.codeql import CodeQLLoader
from codeanalyzer.semantic_analysis.codeql.codeql_exceptions import (
    CodeQLExceptions,
)
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder


class AnalyzerCore:
    """Core functionality for CodeQL analysis.

    Args:
        project_dir (Union[str, Path]): The root directory of the project to analyze.
        virtualenv (Optional[Path]): Path to the virtual environment directory.
        using_codeql (bool): Whether to use CodeQL for analysis.
        rebuild_analysis (bool): Whether to force rebuild the database.
        clear_cache (bool): Whether to delete the cached directory after analysis.
        analysis_depth (int): Depth of analysis (reserved for future use).
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        analysis_depth: int = 1,
        using_codeql: bool = False,
        rebuild_analysis: bool = False,
        cache_dir: Optional[Path] = None,
        clear_cache: bool = True,
    ) -> None:
        self.analysis_depth = analysis_depth
        self.project_dir = Path(project_dir).resolve()
        self.using_codeql = using_codeql
        self.rebuild_analysis = rebuild_analysis
        self.cache_dir = (
            cache_dir.resolve() if cache_dir is not None else self.project_dir 
        ) / ".codeanalyzer"
        self.clear_cache = clear_cache
        self.db_path: Optional[Path] = None
        self.codeql_bin: Optional[Path] = None
        self.virtualenv: Optional[Path] = None

    @staticmethod
    def _cmd_exec_helper(
        cmd: list[str],
        cwd: Optional[Path] = None,
        capture_output: bool = True,
        check: bool = True,
        suppress_output: bool = False,
    ) -> subprocess.CompletedProcess:
        """
        Runs a subprocess with real-time output streaming to the logger.

        Args:
            cmd: Command as a list of arguments.
            cwd: Working directory to run the command in.
            capture_output: If True, retains and returns the output.
            check: If True, raises CalledProcessError on non-zero exit.
            suppress_output: If True, silences log output.

        Returns:
            subprocess.CompletedProcess
        """
        logger.info(f"Running: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        assert process.stdout is not None  # for type checking
        output_lines = []

        for line in process.stdout:
            line = line.rstrip()
            if not suppress_output:
                logger.debug(line)
            if capture_output:
                output_lines.append(line)

        returncode = process.wait()

        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode, cmd, output="\n".join(output_lines)
            )

        return subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout="\n".join(output_lines) if capture_output else None,
            stderr=None,
        )


    def __enter__(self) -> "AnalyzerCore":
        # If no virtualenv is provided, try to create one using requirements.txt or pyproject.toml
        venv_path = self.cache_dir / self.project_dir.name / "virtualenv"
        # Ensure the cache directory exists for this project
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        # Create the virtual environment if it does not exist
        if not venv_path.exists() or self.rebuild_analysis:
            logger.info(f"(Re-)creating virtual environment at {venv_path}")
            self._cmd_exec_helper(
                [sys.executable, "-m", "venv", str(venv_path)],
                check=True,
            )
            # Find python in the virtual environment
            venv_python = venv_path / "bin" / "python"

            # Upgrade pip + install build backend dependencies
            self._cmd_exec_helper(
                [
                    str(venv_python),
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "pip",
                    "build",
                    "setuptools",
                    "wheel",
                ],
                check=True,
            )

            # Install the project itself (reads pyproject.toml)
            self._cmd_exec_helper(
                [str(venv_python), "-m", "pip", "install", "-U", f"{self.project_dir}"],
                cwd=self.project_dir,
                check=True,
            )
            # Install the project dependencies
            self.virtualenv = venv_path

        if self.using_codeql:
            logger.info(f"(Re-)initializing CodeQL analysis for {self.project_dir}")
            cache_root = self.cache_dir / "codeql" 
            cache_root.mkdir(parents=True, exist_ok=True)
            self.db_path = cache_root / f"{self.project_dir.name}-db"
            self.db_path.mkdir(exist_ok=True)

            checksum_file = self.db_path / ".checksum"
            current_checksum = self._compute_checksum(self.project_dir)

            def is_cache_valid() -> bool:
                if not (self.db_path / "db-python").exists():
                    return False
                if not checksum_file.exists():
                    return False
                return checksum_file.read_text().strip() == current_checksum

            if self.rebuild_analysis or not is_cache_valid():
                logger.info("Creating new CodeQL database...")

                codeql_in_path = shutil.which("codeql")
                if codeql_in_path:
                    self.codeql_bin = Path(codeql_in_path)
                else:
                    self.codeql_bin = CodeQLLoader.download_and_extract_codeql(
                        self.cache_dir / "codeql" / "bin"
                    )

                if not shutil.which(str(self.codeql_bin)):
                    raise FileNotFoundError(
                        f"CodeQL binary not executable: {self.codeql_bin}"
                    )

                cmd = [
                    str(self.codeql_bin),
                    "database",
                    "create",
                    str(self.db_path),
                    f"--source-root={self.project_dir}",
                    "--language=python",
                    "--overwrite",
                ]

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                )
                _, err = proc.communicate()

                if proc.returncode != 0:
                    raise CodeQLExceptions.CodeQLDatabaseBuildException(
                        f"Error building CodeQL database:\n{err.decode()}"
                    )

                checksum_file.write_text(current_checksum)

            else:
                logger.info(f"Reusing cached CodeQL DB at {self.db_path}")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.clear_cache and self.cache_dir.exists():
            logger.info(f"Clearing cache directory: {self.cache_dir}")
            shutil.rmtree(self.cache_dir)

    def analyze(self) -> PyApplication:
        """Return the path to the CodeQL database."""
        symbol_table = self._build_symbol_table()
        call_graph = self._get_call_graph()

    def _compute_checksum(self, root: Path) -> str:
        """Compute SHA256 checksum of all Python source files in a project directory. If somethings changes, the
        checksum will change and thus the analysis will be redone.

        Args:
            root (Path): Root directory of the project.

        Returns:
            str: SHA256 checksum of all Python files in the project.
        """
        sha256 = hashlib.sha256()
        for py_file in sorted(root.rglob("*.py")):
            sha256.update(py_file.read_bytes())
        return sha256.hexdigest()

    def _build_symbol_table(self) -> Dict[str, PyModule]:
        """Retrieve a symbol table of the whole project."""
        return SymbolTableBuilder(self.project_dir, self.virtualenv).build()

    def _get_call_graph(self) -> Dict[str, Any]:
        """Retrieve call graph from CodeQL database."""
        logger.info("Call graph extraction not yet implemented.")
        return {}
