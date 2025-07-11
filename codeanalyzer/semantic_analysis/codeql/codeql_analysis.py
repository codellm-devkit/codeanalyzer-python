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

"""CodeQL module for analyzing Python code using CodeQL.

This module provides functionality to create and manage CodeQL databases
for Python projects and execute queries against them.
"""

from pathlib import Path
from typing import Union

from networkx import DiGraph
from pandas import DataFrame

from codeanalyzer.semantic_analysis.codeql.codeql_query_runner import CodeQLQueryRunner


class CodeQL:
    """A class for building the application view of a Python application using CodeQL.

    Args:
        project_dir (str or Path): The path to the root of the Python project.

    Attributes:
        db_path (Path): The path to the CodeQL database.
        temp_db (TemporaryDirectory or None): The temporary directory object if a temporary database was created.
    """

    def __init__(self, project_dir: Union[str, Path], db_path: Path) -> None:
        self.project_dir = project_dir
        self.db_path = db_path

    def _build_call_graph(self) -> DiGraph:
        """Builds the call graph of the application.

        Returns:
            DiGraph: A directed graph representing the call graph of the application.
        """
        query = []

        # Add import
        query += ["import python"]

        # Add Call edges between caller and callee and filter to only capture application methods.
        query += [
            "from Method caller, Method callee",
            "where",
            "caller.fromSource() and",
            "callee.fromSource() and",
            "caller.calls(callee)",
            "select",
        ]

        # Caller metadata
        query += [
            "caller.getFile().getAbsolutePath(),",
            '"[" + caller.getBody().getLocation().getStartLine() + ", " + caller.getBody().getLocation().getEndLine() + "]", //Caller body slice indices',
            "caller.getQualifiedName(), // Caller's fullsignature",
            "caller.getAModifier(), // caller's method modifier",
            "caller.paramsString(), // caller's method parameter types",
            "caller.getReturnType().toString(),  // Caller's return type",
            "caller.getDeclaringType().getQualifiedName(), // Caller's class",
            "caller.getDeclaringType().getAModifier(), // Caller's class modifier",
        ]

        # Callee metadata
        query += [
            "callee.getFile().getAbsolutePath(),",
            '"[" + callee.getBody().getLocation().getStartLine() + ", " + callee.getBody().getLocation().getEndLine() + "]", //Caller body slice indices',
            "callee.getQualifiedName(), // Caller's fullsignature",
            "callee.getAModifier(), // callee's method modifier",
            "callee.paramsString(), // callee's method parameter types",
            "callee.getReturnType().toString(),  // Caller's return type",
            "callee.getDeclaringType().getQualifiedName(), // Caller's class",
            "callee.getDeclaringType().getAModifier() // Caller's class modifier",
        ]

        query_string = "\n".join(query)

        # Execute the query using the CodeQLQueryRunner context manager
        with CodeQLQueryRunner(self.db_path) as query:
            query_result: DataFrame = query.execute(
                query_string,
                column_names=[
                    # Caller Columns
                    "caller_file",
                    "caller_body_slice_index",
                    "caller_signature",
                    "caller_modifier",
                    "caller_params",
                    "caller_return_type",
                    "caller_class_signature",
                    "caller_class_modifier",
                    # Callee Columns
                    "callee_file",
                    "callee_body_slice_index",
                    "callee_signature",
                    "callee_modifier",
                    "callee_params",
                    "callee_return_type",
                    "callee_class_signature",
                    "callee_class_modifier",
                ],
            )

        # Process the query results into JMethod instances
        callgraph: DiGraph = self.__process_call_edges_to_callgraph(query_result)
        return callgraph

    @staticmethod
    def __process_call_edges_to_callgraph(query_result: DataFrame) -> DiGraph:
        """Processes call edges from query results into a call graph.

        Args:
            query_result (DataFrame): The DataFrame containing call edge information.

        Returns:
            DiGraph: A directed graph representing the call graph of the application.
        """
