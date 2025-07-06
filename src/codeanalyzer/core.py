import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Union, Optional
from loguru import logger

from codeanalyzer.schema.schema import PyApplication, PyModule
from codeanalyzer.semantics.codeql import CodeQLLoader
from codeanalyzer.semantics.codeql.codeql_exceptions import CodeQLDatabaseBuildException
from codeanalyzer.syntactics.symbol_table_builder import SymbolTableBuilder


class AnalyzerCore:
    """Core functionality for CodeQL analysis.

    Args:
        project_dir (Union[str, Path]): The root directory of the project to analyze.
        using_codeql (bool): Whether to use CodeQL for analysis.
        rebuild_analysis (bool): Whether to force rebuild the database.
        clear_cache (bool): Whether to delete the cached DB after analysis.
        analysis_depth (int): Depth of analysis (reserved for future use).
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        using_codeql: bool,
        rebuild_analysis: bool,
        clear_cache: bool,
        analysis_depth: int,
    ) -> None:
        self.analysis_depth = analysis_depth
        self.project_dir = Path(project_dir).resolve()
        self.using_codeql = using_codeql
        self.rebuild_analysis = rebuild_analysis
        self.clear_cache = clear_cache

        self.db_path: Optional[Path] = None
        self.codeql_bin: Optional[Path] = None

    def __enter__(self) -> "AnalyzerCore":
        if not self.using_codeql:
            return self

        logger.info(f"Initializing CodeQL analysis for {self.project_dir}")
        cache_root = Path.home() / ".codeanalyzer" / "cache"
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
                    Path.home() / ".codeanalyzer" / "bin"
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
                raise CodeQLDatabaseBuildException(
                    f"Error building CodeQL database:\n{err.decode()}"
                )

            checksum_file.write_text(current_checksum)

        else:
            logger.info(f"Reusing cached CodeQL DB at {self.db_path}")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.clear_cache and self.db_path and self.db_path.exists():
            logger.info(f"Cleaning up analysis artifacts at {self.db_path}")
            shutil.rmtree(self.db_path, ignore_errors=True)

    def analyze(self) -> Optional[Path]:
        """Return the path to the CodeQL database."""
        py_application = PyApplication(
            symbol_table=self._build_symbol_table(), call_graph=self._get_call_graph()
        )
        return py_application.symbol_table

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
        return SymbolTableBuilder(self.project_dir).build()

    def _get_call_graph(self) -> Dict[str, Any]:
        """Retrieve call graph from CodeQL database."""
        logger.info("Call graph extraction not yet implemented.")
        return {}
