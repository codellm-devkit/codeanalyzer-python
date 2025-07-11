################################################################################
# Copyright IBM Corporation 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Python schema models module.

This module defines the data models used to represent Python code structures
for static analysis purposes.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from typing_extensions import Literal
from pydantic import BaseModel

import inspect


def builder(cls):
    """
    Decorator that generates a builder class for a Pydantic models defined below.

    It creates methods like:
        - with_<fieldname>(value)
        - build() to instantiate the model

    It supports nested builder patterns and is mypy-compatible.
    """
    cls_name = cls.__name__
    builder_name = f"{cls_name}Builder"

    # Get type hints and default values for the fields in the model.
    # For example, {file_path: Path, module_name: str, imports: List[PyImport], ...}
    annotations = cls.__annotations__
    # Get default values for the fields in the model.
    defaults = {
        f.name: f.default
        for f in inspect.signature(cls).parameters.values()
        if f.default is not inspect.Parameter.empty
    }
    # Create a namespace for the builder class.
    namespace = {}

    # Create an __init__ method for the builder class that initializes all fields to their default values.
    def __init__(self):
        for field in annotations:
            default = defaults.get(field, None)
            setattr(self, f"_{field}", default)

    namespace["__init__"] = __init__

    # Iterate over all fields in the model and create a method for each field that sets the value and returns the builder instance.
    # This allows for method chaining. The method name will be "<fieldname>".
    for field, field_type in annotations.items():

        def make_method(f=field, t=field_type):
            def method(self, value):
                setattr(self, f"_{f}", value)
                return self

            method.__name__ = f"with_{f}"
            method.__annotations__ = {"value": t, "return": builder_name}
            method.__doc__ = f"Set {f} ({t.__name__})"
            return method

        namespace[f"with_{field}"] = make_method()

    # Create a build method that constructs the model instance using the values set in the builder.
    def build(self):
        return cls(**{k: getattr(self, f"_{k}") for k in annotations})

    # Add the build method to the namespace.
    namespace["build"] = build

    # Assemble the builder class dynamically
    builder_cls = type(builder_name, (object,), namespace)
    # Attach the builder class to the original class as an attribute so we can now call `MyModel.builder().name(...)`.
    setattr(cls, "builder", builder_cls)
    return cls


@builder
class PyImport(BaseModel):
    """Represents a Python import statement.

    Attributes:
        module (str): The name of the module being imported.
        name (str): The name of the imported entity (e.g., function, class).
        alias (Optional[str]): An optional alias for the imported entity.
        start_line (int): The line number where the import statement starts.
        end_line (int): The line number where the import statement ends.
        start_column (int): The starting column of the import statement.
        end_column (int): The ending column of the import statement.

    Example:
        - import numpy as np will be represented as:
            PyImport(module="numpy", name="np", alias="np", start_line=1, end_line=1, start_column=0, end_column=16)
        - from math import sqrt will be represented as:
            PyImport(module="math", name="sqrt", alias=None, start_line=2, end_line=2, start_column=0, end_column=20
        - from os.path import join as path_join will be represented as:
            PyImport(module="os.path", name="path_join", alias="join", start_line=3, end_line=3, start_column=0, end_column=30)
    """

    module: str
    name: str
    alias: Optional[str] = None
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


@builder
class PyComment(BaseModel):
    """
    Represents a Python comment.

    Attributes:
        content (str): The actual comment string (without the leading '#').
        start_line (int): The line number where the comment starts.
        end_line (int): The line number where the comment ends (same as start_line for single-line comments).
        start_column (int): The starting column of the comment.
        end_column (int): The ending column of the comment.
        is_docstring (bool): Whether this comment is actually a docstring (triple-quoted string).
    """

    content: str
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1
    is_docstring: bool = False


@builder
class PySymbol(BaseModel):
    """
    Represents a symbol used or declared in Python code.

    Attributes:
        name (str): The name of the symbol (e.g., 'x', 'self.x', 'os.path').
        scope (Literal['local', 'nonlocal', 'global', 'class', 'module']): The scope where the symbol is accessed.
        kind (Literal['variable', 'parameter', 'attribute', 'function', 'class', 'module']): The kind of symbol.
        type (Optional[str]): Inferred or annotated type, if available.
        qualified_name (Optional[str]): Fully qualified name (e.g., 'self.x', 'os.path.join').
        is_builtin (bool): Whether this is a Python builtin.
        lineno (int): Line number where the symbol is accessed or declared.
        col_offset (int): Column offset.
    """

    name: str
    scope: Literal["local", "nonlocal", "global", "class", "module"]
    kind: Literal["variable", "parameter", "attribute", "function", "class", "module"]
    type: Optional[str] = None
    qualified_name: Optional[str] = None
    is_builtin: bool = False
    lineno: int = -1
    col_offset: int = -1


@builder
class PyVariableDeclaration(BaseModel):
    """Represents a Python variable declaration.

    Attributes:
    """

    name: str
    type: Optional[str]
    initializer: Optional[str] = None
    value: Optional[Any] = None
    scope: Literal["module", "class", "function"] = "module"
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


