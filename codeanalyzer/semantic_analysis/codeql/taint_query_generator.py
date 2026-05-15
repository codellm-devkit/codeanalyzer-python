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

"""Dynamic CodeQL query generator for taint analysis.

This module generates CodeQL queries from taint analysis configurations.

Design philosophy
-----------------
CodeQL's ``codeql/python-all`` pack ships comprehensive built-in taint models
via ``semmle.python.security.dataflow.*`` — these cover hundreds of SQL,
command, path-traversal, XSS, and other sinks automatically, without any
manual API enumeration.

The generated query therefore uses **two complementary layers**:

1. **Built-in CodeQL security models** (primary, comprehensive):
   - ``RemoteFlowSource`` — all web-framework request sources (Flask, Django,
     FastAPI, aiohttp, …) recognised by CodeQL out of the box.
   - ``SqlInjection::Sink`` — all DB cursor patterns (sqlite3, psycopg2,
     mysql-connector, SQLAlchemy, …).
   - ``CommandInjection::Sink`` — subprocess, os.system, shlex, …
   - ``CodeInjection::Sink`` — eval, exec, compile, …
   - ``PathTraversal::Sink`` — open(), os.path operations, …
   - ``XSS::Sink`` — Flask/Django template rendering, …

2. **Configurable user-defined patterns** (supplementary):
   Additional sources/sinks/sanitizers supplied via ``TaintAnalysisConfig``
   that extend the built-in coverage with project-specific APIs.

Uses the modern CodeQL Python API (codeql/python-all >= 7.x):
- ``DataFlow::ConfigSig`` interface with ``implements``
- ``TaintTracking::Global<Config>`` module
- ``API::Node.asSource()`` / ``API::Node.getParameter(N).asSink()``
"""

from typing import List
from codeanalyzer.schema.py_schema import (
    TaintAnalysisConfig,
    TaintSourceConfig,
    TaintSinkConfig,
    TaintSanitizerConfig,
)


