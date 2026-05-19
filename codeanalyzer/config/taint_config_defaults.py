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

"""Default taint analysis configuration.

Design
------
The generated CodeQL query uses CodeQL's built-in security models as the
primary detection layer — all 20 ``*Customizations`` modules shipped with
``codeql/python-all 7.x`` are imported, covering:

  SQL Injection, Command Injection, Code Injection, Path Traversal,
  Reflected XSS, LDAP Injection, XXE, SSRF, SSTI, Unsafe Deserialization,
  Open Redirect, Log Injection, NoSQL Injection, XPath Injection,
  Tar/Zip Slip, HTTP Header Injection, Cleartext Storage, Cleartext Logging,
  Cookie Injection, Regular Expression Injection (ReDoS).

The patterns defined here are **supplementary** — they extend built-in
coverage with sources that are not modelled by CodeQL's ``RemoteFlowSource``:

Sources not in RemoteFlowSource:
  - ``sys.argv``          — command-line arguments
  - ``input()``           — interactive user input
  - ``os.getenv()``       — environment variables
  - ``os.environ.get()``  — environment variables
  - ``requests.*``        — outbound HTTP responses used as data sources

Sinks:
  - The default sinks list is intentionally empty — all common sinks are
    covered by the built-in CodeQL models.  Add project-specific sinks here
    only when they are NOT covered by the built-ins.

Sanitizers:
  - Common HTML/path/command sanitizers that CodeQL may not model as barriers.

Users can extend or override this configuration via a YAML/JSON file passed
with ``--taint-config``.  All CodeQL patterns must use double-quoted strings.
"""

from codeanalyzer.schema.py_schema import (
    TaintAnalysisConfig,
    TaintSourceConfig,
    TaintSinkConfig,
    TaintSanitizerConfig,
)


def get_default_taint_config() -> TaintAnalysisConfig:
    """Returns the default taint analysis configuration.

    Combines CodeQL's built-in security models (primary) with supplementary
    user-configured patterns for sources/sinks not covered by the built-ins.

    Returns:
        TaintAnalysisConfig: Default configuration
    """

    return TaintAnalysisConfig(
        sources=[
            # --- Sources not covered by CodeQL's RemoteFlowSource ---

            # Command-line arguments
            TaintSourceConfig(
                name="command_line_args",
                description="Command-line arguments via sys.argv",
                pattern='API::moduleImport("sys").getMember("argv")',
                source_type="command_line_argument",
            ),

            # Interactive user input
            TaintSourceConfig(
                name="user_input",
                description="Direct user input via input() function",
                pattern='API::builtin("input").getACall()',
                source_type="user_input",
            ),

            # Environment variables
            TaintSourceConfig(
                name="env_getenv",
                description="Environment variables via os.getenv",
                pattern='API::moduleImport("os").getMember("getenv").getACall()',
                source_type="environment_variable",
            ),
            TaintSourceConfig(
                name="env_environ_get",
                description="Environment variables via os.environ.get",
                pattern='API::moduleImport("os").getMember("environ").getMember("get").getACall()',
                source_type="environment_variable",
            ),

            # Outbound HTTP responses used as data sources (requests library)
            TaintSourceConfig(
                name="requests_get_response",
                description="HTTP GET response body (requests.get().text / .json())",
                pattern='API::moduleImport("requests").getMember("get").getReturn().getMember("text")',
                source_type="http_response",
            ),
            TaintSourceConfig(
                name="requests_post_response",
                description="HTTP POST response body (requests.post().text / .json())",
                pattern='API::moduleImport("requests").getMember("post").getReturn().getMember("text")',
                source_type="http_response",
            ),
        ],

        sinks=[
            # The built-in CodeQL security models (imported in taint_query_generator.py) cover
            # all common sinks: SQL, command, code, path, XSS, LDAP, XXE, SSRF, SSTI,
            # deserialization, open redirect, log injection, NoSQL, XPath, tar/zip slip,
            # HTTP header injection, cleartext storage/logging, cookie injection, ReDoS.
            #
            # Add project-specific sinks here only when they are NOT covered by the built-ins.
        ],

        sanitizers=[
            # HTML / XSS sanitizers
            TaintSanitizerConfig(
                name="html_escape",
                description="HTML escape function (html.escape)",
                pattern='API::moduleImport("html").getMember("escape").getACall()',
                sanitizes=["xss", "template_injection"],
            ),
            TaintSanitizerConfig(
                name="markupsafe_escape",
                description="MarkupSafe Markup() / escape()",
                pattern='API::moduleImport("markupsafe").getMember("escape").getACall()',
                sanitizes=["xss"],
            ),

            # Command injection sanitizers
            TaintSanitizerConfig(
                name="shlex_quote",
                description="Shell argument quoting via shlex.quote",
                pattern='API::moduleImport("shlex").getMember("quote").getACall()',
                sanitizes=["command_injection"],
            ),

            # Path traversal sanitizers
            TaintSanitizerConfig(
                name="os_path_normpath",
                description="Path normalization via os.path.normpath",
                pattern='API::moduleImport("os").getMember("path").getMember("normpath").getACall()',
                sanitizes=["path_traversal"],
            ),
            TaintSanitizerConfig(
                name="os_path_abspath",
                description="Absolute path resolution via os.path.abspath",
                pattern='API::moduleImport("os").getMember("path").getMember("abspath").getACall()',
                sanitizes=["path_traversal"],
            ),
            TaintSanitizerConfig(
                name="pathlib_resolve",
                description="Path resolution via pathlib.Path.resolve()",
                pattern='API::moduleImport("pathlib").getMember("Path").getReturn().getMember("resolve").getACall()',
                sanitizes=["path_traversal"],
            ),
        ],

        # Analysis options
        max_path_length=10,
        include_implicit_flows=False,
        confidence_threshold="medium",
        exclude_files=[],
        exclude_functions=[],
        include_safe_flows=False,
        group_by_vulnerability=True,
    )
