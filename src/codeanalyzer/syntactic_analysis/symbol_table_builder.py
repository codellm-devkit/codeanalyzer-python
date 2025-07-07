from pathlib import Path
from typing import Dict

import jedi
from loguru import logger

from codeanalyzer.schema.py_schema import PyModule


class SymbolTableBuilder:
    """A class for building a symbol table for a Python project."""

    def __init__(self, project_dir: Path | str) -> None:
        """
        Args:
            project_dir (Path): The path to the root of the Python project.
        """
        self.project_dir = Path(project_dir)
        self.jedi_project = jedi.Project(
            path=self.project_dir, sys_path=[str(self.project_dir)]
        )

    def build(self) -> Dict[str, PyModule]:
        """Builds the symbol table for the project.

        This method scans the project directory, identifies Python files,
        and constructs a symbol table containing information about classes,
        functions, and variables defined in those files.
        """
        symbol_table: Dict[str, PyModule] = {}
        for py_file in self.project_dir.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
            try:
                py_module: PyModule = self._build_module(py_file)
                symbol_table.update({py_module.signature: py_module})
            except Exception as e:
                logger.error(f"Failed to process {py_file}: {e}")
                continue

    @staticmethod
    def _build_module(py_file: Path) -> PyModule:
        """Builds a PyModule from a Python file.

        Args:
            py_file (Path): Path to the python file.

        Returns:
            PyModule object for the input file.
        """
        logger.info("Implementation with jedi/asteroid goes here...")