class TaintQueryGenerator:
    """Generates CodeQL queries from taint analysis configuration."""

    @staticmethod
    def generate_query(config: TaintAnalysisConfig) -> str:
        """Generate complete taint analysis CodeQL query from configuration.

        The query combines CodeQL's built-in security models with any
        user-configured patterns, giving comprehensive coverage without
        requiring exhaustive manual API enumeration.

        Args:
            config: Taint analysis configuration

        Returns:
            str: Complete CodeQL query ready for execution
        """
        query_parts = []

        query_parts.append(TaintQueryGenerator._generate_header())
        query_parts.append(TaintQueryGenerator._generate_imports())
        query_parts.append(TaintQueryGenerator._generate_source_predicate(config.sources))
        query_parts.append(TaintQueryGenerator._generate_sink_predicate(config.sinks))

        if config.sanitizers:
            query_parts.append(TaintQueryGenerator._generate_sanitizer_predicate(config.sanitizers))

        query_parts.append(TaintQueryGenerator._generate_config_sig(
            has_sanitizers=len(config.sanitizers) > 0
        ))
        query_parts.append(TaintQueryGenerator._generate_flow_module())
        query_parts.append(TaintQueryGenerator._generate_helpers())
        query_parts.append(TaintQueryGenerator._generate_main_query())

        return "\n\n".join(query_parts)

    # ------------------------------------------------------------------
    # Header / imports
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_header() -> str:
        """Generate query header with metadata."""
        return """/**
 * @name Configurable Taint Analysis
 * @description Taint analysis combining CodeQL built-in security models with
 *              configurable user-defined sources, sinks, and sanitizers.
 * @kind path-problem
 * @id python/configurable-taint-analysis
 * @problem.severity warning
 */"""

    @staticmethod
    def _generate_imports() -> str:
        """Generate import statements.

        Imports both the core DataFlow/TaintTracking modules and the built-in
        security-sink/source classes from codeql/python-all so that the query
        benefits from CodeQL's comprehensive model library.

        Module names verified against codeql/python-all 7.x:
          - SqlInjectionCustomizations    → module SqlInjection { class Sink }
          - CommandInjectionCustomizations → module CommandInjection { class Sink }
          - CodeInjectionCustomizations   → module CodeInjection { class Sink }
          - PathInjectionCustomizations   → module PathInjection { class Sink }
          - ReflectedXSSCustomizations    → module ReflectedXss { class Sink }
          - RemoteFlowSources             → class RemoteFlowSource
        """
        return """import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.TaintTracking
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.SqlInjectionCustomizations
import semmle.python.security.dataflow.CommandInjectionCustomizations
import semmle.python.security.dataflow.CodeInjectionCustomizations
import semmle.python.security.dataflow.PathInjectionCustomizations
import semmle.python.security.dataflow.ReflectedXSSCustomizations
import semmle.python.dataflow.new.RemoteFlowSources"""

    # ------------------------------------------------------------------
    # Pattern helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pattern_to_source_node(pattern: str) -> str:
        """Convert a pattern string to a DataFlow::Node expression for sources."""
        if pattern.endswith(".getACall()"):
            return pattern
        return f"{pattern}.asSource()"

    @staticmethod
    def _pattern_to_sink_node(pattern: str, argument_index: int) -> str:
        """Convert a pattern string to a DataFlow::Node expression for sinks."""
        if pattern.endswith(".getACall()"):
            api_node = pattern[:-len(".getACall()")]
            return f"{api_node}.getParameter({argument_index}).asSink()"
        return f"{pattern}.getParameter({argument_index}).asSink()"

    @staticmethod
    def _pattern_to_sanitizer_node(pattern: str) -> str:
        """Convert a pattern string to a DataFlow::Node expression for sanitizers."""
        if pattern.endswith(".getACall()"):
            return pattern
        return f"{pattern}.asSource()"

    # ------------------------------------------------------------------
    # Predicate generators
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_source_predicate(sources: List[TaintSourceConfig]) -> str:
        """Generate isSource predicate combining built-in RemoteFlowSource with
        any user-configured sources.

        Built-in ``RemoteFlowSource`` covers all web-framework request inputs
        (Flask ``request.args/form/json``, Django ``request.GET/POST``,
        FastAPI, aiohttp, Tornado, …) recognised by CodeQL's model library.
        User-configured patterns extend this with project-specific sources
        (e.g. ``sys.argv``, ``input()``, custom HTTP clients).
        """
        lines = [
            "predicate isConfiguredSource(DataFlow::Node node, string sourceType) {",
            "  // Built-in: all web-framework request sources recognised by CodeQL",
            "  (node instanceof RemoteFlowSource and sourceType = \"web_request\")",
        ]

        for source in sources:
            lines.append("  or")
            lines.append(f"  // User-configured: {source.description}")
            node_expr = TaintQueryGenerator._pattern_to_source_node(source.pattern)
            lines.append(f"  (node = {node_expr} and sourceType = \"{source.source_type}\")")

        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def _generate_sink_predicate(sinks: List[TaintSinkConfig]) -> str:
        """Generate isSink predicate combining built-in security sinks with
        any user-configured sinks.

        Built-in sink classes from ``codeql/python-all`` cover:
        - ``SqlInjection::Sink``   — sqlite3, psycopg2, mysql-connector,
                                     SQLAlchemy, Django ORM raw queries, …
        - ``CommandInjection::Sink`` — subprocess.*, os.system, os.popen, …
        - ``CodeInjection::Sink``  — eval(), exec(), compile(), …
        - ``PathTraversal::Sink``  — open(), os.path.*, pathlib.Path.open(), …
        - ``XSS::Sink``            — Flask/Django template rendering, …

        User-configured patterns extend this with project-specific sinks.
        """
        lines = [
            "predicate isConfiguredSink(DataFlow::Node node, string sinkType, string severity, string vulnerabilityType) {",
            "  // Built-in: SQL injection sinks (sqlite3, psycopg2, SQLAlchemy, Django ORM raw, …)",
            "  (node instanceof SqlInjection::Sink and",
            "   sinkType = \"sql_execution\" and severity = \"critical\" and vulnerabilityType = \"SQL Injection\")",
            "  or",
            "  // Built-in: Command injection sinks (subprocess.*, os.system, os.popen, …)",
            "  (node instanceof CommandInjection::Sink and",
            "   sinkType = \"command_execution\" and severity = \"critical\" and vulnerabilityType = \"Command Injection\")",
            "  or",
            "  // Built-in: Code injection sinks (eval, exec, compile, …)",
            "  (node instanceof CodeInjection::Sink and",
            "   sinkType = \"code_execution\" and severity = \"critical\" and vulnerabilityType = \"Code Injection\")",
            "  or",
            "  // Built-in: Path injection sinks (open, os.path.*, pathlib.Path.open, …)",
            "  (node instanceof PathInjection::Sink and",
            "   sinkType = \"file_access\" and severity = \"high\" and vulnerabilityType = \"Path Traversal\")",
            "  or",
            "  // Built-in: Reflected XSS sinks (Flask/Django template rendering, …)",
            "  (node instanceof ReflectedXss::Sink and",
            "   sinkType = \"template_rendering\" and severity = \"high\" and vulnerabilityType = \"Cross-Site Scripting (XSS)\")",
        ]

        for sink in sinks:
            lines.append("  or")
            lines.append(f"  // User-configured: {sink.description}")

            if sink.argument_index is not None:
                node_expr = TaintQueryGenerator._pattern_to_sink_node(sink.pattern, sink.argument_index)
            else:
                node_expr = TaintQueryGenerator._pattern_to_source_node(sink.pattern)

            lines.append("  (")
            lines.append(f"    node = {node_expr} and")
            lines.append(f"    sinkType = \"{sink.sink_type}\" and")
            lines.append(f"    severity = \"{sink.severity}\" and")
            lines.append(f"    vulnerabilityType = \"{sink.vulnerability_type}\"")
            lines.append("  )")

        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def _generate_sanitizer_predicate(sanitizers: List[TaintSanitizerConfig]) -> str:
        """Generate isConfiguredSanitizer predicate from configuration."""
        lines = [
            "predicate isConfiguredSanitizer(DataFlow::Node node) {",
        ]

        for i, sanitizer in enumerate(sanitizers):
            if i > 0:
                lines.append("  or")
            lines.append(f"  // {sanitizer.description}")
            node_expr = TaintQueryGenerator._pattern_to_sanitizer_node(sanitizer.pattern)
            lines.append(f"  node = {node_expr}")

        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def _generate_config_sig(has_sanitizers: bool) -> str:
        """Generate DataFlow::ConfigSig module using modern CodeQL API."""
        lines = [
            "private module ConfiguredTaintConfig implements DataFlow::ConfigSig {",
            "  predicate isSource(DataFlow::Node source) {",
            "    isConfiguredSource(source, _)",
            "  }",
            "",
            "  predicate isSink(DataFlow::Node sink) {",
            "    isConfiguredSink(sink, _, _, _)",
            "  }",
        ]

        if has_sanitizers:
            lines.extend([
                "",
                "  predicate isBarrier(DataFlow::Node node) {",
                "    isConfiguredSanitizer(node)",
                "  }",
            ])

        lines.extend([
            "",
            "  predicate observeDiffInformedIncrementalMode() { any() }",
            "}",
        ])

        return "\n".join(lines)

    @staticmethod
    def _generate_flow_module() -> str:
        """Generate TaintTracking::Global module instantiation."""
        return "module ConfiguredTaintFlow = TaintTracking::Global<ConfiguredTaintConfig>;"

    @staticmethod
    def _generate_helpers() -> str:
        """Generate helper functions for extracting metadata."""
        return """string getFunctionName(DataFlow::Node node) {
  result = node.getScope().(Function).getName()
  or
  not exists(node.getScope().(Function)) and result = "<module>"
}

string getQualifiedFunctionName(DataFlow::Node node) {
  exists(Function f |
    f = node.getScope() |
    if exists(f.getScope().(Class)) then
      result = f.getScope().(Class).getName() + "." + f.getName()
    else
      result = f.getName()
  )
  or
  not exists(node.getScope().(Function)) and result = "<module>"
}"""

    @staticmethod
    def _generate_main_query() -> str:
        """Generate main query select statement using modern path-problem API."""
        return """import ConfiguredTaintFlow::PathGraph

from
  ConfiguredTaintFlow::PathNode source,
  ConfiguredTaintFlow::PathNode sink,
  string sourceType,
  string sinkType,
  string severity,
  string vulnerabilityType
where
  ConfiguredTaintFlow::flowPath(source, sink) and
  isConfiguredSource(source.getNode(), sourceType) and
  isConfiguredSink(sink.getNode(), sinkType, severity, vulnerabilityType)
select
  // 1. Element (sink - required for path-problem)
  sink.getNode(),
  // 2. Source path node (required for path-problem)
  source,
  // 3. Sink path node (required for path-problem)
  sink,
  // 4. Message (required for path-problem)
  "Tainted data from " + sourceType + " flows to " + vulnerabilityType,

  // Additional metadata columns
  // Flow ID
  source.getNode().getLocation().getFile().getAbsolutePath() + ":" +
    source.getNode().getLocation().getStartLine().toString() + "->" +
    sink.getNode().getLocation().getFile().getAbsolutePath() + ":" +
    sink.getNode().getLocation().getStartLine().toString(),

  // Source information
  source.getNode().getLocation().getFile().getAbsolutePath(),
  source.getNode().getLocation().getStartLine(),
  source.getNode().getLocation().getEndLine(),
  source.getNode().getLocation().getStartColumn(),
  source.getNode().getLocation().getEndColumn(),
  sourceType,
  source.getNode().toString(),
  getFunctionName(source.getNode()),
  getQualifiedFunctionName(source.getNode()),

  // Sink information
  sink.getNode().getLocation().getFile().getAbsolutePath(),
  sink.getNode().getLocation().getStartLine(),
  sink.getNode().getLocation().getEndLine(),
  sink.getNode().getLocation().getStartColumn(),
  sink.getNode().getLocation().getEndColumn(),
  sinkType,
  severity,
  sink.getNode().toString(),
  getFunctionName(sink.getNode()),
  getQualifiedFunctionName(sink.getNode()),
  vulnerabilityType,
  // Confidence (always medium for configurable analysis)
  "medium" """

    @staticmethod
    def get_column_names() -> List[str]:
        """Get the column names for the query results.

        Column order matches the select statement:
          1. element (sink node - required for path-problem)
          2. source_path (PathNode - required for path-problem)
          3. sink_path (PathNode - required for path-problem)
          4. message (string - required for path-problem)
          5+ additional metadata columns

        Returns:
            List[str]: Column names in the order they appear in the query
        """
        return [
            # Required path-problem columns (positions 1-4)
            "element",
            "source_path",
            "sink_path",
            "message",
            # Additional metadata
            "flow_id",
            # Source columns
            "source_file",
            "source_start_line",
            "source_end_line",
            "source_start_col",
            "source_end_col",
            "source_type",
            "source_expr",
            "source_function",
            "source_qualified_function",
            # Sink columns
            "sink_file",
            "sink_start_line",
            "sink_end_line",
            "sink_start_col",
            "sink_end_col",
            "sink_type",
            "severity",
            "sink_expr",
            "sink_function",
            "sink_qualified_function",
            "vulnerability_type",
            "confidence",
        ]
