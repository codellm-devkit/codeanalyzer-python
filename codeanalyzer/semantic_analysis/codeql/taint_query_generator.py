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
    TaintNodeRef,
    TaintSourceConfig,
    TaintSinkConfig,
    TaintSanitizerConfig,
)


class TaintQueryGenerator:
    """Generates CodeQL queries from taint analysis configuration."""

    # Built-in CodeQL sink models included in the generated query by default.
    # Each dict has: class (CodeQL class expression), sink_type, severity,
    # vulnerability_type, and comment (used as inline documentation in the query).
    # Individual entries can be suppressed via TaintAnalysisConfig.disabled_builtin_sinks.
    BUILTIN_SINKS: List[dict] = [
        {"class": "SqlInjection::Sink",             "sink_type": "sql_execution",    "severity": "critical", "vulnerability_type": "SQL Injection",                          "comment": "sqlite3, psycopg2, SQLAlchemy, Django ORM raw, …"},
        {"class": "CommandInjection::Sink",         "sink_type": "command_execution","severity": "critical", "vulnerability_type": "Command Injection",                      "comment": "subprocess.*, os.system, os.popen, …"},
        {"class": "CodeInjection::Sink",            "sink_type": "code_execution",   "severity": "critical", "vulnerability_type": "Code Injection",                         "comment": "eval, exec, compile, …"},
        {"class": "PathInjection::Sink",            "sink_type": "file_access",      "severity": "high",     "vulnerability_type": "Path Traversal",                         "comment": "open, os.path.*, pathlib.Path.open, …"},
        {"class": "ReflectedXss::Sink",             "sink_type": "template_rendering","severity": "high",    "vulnerability_type": "Cross-Site Scripting (XSS)",             "comment": "Flask/Django template rendering, …"},
        {"class": "LdapInjection::DnSink",          "sink_type": "ldap_query",       "severity": "high",     "vulnerability_type": "LDAP Injection",                         "comment": "LDAP DN component"},
        {"class": "LdapInjection::FilterSink",      "sink_type": "ldap_query",       "severity": "high",     "vulnerability_type": "LDAP Injection",                         "comment": "LDAP filter component"},
        {"class": "Xxe::Sink",                      "sink_type": "xml_parsing",      "severity": "high",     "vulnerability_type": "XML External Entity (XXE)",              "comment": "XML parsers with external entity expansion"},
        {"class": "ServerSideRequestForgery::Sink", "sink_type": "ssrf_request",     "severity": "high",     "vulnerability_type": "Server-Side Request Forgery (SSRF)",     "comment": "outbound HTTP requests with user-controlled URL"},
        {"class": "TemplateInjection::Sink",        "sink_type": "template_rendering","severity": "critical","vulnerability_type": "Server-Side Template Injection (SSTI)",  "comment": "render_template_string, Jinja2 Environment.from_string, …"},
        {"class": "UnsafeDeserialization::Sink",    "sink_type": "deserialization",  "severity": "critical", "vulnerability_type": "Unsafe Deserialization",                 "comment": "pickle.loads, yaml.load, …"},
        {"class": "UrlRedirect::Sink",              "sink_type": "url_redirect",     "severity": "medium",   "vulnerability_type": "Open Redirect",                          "comment": "redirect(), HttpResponseRedirect, …"},
        {"class": "LogInjection::Sink",             "sink_type": "log_output",       "severity": "medium",   "vulnerability_type": "Log Injection",                          "comment": "logging.*, structlog, …"},
        {"class": "NoSqlInjection::StringSink",     "sink_type": "nosql_query",      "severity": "high",     "vulnerability_type": "NoSQL Injection",                        "comment": "MongoDB/Redis string queries"},
        {"class": "NoSqlInjection::DictSink",       "sink_type": "nosql_query",      "severity": "high",     "vulnerability_type": "NoSQL Injection",                        "comment": "MongoDB dict/object queries"},
        {"class": "XpathInjection::Sink",           "sink_type": "xpath_query",      "severity": "high",     "vulnerability_type": "XPath Injection",                        "comment": "lxml, ElementTree XPath expressions"},
        {"class": "TarSlip::Sink",                  "sink_type": "file_access",      "severity": "high",     "vulnerability_type": "Tar/Zip Slip",                           "comment": "tarfile.extract, zipfile.extractall, …"},
        {"class": "HttpHeaderInjection::Sink",      "sink_type": "http_header",      "severity": "medium",   "vulnerability_type": "HTTP Header Injection",                  "comment": "Response.headers, …"},
        {"class": "CookieInjection::Sink",          "sink_type": "cookie_write",     "severity": "medium",   "vulnerability_type": "Cookie Injection",                       "comment": "set_cookie, …"},
        {"class": "PolynomialReDoS::Sink",          "sink_type": "regex_execution",  "severity": "medium",   "vulnerability_type": "Regular Expression Injection (ReDoS)",   "comment": "re.match/search/fullmatch with user-supplied pattern"},
    ]

    @classmethod
    def builtin_sink_count(cls) -> int:
        """Number of built-in CodeQL sink models always active in the generated query."""
        return len(cls.BUILTIN_SINKS)

    @classmethod
    def builtin_sink_names(cls) -> List[str]:
        """All built-in sink class names (usable in ``disabled_builtin_sinks``)."""
        return [s["class"] for s in cls.BUILTIN_SINKS]

    @staticmethod
    def generate_query(config: TaintAnalysisConfig) -> str:
        """Generate complete taint analysis CodeQL query from configuration.

        The query combines CodeQL's built-in security models with any
        user-configured patterns and/or explicit call-site locations from
        ``TaintSourceConfig.locations``, ``TaintSinkConfig.locations``, and
        ``TaintSanitizerConfig.locations``.

        For focused queries (pinned to specific call sites), callers should
        build a ``TaintAnalysisConfig`` whose source/sink entries use the
        ``locations`` field — typically via
        ``CodeQL._build_source_configs()`` / ``CodeQL._build_sink_configs()``.

        Args:
            config: Taint analysis configuration.

        Returns:
            str: Complete CodeQL query ready for execution
        """
        query_parts = [
            TaintQueryGenerator._generate_header(),
            TaintQueryGenerator._generate_imports(),
            TaintQueryGenerator._generate_source_predicate(config),
            TaintQueryGenerator._generate_sink_predicate(config),
        ]

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

        Module names verified against codeql/python-all 7.1.0:
          - SqlInjectionCustomizations              → module SqlInjection { class Sink }
          - CommandInjectionCustomizations          → module CommandInjection { class Sink }
          - CodeInjectionCustomizations             → module CodeInjection { class Sink }
          - PathInjectionCustomizations             → module PathInjection { class Sink }
          - ReflectedXSSCustomizations              → module ReflectedXss { class Sink }
          - LdapInjectionCustomizations             → module LdapInjection { class DnSink, FilterSink }
          - XxeCustomizations                       → module Xxe { class Sink }
          - ServerSideRequestForgeryCustomizations  → module ServerSideRequestForgery { class Sink }
          - TemplateInjectionCustomizations         → module TemplateInjection { class Sink }
          - UnsafeDeserializationCustomizations     → module UnsafeDeserialization { class Sink }
          - UrlRedirectCustomizations               → module UrlRedirect { class Sink }
          - LogInjectionCustomizations              → module LogInjection { class Sink }
          - NoSqlInjectionCustomizations            → module NoSqlInjection { class StringSink, DictSink }
          - XpathInjectionCustomizations            → module XpathInjection { class Sink }
          - TarSlipCustomizations                   → module TarSlip { class Sink }
          - HttpHeaderInjectionCustomizations       → module HttpHeaderInjection { class Sink }
          - CookieInjectionCustomizations           → module CookieInjection { class Sink }
          - PolynomialReDoSCustomizations           → module PolynomialReDoS { class Sink }
          - RemoteFlowSources                       → class RemoteFlowSource

        NOTE: CleartextStorageCustomizations and CleartextLoggingCustomizations are
        intentionally excluded from this unified query. Those modules use SensitiveDataSource
        (passwords, PII) as their built-in source, not RemoteFlowSource. Mixing them into a
        query that uses general user-input sources produces false positives on every
        print()/file.write() that receives user data. They are best used in a dedicated query
        with SensitiveDataSource as the source.
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
import semmle.python.security.dataflow.LdapInjectionCustomizations
import semmle.python.security.dataflow.XxeCustomizations
import semmle.python.security.dataflow.ServerSideRequestForgeryCustomizations
import semmle.python.security.dataflow.TemplateInjectionCustomizations
import semmle.python.security.dataflow.UnsafeDeserializationCustomizations
import semmle.python.security.dataflow.UrlRedirectCustomizations
import semmle.python.security.dataflow.LogInjectionCustomizations
import semmle.python.security.dataflow.NoSqlInjectionCustomizations
import semmle.python.security.dataflow.XpathInjectionCustomizations
import semmle.python.security.dataflow.TarSlipCustomizations
import semmle.python.security.dataflow.HttpHeaderInjectionCustomizations
import semmle.python.security.dataflow.CookieInjectionCustomizations
import semmle.python.security.dataflow.PolynomialReDoSCustomizations
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
    def _pattern_to_default_sink_node(pattern: str) -> str:
        """Sink node for patterns without a specific argument index — matches any tainted argument."""
        if pattern.endswith(".getACall()"):
            base = pattern[:-len(".getACall()")]
            return f"{base}.getACall().getAnArg()"
        return f"{pattern}.asSink()"

    @staticmethod
    def _pattern_to_sanitizer_node(pattern: str) -> str:
        """Convert a pattern string to a DataFlow::Node expression for sanitizers."""
        if pattern.endswith(".getACall()"):
            return pattern
        return f"{pattern}.asSource()"

    # ------------------------------------------------------------------
    # Location clause helpers (shared by source, sink, sanitizer generators)
    # ------------------------------------------------------------------

    @staticmethod
    def _location_source_clause(ref: TaintNodeRef, source_type: str) -> List[str]:
        """QL lines for one location-pinned source clause (without a preceding 'or')."""
        escaped = ref.file_path.replace("\\", "\\\\").replace('"', '\\"')
        conds = [
            f'node.getLocation().getFile().getAbsolutePath() = "{escaped}"',
            f"node.getLocation().getStartLine() = {ref.start_line}",
        ]
        if ref.start_column >= 0:
            conds.append(f"node.getLocation().getStartColumn() = {ref.start_column}")
        conds.append(f'sourceType = "{source_type}"')
        ind = "    "
        return [
            f"  // Location-pinned: {ref.file_path}:{ref.start_line}",
            "  (\n" + ind + (" and\n" + ind).join(conds) + "\n  )",
        ]

    @staticmethod
    def _location_sink_clause(
        ref: TaintNodeRef, sink_type: str, severity: str, vulnerability_type: str
    ) -> List[str]:
        """QL lines for one location-pinned sink clause (without a preceding 'or')."""
        escaped = ref.file_path.replace("\\", "\\\\").replace('"', '\\"')
        conds = [
            f'node.getLocation().getFile().getAbsolutePath() = "{escaped}"',
            f"node.getLocation().getStartLine() = {ref.start_line}",
        ]
        if ref.start_column >= 0:
            conds.append(f"node.getLocation().getStartColumn() = {ref.start_column}")
        conds += [
            f'sinkType = "{sink_type}"',
            f'severity = "{severity}"',
            f'vulnerabilityType = "{vulnerability_type}"',
        ]
        ind = "    "
        return [
            f"  // Location-pinned: {ref.file_path}:{ref.start_line}",
            "  (\n" + ind + (" and\n" + ind).join(conds) + "\n  )",
        ]

    @staticmethod
    def _location_sanitizer_clause(ref: TaintNodeRef) -> List[str]:
        """QL lines for one location-pinned sanitizer clause (without a preceding 'or')."""
        escaped = ref.file_path.replace("\\", "\\\\").replace('"', '\\"')
        conds = [
            f'node.getLocation().getFile().getAbsolutePath() = "{escaped}"',
            f"node.getLocation().getStartLine() = {ref.start_line}",
        ]
        if ref.start_column >= 0:
            conds.append(f"node.getLocation().getStartColumn() = {ref.start_column}")
        ind = "    "
        return [
            f"  // Location-pinned: {ref.file_path}:{ref.start_line}",
            "  (\n" + ind + (" and\n" + ind).join(conds) + "\n  )",
        ]

    # ------------------------------------------------------------------
    # Predicate generators
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_source_predicate(config: "TaintAnalysisConfig") -> str:
        """Generate isSource predicate from configuration.

        Combines (in order):
        1. Built-in ``RemoteFlowSource`` when ``config.include_remote_flow_source``
           is ``True`` (covers all web-framework request inputs).
        2. For each ``TaintSourceConfig`` entry:
           - A pattern-based clause when ``entry.pattern`` is set.
           - One location-pinned clause per ``TaintNodeRef`` in
             ``entry.locations``, with optional column precision.

        When ``config.include_remote_flow_source`` is ``False`` (set
        automatically by the focused-query helpers) only the explicitly
        listed sources are active.
        """
        lines = [
            "predicate isConfiguredSource(DataFlow::Node node, string sourceType) {",
        ]

        first = True
        if config.include_remote_flow_source:
            lines.append("  // Built-in: all web-framework request sources recognised by CodeQL")
            lines.append('  (node instanceof RemoteFlowSource and sourceType = "web_request")')
            first = False

        for source in config.sources:
            if source.pattern:
                if not first:
                    lines.append("  or")
                lines.append(f"  // User-configured: {source.description}")
                node_expr = TaintQueryGenerator._pattern_to_source_node(source.pattern)
                lines.append(f'  (node = {node_expr} and sourceType = "{source.source_type}")')
                first = False
            for ref in source.locations:
                if not first:
                    lines.append("  or")
                lines.extend(TaintQueryGenerator._location_source_clause(ref, source.source_type))
                first = False

        if first:
            lines.append("  none()")

        lines.append("}")
        return "\n".join(lines)

    @classmethod
    def _generate_sink_predicate(cls, config: "TaintAnalysisConfig") -> str:
        """Generate isSink predicate from configuration.

        Combines (in order):
        1. Built-in CodeQL sink classes from ``BUILTIN_SINKS`` unless suppressed
           via ``config.disabled_builtin_sinks``.
        2. For each ``TaintSinkConfig`` entry:
           - A pattern-based clause when ``entry.pattern`` is set.
           - One location-pinned clause per ``TaintNodeRef`` in
             ``entry.locations``, with optional column precision.
        """
        disabled = set(config.disabled_builtin_sinks)
        active_builtins = [s for s in cls.BUILTIN_SINKS if s["class"] not in disabled]

        lines = [
            "predicate isConfiguredSink(DataFlow::Node node, string sinkType, string severity, string vulnerabilityType) {",
        ]

        first = True
        for sink in active_builtins:
            if not first:
                lines.append("  or")
            lines.append(f"  // Built-in: {sink['vulnerability_type']} ({sink['comment']})")
            lines.append(f"  (node instanceof {sink['class']} and")
            lines.append(
                f'   sinkType = "{sink["sink_type"]}" and severity = "{sink["severity"]}"'
                f' and vulnerabilityType = "{sink["vulnerability_type"]}")'
            )
            first = False

        for sink in config.sinks:
            if sink.pattern:
                if not first:
                    lines.append("  or")
                lines.append(f"  // User-configured: {sink.description}")
                if sink.argument_index is not None:
                    node_expr = TaintQueryGenerator._pattern_to_sink_node(sink.pattern, sink.argument_index)
                else:
                    node_expr = TaintQueryGenerator._pattern_to_default_sink_node(sink.pattern)
                lines.extend([
                    "  (",
                    f'    node = {node_expr} and',
                    f'    sinkType = "{sink.sink_type}" and',
                    f'    severity = "{sink.severity}" and',
                    f'    vulnerabilityType = "{sink.vulnerability_type}"',
                    "  )",
                ])
                first = False
            for ref in sink.locations:
                if not first:
                    lines.append("  or")
                lines.extend(TaintQueryGenerator._location_sink_clause(
                    ref, sink.sink_type, sink.severity, sink.vulnerability_type
                ))
                first = False

        if first:
            lines.append("  none()")

        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def _generate_sanitizer_predicate(sanitizers: List[TaintSanitizerConfig]) -> str:
        """Generate isConfiguredSanitizer predicate from configuration.

        For each ``TaintSanitizerConfig`` entry:
        - A pattern-based clause when ``entry.pattern`` is set.
        - One location-pinned clause per ``TaintNodeRef`` in ``entry.locations``.
        """
        lines = [
            "predicate isConfiguredSanitizer(DataFlow::Node node) {",
        ]

        first = True
        for sanitizer in sanitizers:
            if sanitizer.pattern:
                if not first:
                    lines.append("  or")
                lines.append(f"  // {sanitizer.description}")
                node_expr = TaintQueryGenerator._pattern_to_sanitizer_node(sanitizer.pattern)
                lines.append(f"  node = {node_expr}")
                first = False
            for ref in sanitizer.locations:
                if not first:
                    lines.append("  or")
                lines.extend(TaintQueryGenerator._location_sanitizer_clause(ref))
                first = False

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