@builder
class PyCallableParameter(BaseModel):
    """Represents a parameter of a Python callable (function/method).

    Attributes:
        name (str): The name of the parameter.
        type (str): The type of the parameter.
        default_value (str): The default value of the parameter, if any.
        start_line (int): The line number where the parameter is defined.
        end_line (int): The line number where the parameter definition ends.
        start_column (int): The column number where the parameter starts.
        end_column (int): The column number where the parameter ends.
    """

    name: str
    type: Optional[str] = None
    default_value: Optional[str] = None
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


@builder
class PyCallsite(BaseModel):
    """
    Represents a Python call site (function or method invocation) with contextual metadata.
    """

    method_name: str
    receiver_expr: Optional[str] = None
    receiver_type: Optional[str] = None
    argument_types: List[str] = []
    return_type: Optional[str] = None
    callee_signature: Optional[str] = None
    is_constructor_call: bool = False
    start_line: int = -1
    start_column: int = -1
    end_line: int = -1
    end_column: int = -1


@builder
class PyCallable(BaseModel):
    """Represents a Python callable (function/method).

    Attributes:
        name (str): The name of the callable.
        signature (str): The fully qualified name of the callable (e.g., module.function_name).
        docstring (PyComment): The docstring of the callable.
        decorators (List[str]): List of decorators applied to the callable.
        parameters (List[PyCallableParameter]): List of parameters for the callable.
        return_type (Optional[str]): The type of the return value, if specified.
        code (str): The actual code of the callable.
        start_line (int): The line number where the callable is defined.
        end_line (int): The line number where the callable definition ends.
        code_start_line (int): The line number where the code block starts.
        accessed_symbols (List[str]): Symbols accessed within the callable.
        call_sites (List[str]): Call sites of this callable.
        is_entrypoint (bool): Whether this callable is an entry point.
        local_variables (List[PyVariableDeclaration]): Local variables within the callable.
        cyclomatic_complexity (int): Cyclomatic complexity of the callable.
    """

    name: str
    path: str
    signature: str  # e.g., module.<class_name>.function_name
    comments: List[PyComment] = []
    decorators: List[str] = []
    parameters: List[PyCallableParameter] = []
    return_type: Optional[str] = None
    code: str = None
    start_line: int = -1
    end_line: int = -1
    code_start_line: int = -1
    accessed_symbols: List[PySymbol] = []
    call_sites: List[PyCallsite] = []
    local_variables: List[PyVariableDeclaration] = []
    cyclomatic_complexity: int = 0

    def __hash__(self) -> int:
        """Generate a hash based on the callable's signature."""
        return hash(self.signature)


@builder
class PyClassAttribute(BaseModel):
    """Represents a Python class attribute.

    Attributes:
        name (str): The name of the attribute.
        type (str): The type of the attribute.
        docstring (PyComment): The docstring of the attribute.
        start_line (int): The line number where the attribute is defined.
        end_line (int): The line number where the attribute definition ends.
    """

    name: str
    type: Optional[str] = None
    comments: List[PyComment] = []
    start_line: int = -1
    end_line: int = -1


@builder
class PyClass(BaseModel):
    """Represents a Python class.

    Attributes:
        name (str): The name of the class.
        signature (str): The fully qualified name of the class (e.g., module.class_name).
        docstring (PyComment): The docstring of the class.
        base_classes (List[str]): List of base class names.
        methods (Dict[str, PyCallable]): Mapping of method names to their callable representations.
        attributes (Dict[str, PyClassAttribute]): Mapping of attribute names to their variable declarations.
        inner_classes (Dict[str, "PyClass"]): Mapping of inner class names to their class representations.
        start_line (int): The line number where the class definition starts.
        end_line (int): The line number where the class definition ends.
    """

    name: str
    signature: str  # e.g., module.class_name
    comments: List[PyComment] = []
    code: str = None
    base_classes: List[str] = []
    methods: Dict[str, PyCallable] = {}
    attributes: Dict[str, PyClassAttribute] = {}
    inner_classes: Dict[str, "PyClass"] = {}
    start_line: int = -1
    end_line: int = -1

    def __hash__(self):
        """Generate a hash based on the class's signature."""
        return hash(self.signature)


@builder
class PyModule(BaseModel):
    """Represents a Python module.

    Attributes:
        file_path (str): The file path of the module.
        module_name (str): The name of the module (e.g., module.submodule).
        imports (List[PyImport]): List of import statements in the module.
        comments (List[PyComment]): List of comments in the module.
        classes (Dict[str, PyClass]): Mapping of class names to their class representations.
        functions (Dict[str, PyCallable]): Mapping of function names to their callable representations.
        variables (List[PyVariableDeclaration]): List of variable declarations in the module.
    """

    file_path: str
    module_name: str
    imports: List[PyImport] = []
    comments: List[PyComment] = []
    classes: Dict[str, PyClass] = {}
    functions: Dict[str, PyCallable] = {}
    variables: List[PyVariableDeclaration] = []


@builder
class PyApplication(BaseModel):
    """Represents a Python application.

    Attributes:
        name (str): The name of the application.
        version (str): The version of the application.
        description (str): A brief description of the application.
    """

    symbol_table: dict[Path, PyModule]
