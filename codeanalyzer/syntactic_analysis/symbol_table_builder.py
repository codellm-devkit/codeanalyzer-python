import ast
import hashlib
import tokenize
from ast import AST, ClassDef
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional, Union

import jedi
from jedi.api import Script
from jedi.api.project import Project

from codeanalyzer.schema.py_schema import (
    PyCallable,
    PyCallableParameter,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyImport,
    PyModule,
    PySymbol,
    PyVariableDeclaration,
)


class SymbolTableBuilder:
    """A class for building a symbol table for a Python project."""

    def __init__(self, project_dir: Union[Path, str], virtualenv: Union[Path, str, None]) -> None:
        self.project_dir = Path(project_dir)
        if virtualenv is None:
            # If no virtual environment is provided, create a jedi project without an environment.
            self.jedi_project: Project = jedi.Project(path=self.project_dir)
        else:
            # If there is a virtual environment, add its site-packages to sys_path so jedi can find the installed packages.
            self.jedi_project: Project = jedi.Project(
                path=self.project_dir,
                environment_path=Path(virtualenv) / "bin" / "python",
            )

    @staticmethod
    def _infer_type(script: Script, line: int, column: int) -> str:
        """Tries to infer the type at a given position using Jedi."""
        try:
            inference = script.infer(line=line, column=column)
            if inference:
                return inference[0].name  # or .full_name
        except Exception:
            pass
        return None

    @staticmethod
    def _infer_qualified_name(script: Script, line: int, column: int) -> Optional[str]:
        """
        Tries to infer the fully qualified name (e.g., os.path.join) at the given position using Jedi.

        Args:
            script (jedi.Script): The Jedi script object.
            line (int): Line number of the expression.
            column (int): Column offset of the expression.

        Returns:
            Optional[str]: The fully qualified name if available, else None.
        """
        try:
            definitions = script.infer(line=line, column=column)
            if definitions:
                return definitions[0].full_name
        except Exception:
            pass
        return None

    def build_pymodule_from_file(self, py_file: Path) -> PyModule:
        """Builds a PyModule from a Python file.

        Args:
            py_file (Path): Path to the python file.

        Returns:
            PyModule object for the input file.
        """
        # Get the raw source code from the file
        source = py_file.read_text(encoding="utf-8")
        
        # Get file metadata for caching
        stat = py_file.stat()
        file_size = stat.st_size
        last_modified = stat.st_mtime
        content_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
        
        # Create a Jedi script for the file
        script: Script = Script(path=str(py_file), project=self.jedi_project)
        module = ast.parse(source, filename=str(py_file))
        
        return (
            PyModule.builder()
            .file_path(str(py_file))
            .module_name(py_file.stem)
            .comments(self._pycomments(module, source))
            .imports(self._imports(module))
            .variables(self._module_variables(module, script))
            .classes(self._add_class(module, script))
            .functions(self._callables(module, script))
            .content_hash(content_hash)
            .last_modified(last_modified)
            .file_size(file_size)
            .build()
        )

    def _imports(self, module: ast.Module) -> List[PyImport]:
        """
        Extracts all import statements from the module.

        Args:
            module (ast.Module): The AST node representing the module.
            script (Script): The Jedi script object for the module.

        Returns:
            List[PyImport]: A list of PyImport objects representing the import statements.
        """
        imports: List[PyImport] = []

        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        PyImport.builder()
                        .module(alias.name)  # for "import os", alias.name = "os"
                        .name(alias.asname or alias.name)  # name in local scope
                        .alias(alias.name if alias.asname else None)
                        .start_line(getattr(node, "lineno", -1))
                        .end_line(getattr(node, "end_lineno", node.lineno))
                        .start_column(getattr(node, "col_offset", -1))
                        .end_column(getattr(node, "end_col_offset", -1))
                        .build()
                    )

            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""  # e.g., from . import x
                for alias in node.names:
                    qualified_module = module_name
                    if node.level:
                        # Handle relative import
                        qualified_module = "." * node.level + (module_name or "")
                    imports.append(
                        PyImport.builder()
                        .module(qualified_module)
                        .name(alias.asname or alias.name)
                        .alias(alias.name if alias.asname else None)
                        .start_line(getattr(node, "lineno", -1))
                        .end_line(getattr(node, "end_lineno", node.lineno))
                        .start_column(getattr(node, "col_offset", -1))
                        .end_column(getattr(node, "end_col_offset", -1))
                        .build()
                    )

        return imports

    def _add_class(self, node: AST, script: Script, prefix: str = "") -> Dict[str, PyClass]:
        classes: Dict[str, PyClass] = {}

        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.ClassDef):
                continue

            class_name = child.name
            start_line = child.lineno
            end_line = getattr(child, "end_lineno", start_line + len(child.body))
            code = ast.unparse(child).strip()

            # Try resolving full signature with Jedi
            if prefix:
                signature = f"{prefix}.{class_name}"
            else:
                try:
                    definitions = script.goto(line=start_line, column=child.col_offset)
                    signature = next(
                        (d.full_name for d in definitions if d.type == "class"),
                        f"{Path(script.path).relative_to(self.project_dir).__str__().replace('/', '.').replace('.py', '')}.{class_name}"
                    )
                except Exception:
                    signature = f"{Path(script.path).relative_to(self.project_dir).__str__().replace('/', '.').replace('.py', '')}.{class_name}"
            py_class = (
                PyClass.builder()
                .name(class_name)
                .signature(signature)
                .start_line(start_line)
                .end_line(end_line)
                .code(code)
                .comments(self._pycomments(child, code))
                .base_classes([
                    ast.unparse(base)
                    for base in child.bases
                    if isinstance(base, ast.expr)
                ])
                .methods(self._callables(child, script, prefix=signature))  # Pass class signature as prefix
                .attributes(self._class_attributes(child, script))
                .inner_classes(self._add_class(child, script, prefix=signature))  # Pass class signature as prefix
                .build()
            )

            classes[signature] = py_class

        return classes


    def _callables(self, node: AST, script: Script, prefix: str = "") -> Dict[str, PyCallable]:
        callables: Dict[str, PyCallable] = {}

        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_name = child.name  # Keep the actual method name unchanged
                start_line = child.lineno
                end_line = getattr(child, "end_lineno", start_line + len(child.body))
                code = ast.unparse(child).strip()
                decorators = [ast.unparse(d) for d in child.decorator_list]
                
                if prefix:
                    # We're in a nested context - build signature with prefix
                    signature = f"{prefix}.{method_name}"
                else:
                    # Top-level function - try Jedi first, fall back to relative path-based
                    try:
                        definitions = script.goto(line=start_line, column=child.col_offset)
                        signature = next(
                            (d.full_name for d in definitions if d.type == "function"),
                            None
                        )
                    except Exception:
                        signature = None
                    
                    # If Jedi didn't provide a signature, build one relative to project_dir
                    if not signature:
                        relative_path = Path(script.path).relative_to(self.project_dir)
                        signature = f"{str(relative_path).replace('/', '.').replace('.py', '')}.{method_name}"
                py_callable = (
                    PyCallable.builder()
                    .name(method_name)  # Use the actual method name, not the full signature
                    .path(str(script.path))
                    .signature(signature)  # Use the full signature here
                    .decorators(decorators)
                    .code(code)
                    .start_line(start_line)
                    .end_line(end_line)
                    .code_start_line(child.body[0].lineno if child.body else start_line)
                    .accessed_symbols(self._accessed_symbols(child, script))
                    .call_sites(self._call_sites(child, script))
                    .local_variables(self._local_variables(child, script))
                    .cyclomatic_complexity(self._cyclomatic_complexity(child))
                    .parameters(self._callable_parameters(child, script))
                    .return_type(
                        ast.unparse(child.returns)
                        if child.returns else self._infer_type(script, child.lineno, child.col_offset)
                    )
                    .comments(self._pycomments(child, code))
                    .inner_callables(self._callables(child, script, signature))  # Pass current signature as prefix
                    .inner_classes(self._add_class(child, script, signature))    # Pass current signature as prefix
                    .build()
                )

                callables[method_name] = py_callable  # Key by method name, not full signature

        return callables
    
    def _pycomments(self, node: ast.AST, source: str) -> List[PyComment]:
        """
        Extracts all PyComment instances (docstring and # comments) from within a specific AST node's body.

        Args:
            node (AST): The AST node (e.g., Module, ClassDef, FunctionDef).
            source (str): Source code of the file.

        Returns:
            List[PyComment]: List of PyComment instances.
        """
        comments: List[PyComment] = []

        # 1. Extract docstring (if any)
        docstring_content = ast.get_docstring(node, clean=False)
        if docstring_content:
            try:
                string_node = node.body[0].value  # type: ignore
                start_line = getattr(string_node, "lineno", getattr(node, "lineno", -1))
                end_line = getattr(string_node, "end_lineno", start_line)
                start_column = getattr(string_node, "col_offset", -1)
                end_column = getattr(
                    string_node, "end_col_offset", start_column + len(docstring_content)
                )
            except Exception:
                start_line = getattr(node, "lineno", -1)
                end_line = getattr(node, "end_lineno", start_line)
                start_column = getattr(node, "col_offset", -1)
                end_column = start_column + len(docstring_content)

            comments.append(
                PyComment.builder()
                .content(docstring_content)
                .start_line(start_line)
                .end_line(end_line)
                .start_column(start_column)
                .end_column(end_column)
                .is_docstring(True)
                .build()
            )

        # 2. Extract # comments scoped within the node's line range
        node_start = getattr(node, "lineno", -1)
        node_end = getattr(node, "end_lineno", node_start)

        tokens = tokenize.generate_tokens(StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                tok_line, tok_col = tok.start
                if node_start <= tok_line <= node_end:
                    comment_text = tok.string.lstrip("#").strip()
                    comments.append(
                        PyComment.builder()
                        .content(comment_text)
                        .start_line(tok_line)
                        .end_line(tok_line)
                        .start_column(tok_col)
                        .end_column(tok_col + len(tok.string))
                        .is_docstring(False)
                        .build()
                    )

        return comments

    def _class_attributes(
        self, ast_node: ast.AST, script: Script
    ) -> Dict[str, PyClassAttribute]:
        """
        Extracts class attributes from the class definition.

        Args:
            ast_node (AST): The AST node representing the class.
            script (Script): The Jedi script object for the module.

        Returns:
            Dict[str, PyClassAttribute]: A dictionary mapping attribute names to their metadata.
        """
        attributes: Dict[str, PyClassAttribute] = {}

        for stmt in ast_node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        attributes[target.id] = (
                            PyClassAttribute.builder()
                            .name(target.id)
                            .type(
                                self._infer_type(
                                    script, target.lineno, target.col_offset
                                )
                            )
                            .start_line(getattr(target, "lineno", -1))
                            .end_line(getattr(stmt, "end_lineno", stmt.lineno))
                            .build()
                        )

            elif isinstance(stmt, ast.AnnAssign):
                target = stmt.target
                if isinstance(target, ast.Name):
                    attributes[target.id] = (
                        PyClassAttribute.builder()
                        .name(target.id)
                        .type(
                            ast.unparse(stmt.annotation)
                            if stmt.annotation
                            else self._infer_type(
                                script, target.lineno, target.col_offset
                            )
                        )
                        .start_line(getattr(target, "lineno", -1))
                        .end_line(getattr(stmt, "end_lineno", stmt.lineno))
                        .build()
                    )
            # We may also encounter `__slots__` in class definitions.
            # This is a special case where attributes are defined in a list or tuple.
            # class Foo:
            #     __slots__ = ('x', 'y')
            #
            # Doing so restricts dynamic attribute assignment.
            # This means that you can do
            # Foo.x = 1
            # Foo.y = 2
            # But, not
            # Foo.z = 3
            elif isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__slots__" for t in stmt.targets
            ):
                if isinstance(stmt.value, (ast.List, ast.Tuple)):
                    for elt in stmt.value.elts:
                        if isinstance(elt, (ast.Str, ast.Constant)):
                            value = elt.s if isinstance(elt, ast.Str) else elt.value
                            attributes[value] = (
                                PyClassAttribute.builder()
                                .name(value)
                                .type("slot")
                                .start_line(getattr(stmt, "lineno", -1))
                                .end_line(getattr(stmt, "end_lineno", stmt.lineno))
                                .build()
                            )

        return attributes

    def _callable_parameters(
        self, fn_node: ast.FunctionDef, script: Script
    ) -> List[PyCallableParameter]:
        """
        Extracts callable parameters from the function definition.
        """

        # Pull full name from Jedi (e.g., mypkg.module.MyClass.my_func)
        try:
            definitions = script.goto(line=fn_node.lineno, column=fn_node.col_offset)
            full_name = next(
                (d.full_name for d in definitions if d.type == "function"), None
            )
        except Exception:
            full_name = None

        class_name = (
            full_name.split(".")[-2] if full_name and "." in full_name else None
        )

        params: List[PyCallableParameter] = []
        args = fn_node.args

        def resolve_type(arg_node: ast.arg) -> Optional[str]:
            if arg_node.annotation:
                return ast.unparse(arg_node.annotation)
            if arg_node.arg in {"self", "cls"} and class_name:
                return class_name
            return self._infer_type(script, arg_node.lineno, arg_node.col_offset)

        def build_param(
            arg_node: ast.arg, default: Optional[ast.expr]
        ) -> PyCallableParameter:
            return (
                PyCallableParameter.builder()
                .name(arg_node.arg)
                .type(resolve_type(arg_node))
                .default_value(ast.unparse(default) if default else None)
                .start_line(getattr(arg_node, "lineno", -1))
                .end_line(
                    getattr(arg_node, "end_lineno", getattr(arg_node, "lineno", -1))
                )
                .start_column(getattr(arg_node, "col_offset", -1))
                .end_column(getattr(arg_node, "end_col_offset", -1))
                .build()
            )

        # Fill out all parameter types
        for arg in getattr(args, "posonlyargs", []):
            params.append(build_param(arg, None))

        default_start = len(args.args) - len(args.defaults)
        for i, arg in enumerate(args.args):
            default = args.defaults[i - default_start] if i >= default_start else None
            params.append(build_param(arg, default))

        if args.vararg:
            params.append(build_param(args.vararg, None))

        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            params.append(build_param(arg, default))

        if args.kwarg:
            params.append(build_param(args.kwarg, None))

        return params

    def _accessed_symbols(
        self, fn_node: ast.FunctionDef, script: Script
    ) -> List[PySymbol]:
        """Analyzes the function body to extract all accessed symbols."""
        symbols = []
        for node in ast.walk(fn_node):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                symbol = self._symbol_from_name_node(
                    node, script, enclosing_scope="local"
                )
                symbols.append(symbol)
        return symbols

    def _call_sites(self, fn_node: ast.FunctionDef, script: Script) -> List[PyCallsite]:
        """
        Finds all call sites made from within the function using Jedi for type inference.

        Args:
            fn_node (ast.FunctionDef): The AST node representing the function.
            script (jedi.Script): The Jedi script object.

        Returns:
            List[PyCallsite]: A list of PyCallsite objects representing each call.
        """
        call_sites: List[PyCallsite] = []

        for node in ast.walk(fn_node):
            if not isinstance(node, ast.Call):
                continue

            func_expr = node.func

            method_name = "<unknown>"
            callee_signature = self._infer_qualified_name(
                script, node.lineno, node.col_offset
            )
            return_type = self._infer_type(script, node.lineno, node.col_offset)

            receiver_expr = None
            receiver_type = None
            if isinstance(func_expr, ast.Attribute):
                receiver_expr = ast.unparse(func_expr.value)
                receiver_type = self._infer_type(
                    script, func_expr.value.lineno, func_expr.value.col_offset
                )
                method_name = func_expr.attr
            elif isinstance(func_expr, ast.Name):
                method_name = func_expr.id

            argument_types = [
                self._infer_type(script, arg.lineno, arg.col_offset)
                or type(arg).__name__
                for arg in node.args
            ]

            call_sites.append(
                PyCallsite.builder()
                .method_name(method_name)
                .receiver_expr(receiver_expr)
                .receiver_type(receiver_type)
                .argument_types(argument_types)
                .return_type(return_type)
                .callee_signature(callee_signature)
                .is_constructor_call(method_name == "__init__")
                .start_line(getattr(node, "lineno", -1))
                .start_column(getattr(node, "col_offset", -1))
                .end_line(getattr(node, "end_lineno", -1))
                .end_column(getattr(node, "end_col_offset", -1))
                .build()
            )

        return call_sites

    def _module_variables(
        self, module: ast.Module, script: Script
    ) -> List[PyVariableDeclaration]:
        """
        Extracts all variable declarations at the module level (excluding functions/classes).
        Includes variables in `if __name__ == "__main__"` blocks.

        Args:
            module (ast.Module): The root module AST.
            script (jedi.Script): For type inference.

        Returns:
            List[PyVariableDeclaration]
        """
        module_vars = []

        def is_nested_in_function_or_class(n: ast.AST) -> bool:
            while hasattr(n, "parent"):
                n = n.parent
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    return True
            return False

        # Add parent pointers (needed for scope check)
        for node in ast.walk(module):
            for child in ast.iter_child_nodes(node):
                child.parent = node  # type: ignore

        for node in ast.walk(module):
            if isinstance(node, ast.Assign):
                if is_nested_in_function_or_class(node):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        module_vars.append(
                            PyVariableDeclaration.builder()
                            .name(target.id)
                            .type(
                                self._infer_type(
                                    script, target.lineno, target.col_offset
                                )
                            )
                            .initializer(
                                ast.unparse(node.value) if node.value else None
                            )
                            .value(None)
                            .scope("module")
                            .start_line(getattr(target, "lineno", -1))
                            .end_line(
                                getattr(node, "end_lineno", getattr(node, "lineno", -1))
                            )
                            .start_column(getattr(target, "col_offset", -1))
                            .end_column(getattr(target, "end_col_offset", -1))
                            .build()
                        )

            elif isinstance(node, ast.AnnAssign):
                if is_nested_in_function_or_class(node):
                    continue
                target = node.target
                if isinstance(target, ast.Name):
                    module_vars.append(
                        PyVariableDeclaration.builder()
                        .name(target.id)
                        .type(
                            ast.unparse(node.annotation)
                            if node.annotation
                            else self._infer_type(script, node.lineno, node.col_offset)
                        )
                        .initializer(ast.unparse(node.value) if node.value else None)
                        .value(None)
                        .scope("module")
                        .start_line(getattr(target, "lineno", -1))
                        .end_line(
                            getattr(node, "end_lineno", getattr(node, "lineno", -1))
                        )
                        .start_column(getattr(target, "col_offset", -1))
                        .end_column(getattr(target, "end_col_offset", -1))
                        .build()
                    )

        return module_vars

    def _local_variables(
        self, fn_node: ast.FunctionDef, script: Script
    ) -> List[PyVariableDeclaration]:
        """
        Extracts all local variables and instance attribute assignments from the function.

        Args:
            fn_node (ast.FunctionDef): The function AST node.
            script (jedi.Script): Jedi script for type inference.

        Returns:
            List[PyVariableDeclaration]: All variables assigned inside this function.
        """
        local_vars: List[PyVariableDeclaration] = []

        for node in ast.walk(fn_node):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    # This one handles simple variable assignments
                    if isinstance(target, ast.Name):
                        local_vars.append(
                            PyVariableDeclaration.builder()
                            .name(target.id)
                            .type(
                                self._infer_type(
                                    script, target.lineno, target.col_offset
                                )
                            )
                            .initializer(
                                ast.unparse(node.value) if node.value else None
                            )
                            .value(None)
                            .scope("function")
                            .start_line(getattr(target, "lineno", -1))
                            .end_line(
                                getattr(node, "end_lineno", getattr(node, "lineno", -1))
                            )
                            .start_column(getattr(target, "col_offset", -1))
                            .end_column(getattr(target, "end_col_offset", -1))
                            .build()
                        )
                    # This handles instance attribute assignments like self.attr = value
                    elif (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        local_vars.append(
                            PyVariableDeclaration.builder()
                            .name(target.attr)
                            .type(
                                self._infer_type(
                                    script, target.lineno, target.col_offset
                                )
                            )
                            .initializer(
                                ast.unparse(node.value) if node.value else None
                            )
                            .value(None)
                            .scope("class")
                            .start_line(getattr(target, "lineno", -1))
                            .end_line(
                                getattr(node, "end_lineno", getattr(node, "lineno", -1))
                            )
                            .start_column(getattr(target, "col_offset", -1))
                            .end_column(getattr(target, "end_col_offset", -1))
                            .build()
                        )

            elif isinstance(node, ast.AnnAssign):
                target = node.target
                annotation_str = (
                    ast.unparse(node.annotation)
                    if node.annotation
                    else self._infer_type(script, node.lineno, node.col_offset)
                )
                initializer_str = ast.unparse(node.value) if node.value else None
                # Annotated local variable: x: int = SOME_VALUE
                if isinstance(target, ast.Name):
                    local_vars.append(
                        PyVariableDeclaration.builder()
                        .name(target.id)
                        .type(annotation_str)
                        .initializer(initializer_str)
                        .value(None)
                        .scope("function")
                        .start_line(getattr(target, "lineno", -1))
                        .end_line(
                            getattr(node, "end_lineno", getattr(node, "lineno", -1))
                        )
                        .start_column(getattr(target, "col_offset", -1))
                        .end_column(getattr(target, "end_col_offset", -1))
                        .build()
                    )
                # Annotated instance attribute: self.attr: int = SOME_VALUE
                elif (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    local_vars.append(
                        PyVariableDeclaration.builder()
                        .name(target.attr)
                        .type(annotation_str)
                        .initializer(initializer_str)
                        .value(None)
                        .scope("class")
                        .start_line(getattr(target, "lineno", -1))
                        .end_line(
                            getattr(node, "end_lineno", getattr(node, "lineno", -1))
                        )
                        .start_column(getattr(target, "col_offset", -1))
                        .end_column(getattr(target, "end_col_offset", -1))
                        .build()
                    )

        return local_vars

    def _cyclomatic_complexity(self, fn_node: ast.FunctionDef) -> int:
        """
        Computes the cyclomatic complexity of a function based on its control flow constructs.

        Args:
            fn_node (ast.FunctionDef): AST node representing the function.

        Returns:
            int: Cyclomatic complexity score (>= 1).
        """
        complexity = 1  # Base path

        for node in ast.walk(fn_node):
            if isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                complexity += 1

            elif isinstance(node, ast.BoolOp):
                # Count 'and' / 'or' as individual decision points
                complexity += len(node.values) - 1

            elif isinstance(node, ast.IfExp):
                # Ternary conditional: x if cond else y
                complexity += 1

            elif isinstance(node, ast.ExceptHandler):
                # Try and catch statement
                complexity += 1

            # TODO: I am also counting 'assert' or 'return' or 'yield' as complexity bumps
            elif isinstance(node, (ast.Assert, ast.Return, ast.Yield, ast.YieldFrom)):
                complexity += 1

        return complexity

    def _symbol_from_name_node(
        self,
        name_node: ast.Name,
        script: Optional[Script] = None,
        enclosing_scope: Optional[str] = None,  # e.g. "function", "class", "module"
    ) -> PySymbol:
        """
        Builds a PySymbol object from a given ast.Name node.

        Args:
            name_node (ast.Name): The AST node representing the variable.
            script (Optional[jedi.Script]): Jedi script for type/scope inference.
            enclosing_scope (Optional[str]): The logical scope the name is inside of.

        Returns:
            PySymbol: A fully built symbol object.
        """
        name = name_node.id
        lineno = getattr(name_node, "lineno", -1)
        col_offset = getattr(name_node, "col_offset", -1)
        is_builtin = name in dir(__builtins__)
        qname = None
        inferred_type = None
        kind = "variable"
        scope = enclosing_scope or "local"

        if script:
            try:
                definitions = script.infer(line=lineno, column=col_offset)
                if definitions:
                    d = definitions[0]
                    inferred_type = d.name
                    qname = d.full_name
                    if d.type == "function":
                        kind = "function"
                    elif d.type == "module":
                        kind = "module"
                        scope = "global"
                    elif d.type == "class":
                        kind = "class"
                    elif d.type == "param":
                        kind = "parameter"
            except Exception:
                pass

        return (
            PySymbol.builder()
            .name(name)
            .scope(scope)
            .kind(kind)
            .type(inferred_type)
            .qualified_name(qname)
            .is_builtin(is_builtin)
            .lineno(lineno)
            .col_offset(col_offset)
            .build()
        )
