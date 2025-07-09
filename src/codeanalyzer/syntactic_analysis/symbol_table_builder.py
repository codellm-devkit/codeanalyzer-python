from pathlib import Path
import sys
from typing import Dict, List, Optional
import astor
from ipdb import set_trace
import jedi
from loguru import logger
from jedi.api.project import Project
from codeanalyzer.schema.py_schema import (
    PyCallable,
    PyCallableParameter,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyModule,
    PySymbol,
    PyVariableDeclaration,
)
import ast
from ast import AST, ClassDef


class SymbolTableBuilder:
    """A class for building a symbol table for a Python project."""

    def __init__(self, project_dir: Path | str, virtualenv: Path | str | None) -> None:
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
                py_module: PyModule = self._module(py_file)
                symbol_table.update({py_module.signature: py_module})
            except Exception as e:
                logger.error(f"Failed to process {py_file}: {e}")
                continue

    @staticmethod
    def _infer_type(script: jedi.api.Script, line: int, column: int) -> str:
        """Tries to infer the type at a given position using Jedi."""
        try:
            inference = script.infer(line=line, column=column)
            if inference:
                return inference[0].name  # or .full_name
        except Exception:
            pass
        return None

    @staticmethod
    def _infer_qualified_name(
        script: jedi.api.Script, line: int, column: int
    ) -> Optional[str]:
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

    def _module(self, py_file: Path) -> PyModule:
        """Builds a PyModule from a Python file.

        Args:
            py_file (Path): Path to the python file.

        Returns:
            PyModule object for the input file.
        """
        source = py_file.read_text(encoding="utf-8")
        script = self.jedi_project.get_script(str(py_file))
        tree = ast.parse(source, filename=str(py_file))

        module_builder = (
            PyModule.builder().file_path(str(py_file)).module_name(py_file.stem)
        )

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ClassDef):
                module_builder.classes.update(self._class(node, script))

        return module_builder.build()

    def _class(
        self, class_node: ast.ClassDef, script: jedi.api.Script
    ) -> Dict[str, PyClass]:
        """Builds a PyClass from a class definition node.

        Args:
            class_node (ast.ClassDef): The AST node representing the class.
            script (jedi.api.Script): The Jedi script object for the module.

        Returns:
            Dict[str, PyClass]: Mapping of class name to PyClass object.
        """
        # Try resolving full signature with Jedi
        try:
            definitions = script.goto(line=class_node.lineno, column=class_node.col_offset)
            signature = next((d.full_name for d in definitions if d.type == "class"), f"{script.path}.{class_node.name}")
        except Exception:
            signature = f"{script.path}.{class_node.name}"

        py_class = (
            PyClass.builder()
            .name(class_node.name)
            .signature(signature)
            .start_line(class_node.lineno)
            .end_line(getattr(class_node, "end_lineno", class_node.lineno + len(class_node.body)))
            .docstring(self._pycomment(class_node))
            .code(astor.to_source(class_node).strip())
            .base_classes([
                ast.unparse(base)
                for base in class_node.bases
                if isinstance(base, ast.expr)
            ])
            .methods(self._callables(class_node, script))
            .attributes(self._class_attributes(class_node, script))
            .inner_classes({
                child.name: self._class(child, script)
                for child in class_node.body
                if isinstance(child, ast.ClassDef)
            })
            .build()
        )

        return {signature: py_class}

    def _callables(
        self, node: AST, script: jedi.api.Script
    ) -> Dict[str, PyCallable]:
        """
        Builds PyCallable objects from any AST node that may contain functions.

        Args:
            node (AST): The AST node to process (e.g., Module, ClassDef, FunctionDef).
            script (jedi.api.Script): The Jedi script object for the module.

        Returns:
            Dict[str, PyCallable]: A dictionary mapping function/method names to PyCallable objects.
        """
        callables: Dict[str, PyCallable] = {}
        module_path: str = script.path or "<unknown_module>"
        module_name: str = Path(module_path).stem if module_path else "<unknown>"

        def visit(n: AST, class_prefix: str = ""):
            for child in ast.iter_child_nodes(n):
                if isinstance(child, ast.FunctionDef):
                    method_name = child.name
                    start_line = child.lineno
                    end_line = getattr(
                        child, "end_lineno", start_line + len(child.body)
                    )
                    code_start_line = child.body[0].lineno if child.body else start_line
                    code = astor.to_source(child).strip()
                    decorators = [ast.unparse(d) for d in child.decorator_list]

                    try:
                        definitions = script.goto(
                            line=start_line, column=child.col_offset
                        )
                    except Exception:
                        definitions = []

                    signature = next(
                        (d.full_name for d in definitions if d.type == "function"),
                        f"{module_name}.{class_prefix}{method_name}",
                    )

                    callables[method_name] = (
                        PyCallable.builder()
                        .name(method_name)
                        .signature(signature)
                        .decorators(decorators)
                        .code(code)
                        .start_line(start_line)
                        .end_line(end_line)
                        .code_start_line(code_start_line)
                        .accessed_symbols(self._accessed_symbols(child, script))
                        .call_sites(self._call_sites(child, script))
                        .local_variables(self._local_variables(child))
                        .cyclomatic_complexity(
                            self._cyclomatic_complexity(child)
                        )
                        .parameters(self._callable_parameters(child))
                        .return_type(
                            ast.unparse(child.returns)
                            if child.returns
                            else self._infer_type(
                                script, child.lineno, child.col_offset
                            )
                        )
                        .docstring(self._pycomment(child))
                        .build()
                    )

                    visit(child, class_prefix + method_name + ".")

                elif isinstance(child, ast.ClassDef):
                    visit(child, class_prefix + child.name + ".")

                elif hasattr(child, "body"):
                    visit(child, class_prefix)

        visit(node)
        return callables

    def _pycomment(self, node: ast.AST) -> Optional[PyComment]:
        """
        Builds a PyComment from the docstring of a function, class, or module, if present.

        Args:
            node (AST): The AST node.

        Returns:
            PyComment or None
        """
        content = ast.get_docstring(node, clean=False)
        if not content:
            return None  # No docstring present

        # The docstring node is guaranteed to be node.body[0].value
        try:
            string_node = node.body[0].value
            start_line = getattr(string_node, "lineno", getattr(node, "lineno", -1))
            end_line = getattr(string_node, "end_lineno", start_line)
            start_column = getattr(string_node, "col_offset", -1)
            end_column = getattr(string_node, "end_col_offset", start_column + len(content))
        except Exception:
            # Fallback in weird cases
            start_line = getattr(node, "lineno", -1)
            end_line = getattr(node, "end_lineno", start_line)
            start_column = getattr(node, "col_offset", -1)
            end_column = start_column + len(content)

        return (
            PyComment.builder()
            .content(content)
            .start_line(start_line)
            .end_line(end_line)
            .start_column(start_column)
            .end_column(end_column)
            .is_docstring(True)
            .build()
        )


    def _class_attributes(
        self, ast_node: ast.AST, script: jedi.api.Script
    ) -> Dict[str, PyClassAttribute]:
        """
        Extracts class attributes from the class definition.

        Args:
            ast_node (AST): The AST node representing the class.
            script (jedi.api.Script): The Jedi script object for the module.

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
            # Means that you can only do
            # Foo.x = 1
            # Foo.y = 2
            # and not Foo.z = 3
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
        self, fn_node: ast.FunctionDef, script: jedi.api.Script
    ) -> List[PyCallableParameter]:
        """
        Extracts callable parameters from the function definition.
        """

        # Pull full name from Jedi (e.g., mypkg.module.MyClass.my_func)
        try:
            definitions = script.goto(line=fn_node.lineno, column=fn_node.col_offset)
            full_name = next((d.full_name for d in definitions if d.type == "function"), None)
        except Exception:
            full_name = None

        class_name = full_name.split(".")[-2] if full_name and "." in full_name else None

        params: List[PyCallableParameter] = []
        args = fn_node.args

        def resolve_type(arg_node: ast.arg) -> Optional[str]:
            if arg_node.annotation:
                return ast.unparse(arg_node.annotation)
            if arg_node.arg in {"self", "cls"} and class_name:
                return class_name
            return self._infer_type(script, arg_node.lineno, arg_node.col_offset)

        def build_param(arg_node: ast.arg, default: Optional[ast.expr]) -> PyCallableParameter:
            return (
                PyCallableParameter.builder()
                .name(arg_node.arg)
                .type(resolve_type(arg_node))
                .default_value(ast.unparse(default) if default else None)
                .start_line(getattr(arg_node, "lineno", -1))
                .end_line(getattr(arg_node, "end_lineno", getattr(arg_node, "lineno", -1)))
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
        self, fn_node: ast.FunctionDef, script: jedi.api.Script
    ) -> List[str]:
        """Analyzes the function body to extract all accessed symbols."""
        symbols = []
        for node in ast.walk(fn_node):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                symbol = self._symbol_from_name_node(
                    node, script, enclosing_scope="function"
                )
                symbols.append(symbol)
        return symbols

    def _call_sites(
        self, fn_node: ast.FunctionDef, script: jedi.api.Script
    ) -> List[PyCallsite]:
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
                .is_public(False)
                .is_protected(False)
                .is_private(False)
                .is_unspecified(True)
                .is_static_call(False)
                .is_constructor_call(method_name == "__init__")
                .crud_operation(None)
                .crud_query(None)
                .start_line(getattr(node, "lineno", -1))
                .start_column(getattr(node, "col_offset", -1))
                .end_line(getattr(node, "end_lineno", -1))
                .end_column(getattr(node, "end_col_offset", -1))
                .build()
            )

        return call_sites

    def _local_variables(
        self, fn_node: ast.FunctionDef, script: jedi.api.Script
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
                complexity += 1

            # I am also counting 'assert' or 'return' or 'yield' as complexity bumps
            elif isinstance(node, (ast.Assert, ast.Return, ast.Yield, ast.YieldFrom)):
                complexity += 1

        return complexity

    def _symbol_from_name_node(
        self,
        name_node: ast.Name,
        script: Optional[jedi.api.Script] = None,
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
