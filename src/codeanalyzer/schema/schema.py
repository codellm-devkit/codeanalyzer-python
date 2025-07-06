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

from typing import Any, Dict, List, Optional
from typing_extensions import Literal
from pydantic import BaseModel


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
    signature: str  # e.g., module.<class_name>.function_name
    docstring: PyComment = None
    decorators: List[str] = []
    parameters: List[PyCallableParameter] = []
    return_type: Optional[str] = None
    code: str = None
    start_line: int = -1
    end_line: int = -1
    code_start_line: int = -1
    accessed_symbols: List[str] = []
    call_sites: List[str] = []
    is_entrypoint: bool = False
    local_variables: List[PyVariableDeclaration] = []
    cyclomatic_complexity: int = 0

    def __hash__(self) -> int:
        """Generate a hash based on the callable's signature."""
        return hash(self.signature)


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
    type: str = None
    docstring: PyComment = None
    start_line: int = -1
    end_line: int = -1


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
    docstring: PyComment = None
    base_classes: List[str] = []
    methods: Dict[str, PyCallable] = {}
    attributes: Dict[str, PyClassAttribute] = {}
    inner_classes: Dict[str, "PyClass"] = {}
    start_line: int = -1
    end_line: int = -1

    def __hash__(self):
        """Generate a hash based on the class's signature."""
        return hash(self.signature)


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


class PyApplication(BaseModel):
    """Represents a Python application.

    Attributes:
        name (str): The name of the application.
        version (str): The version of the application.
        description (str): A brief description of the application.
    """

    symbol_table: dict[str, PyModule]
    # TODO: Implement call graph extraction
    call_graph: List[Any] | None = None
