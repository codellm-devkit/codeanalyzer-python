import ast
from pathlib import Path
import sys
from typing import Dict
from ipdb import set_trace
import jedi
from loguru import logger
from jedi.api.project import Project
from codeanalyzer.schema.builders import PyClassBuilder, PyModuleBuilder
from codeanalyzer.schema.py_schema import PyClass, PyModule


class SymbolTableBuilder:
    """A class for building a symbol table for a Python project."""

    def __init__(self, project_dir: Path | str, virtualenv: Path | str | None) -> None:
        self.project_dir = Path(project_dir)
        if virtualenv is None:
            # If no virtual environment is provided, create a jedi project without an environment.
            self.jedi_project: Project = jedi.Project(path=self.project_dir)
        else:
            # If there is a virtual environment, add its site-packages to sys_path so jedi can find the installed packages.
            self.jedi_project: Project = jedi.Project(path=self.project_dir, environment_path=Path(virtualenv) / "bin" / "python")

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

    def _build_module(self, py_file: Path) -> PyModule:
        """Builds a PyModule from a Python file.

        Args:
            py_file (Path): Path to the python file.

        Returns:
            PyModule object for the input file.
        """
        source = py_file.read_text(encoding="utf-8")
        script = self.jedi_project.get_script(str(py_file))
        tree = ast.parse(source, filename=str(py_file))

        module_builder = PyModule.builder().file_path(str(py_file)).module_name(py_file.stem)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                module_builder.classes(self._build_class(node, script))
            
        return module_builder.build()
    
    def _build_class(self, class_node: ast.ClassDef, script: jedi.api.Script) -> Dict[str, PyClass]:
        """Builds a PyClass from a class definition node.

        Args:
            class_node (ast.ClassDef): The AST node representing the class.
            script (jedi.api.Script): The Jedi script object for the module.

        Returns:
            PyModule object representing the class.
        """

        class_builder = PyClass.builder()\
            .name(class_node.name)\
            .signature(f"{script.path}.{class_node.name}")\
            .positions(class_node.lineno, getattr(class_node, 'end_lineno', -1))\
            .docstring(self._build_pycomment(docstring, is_docstring=True)
                if (docstring := ast.get_docstring(class_node))
                else None)\
            .base_classes([ast.unparse(base) for base in class_node.bases if isinstance(base, ast.expr)])\
            .methods(self._build_callables(class_node, script))\
            
    
    def _build_pycomment(self, docstring: str, is_docstring: bool = False) -> str:
        """Builds a PyComment from a docstring.

        Args:
            docstring (str): The docstring to convert.
            is_docstring (bool): Whether the docstring is a class or function docstring.

        Returns:
            str: The formatted comment.
        """
        pass

    def _build_pycomment(self, docstring: str, is_docstring: bool = False) -> str:
        """Builds a PyComment from a docstring.

        Args:
            docstring (str): The docstring to convert.
            is_docstring (bool): Whether the docstring is a class or function docstring.

        Returns:
            str: The formatted comment.
        """
        if is_docstring:
            return f'"""{docstring}"""'
        return f"# {docstring}"