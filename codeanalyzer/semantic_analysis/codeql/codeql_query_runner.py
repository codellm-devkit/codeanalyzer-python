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

"""Backend module for CodeQL query execution.

This module provides functionality to run CodeQL queries against CodeQL databases
and process the results.
"""

import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import List

import pandas as pd
from pandas import DataFrame

from codeanalyzer.semantic_analysis.codeql.codeql_exceptions import CodeQLExceptions


class CodeQLQueryRunner:
    """A class for executing CodeQL queries against a CodeQL database.

    This class provides a context manager interface for executing CodeQL queries
    and handling temporary resources needed during query execution.

    Args:
        database_path (str): The path to the CodeQL database.
        codeql_bin (str | Path | None): Absolute path to the CodeQL CLI
            binary. When ``None``, falls back to whatever ``codeql`` is on
            ``PATH``.

    Attributes:
        database_path (Path): The path to the CodeQL database.
        codeql_bin (str): Resolved binary path or the literal ``"codeql"``.
        temp_file_path (Path): The path to the temporary query file.
        csv_output_file (Path): The path to the CSV output file.
        temp_bqrs_file_path (Path): The path to the temporary bqrs file.
        temp_qlpack_file (Path): The path to the temporary qlpack file.

    Raises:
        CodeQLQueryExecutionException: If there is an error executing the query.
    """

    def __init__(self, database_path: str, codeql_bin=None, codeql_packs_dir=None):
        self.database_path: Path = Path(database_path)
        self.codeql_bin: str = str(codeql_bin) if codeql_bin else "codeql"
        self.codeql_packs_dir = (
            Path(codeql_packs_dir) if codeql_packs_dir is not None else None
        )
        self.temp_file_path: Path = None

    def __enter__(self):
        """Context entry that prepares paths to execute a CodeQL query.

        The ``.ql`` file is written **inside the prepared qlpack
        directory** (``codeql_packs_dir``) so ``import python`` resolves
        against that pack's installed dependencies — no
        ``--additional-packs`` or ``--search-path`` needed. The CSV /
        BQRS output files live in ``tempfile`` because they're transient
        per-query artifacts.
        """
        # CSV and BQRS files are transient per-query — fine in /tmp.
        csv_file = tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv")
        bqrs_file = tempfile.NamedTemporaryFile("w", delete=False, suffix=".bqrs")
        self.csv_output_file = Path(csv_file.name)
        self.temp_bqrs_file_path = Path(bqrs_file.name)
        csv_file.close()
        bqrs_file.close()

        # The .ql file MUST live inside the prepared qlpack so its
        # ``import python`` resolves via that pack's lock file. Writing
        # outside the pack means CodeQL falls back to a default
        # search-path that doesn't include downloaded library packs.
        if self.codeql_packs_dir is None:
            raise RuntimeError(
                "CodeQLQueryRunner requires codeql_packs_dir — the directory "
                "of an installed qlpack that depends on codeql/python-all."
            )
        ql_file = tempfile.NamedTemporaryFile(
            "w", delete=False, suffix=".ql", dir=str(self.codeql_packs_dir)
        )
        self.temp_file_path = Path(ql_file.name)
        ql_file.close()

        return self

    def execute(self, query_string: str, column_names: List[str]) -> DataFrame:
        """Writes the query to the temporary file and executes it against the specified CodeQL database.

        Args:
            query_string (str): The CodeQL query string to be executed.
            column_names (List[str]): The list of column names for the CSV the CodeQL produces when we execute the query.

        Returns:
            dict: A dictionary containing the resulting DataFrame.

        Raises:
            RuntimeError: If the context manager is not entered using the 'with' statement.
            CodeQLQueryExecutionException: If there is an error executing the query.
        """
        if not self.temp_file_path:
            raise RuntimeError("CodeQLQueryRunner not entered using 'with' statement.")

        # Write the query to the temp file so we can execute it.
        self.temp_file_path.write_text(query_string)

        # The .ql file sits inside the qlpack directory whose lock file
        # already resolves ``codeql/python-all`` and its transitive
        # dependencies. ``codeql query run`` auto-discovers the enclosing
        # qlpack — no extra flags required.
        codeql_query_cmd = shlex.split(
            f"{shlex.quote(self.codeql_bin)} query run {self.temp_file_path} "
            f"--database={self.database_path} "
            f"--output={self.temp_bqrs_file_path}",
            posix=False,
        )

        call = subprocess.Popen(
            codeql_query_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        _, err = call.communicate()
        if call.returncode != 0:
            raise CodeQLExceptions.CodeQLQueryExecutionException(
                f"Error executing query: {(err or b'').decode(errors='replace')}"
            )

        # Convert the bqrs file to a CSV file
        bqrs2csv_command = shlex.split(
            f"{shlex.quote(self.codeql_bin)} bqrs decode --format=csv --output={self.csv_output_file} {self.temp_bqrs_file_path}",
            posix=False,
        )

        # Read the CSV file content and cast it to a DataFrame

        call = subprocess.Popen(
            bqrs2csv_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        _, err = call.communicate()
        if call.returncode != 0:
            raise CodeQLExceptions.CodeQLQueryExecutionException(
                f"Error decoding bqrs: {(err or b'').decode(errors='replace')}"
            )
        else:
            return pd.read_csv(
                self.csv_output_file,
                header=None,
                names=column_names,
                skiprows=[0],
            )

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up resources used by the CodeQL analysis.

        Args:
            exc_type: The exception type if an exception was raised in the context, otherwise None.
            exc_val: The exception instance if an exception was raised in the context, otherwise None.
            exc_tb: The traceback if an exception was raised in the context, otherwise None.

        Note:
            Deletes the temporary files created during the analysis, including the temporary file path,
            the CSV output file, and the temporary QL pack file.
        """
        if self.temp_file_path and self.temp_file_path.exists():
            self.temp_file_path.unlink()

        if self.csv_output_file and self.csv_output_file.exists():
            self.csv_output_file.unlink()

        if self.temp_bqrs_file_path and self.temp_bqrs_file_path.exists():
            self.temp_bqrs_file_path.unlink()
