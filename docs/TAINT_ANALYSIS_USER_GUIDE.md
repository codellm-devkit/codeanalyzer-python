# Taint Analysis User Guide

Taint analysis (analysis level 3) tracks untrusted data from entry points
(**sources**) through the application to dangerous call sites (**sinks**),
reporting each path as a security vulnerability. It is powered by CodeQL and
requires the CodeQL CLI to be installed.

---

## Table of Contents

1. [Quick start](#quick-start)
2. [How it works](#how-it-works)
3. [Built-in coverage](#built-in-coverage)
4. [Configuration modes](#configuration-modes)
5. [Configuration file reference](#configuration-file-reference)
6. [Writing patterns](#writing-patterns)
7. [Output format](#output-format)
8. [Programmatic API](#programmatic-api)
9. [Troubleshooting](#troubleshooting)

---

## Quick start

```bash
# Analyse a project with all built-in defaults
codeanalyzer -i ./myproject -a 3 --codeql

# Extend defaults with project-specific sources/sinks
codeanalyzer -i ./myproject -a 3 --codeql --taint-config taint.yaml

# Use only your own config, no built-in defaults
codeanalyzer -i ./myproject -a 3 --codeql --taint-config taint.yaml --no-taint-defaults
```

---

## How it works

The analysis generates a CodeQL query from three layers:

1. **Built-in sources** — CodeQL's `RemoteFlowSource` class, which
   automatically recognises all web-framework request inputs (Flask, Django,
   FastAPI, aiohttp, Tornado, …) without any manual configuration.

2. **Supplementary sources** — Additional sources provided by the default
   configuration or your custom config file (e.g. `sys.argv`, `input()`,
   environment variables).

3. **Sinks** — Two complementary layers:
   - *Built-in CodeQL sinks* — 20 vulnerability-specific sink classes
     (SQL, command injection, path traversal, XSS, SSRF, SSTI, …) that
     cover hundreds of framework APIs automatically. These are **always
     active** unless explicitly suppressed with `disabled_builtin_sinks`.
   - *User-defined sinks* — Project-specific APIs added via config file.

4. **Sanitizers** — Call sites that block taint propagation (HTML escape,
   shell quoting, path normalisation, …).

---

## Built-in coverage

### Default sources (always active)

| Name | What it matches | Source type |
|---|---|---|
| `RemoteFlowSource` (CodeQL) | All web-framework request inputs | `web_request` |
| `command_line_args` | `sys.argv` | `command_line_argument` |
| `user_input` | `input()` | `user_input` |
| `env_getenv` | `os.getenv()` | `environment_variable` |
| `env_environ_get` | `os.environ.get()` | `environment_variable` |
| `requests_get_response` | `requests.get().text` | `http_response` |
| `requests_post_response` | `requests.post().text` | `http_response` |

### Built-in sinks (always active, 20 total)

| CodeQL class | Vulnerability type | Severity |
|---|---|---|
| `SqlInjection::Sink` | SQL Injection | critical |
| `CommandInjection::Sink` | Command Injection | critical |
| `CodeInjection::Sink` | Code Injection | critical |
| `TemplateInjection::Sink` | Server-Side Template Injection (SSTI) | critical |
| `UnsafeDeserialization::Sink` | Unsafe Deserialization | critical |
| `PathInjection::Sink` | Path Traversal | high |
| `ReflectedXss::Sink` | Cross-Site Scripting (XSS) | high |
| `LdapInjection::DnSink` | LDAP Injection | high |
| `LdapInjection::FilterSink` | LDAP Injection | high |
| `Xxe::Sink` | XML External Entity (XXE) | high |
| `ServerSideRequestForgery::Sink` | Server-Side Request Forgery (SSRF) | high |
| `NoSqlInjection::StringSink` | NoSQL Injection | high |
| `NoSqlInjection::DictSink` | NoSQL Injection | high |
| `XpathInjection::Sink` | XPath Injection | high |
| `TarSlip::Sink` | Tar/Zip Slip | high |
| `UrlRedirect::Sink` | Open Redirect | medium |
| `LogInjection::Sink` | Log Injection | medium |
| `HttpHeaderInjection::Sink` | HTTP Header Injection | medium |
| `CookieInjection::Sink` | Cookie Injection | medium |
| `PolynomialReDoS::Sink` | Regular Expression Injection (ReDoS) | medium |

### Default sanitizers (always active)

| Name | What it matches |
|---|---|
| `html_escape` | `html.escape()` |
| `markupsafe_escape` | `markupsafe.escape()` |
| `shlex_quote` | `shlex.quote()` |
| `os_path_normpath` | `os.path.normpath()` |
| `os_path_abspath` | `os.path.abspath()` |
| `pathlib_resolve` | `pathlib.Path.resolve()` |

---

## Configuration modes

| Invocation | What is active |
|---|---|
| No `--taint-config` | Built-in defaults only |
| `--taint-config file.yaml` | Defaults **extended** with `file.yaml` (union) |
| `--taint-config file.yaml --no-taint-defaults` | `file.yaml` only, no defaults |

The third mode lets you constrain the analysis to a specific set of
sources/sinks — for example, when tuning for a particular project or auditing
a single vulnerability class.

---

## Configuration file reference

Configuration files can be YAML (`.yaml` / `.yml`) or JSON (`.json`).
All three top-level sections are optional; omit any section to inherit the
defaults for it (when `--taint-defaults` is active).

```yaml
# Optional global settings
max_path_length: 10           # Maximum taint-path steps (default: 10)
confidence_threshold: medium  # high | medium | low (default: medium)
group_by_vulnerability: true  # Group log output by type (default: true)

# Suppress specific built-in CodeQL sinks (see list above)
disabled_builtin_sinks: []

# Exclude files / functions from analysis
exclude_files: []             # Glob patterns relative to project root
exclude_functions: []         # Fully-qualified function names

# Additional sources, sinks, sanitizers (see sections below)
sources: []
sinks: []
sanitizers: []
```

### `sources[]`

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique identifier used in logs and deduplication |
| `description` | string | yes | Human-readable explanation |
| `pattern` | string | yes | CodeQL API-graph expression (see [Writing patterns](#writing-patterns)) |
| `source_type` | string | yes | Label propagated to `PyTaintSource.source_type` in results |
| `enabled` | bool | no | Default `true`; set `false` to temporarily disable |

```yaml
sources:
  - name: redis_get
    description: "Values retrieved from Redis"
    pattern: 'API::moduleImport("redis").getMember("Redis").getInstance().getMember("get").getReturn()'
    source_type: cache_read
```

### `sinks[]`

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique identifier |
| `description` | string | yes | Human-readable explanation |
| `pattern` | string | yes | CodeQL API-graph expression |
| `sink_type` | string | yes | Label propagated to `PyTaintSink.sink_type` in results |
| `vulnerability_type` | string | yes | Vulnerability name reported in results |
| `severity` | string | yes | `critical` \| `high` \| `medium` \| `low` |
| `argument_index` | int | no | Zero-based index of the dangerous argument. When omitted, any tainted argument triggers the sink. |
| `enabled` | bool | no | Default `true` |

```yaml
sinks:
  - name: internal_db_query
    description: "Internal database wrapper"
    pattern: 'API::moduleImport("myapp.db").getMember("query").getACall()'
    sink_type: sql_execution
    vulnerability_type: SQL Injection
    severity: critical
    argument_index: 0   # Only the first argument (the query string) matters
```

Use `argument_index` to avoid false positives when only one specific argument
of a multi-argument call is dangerous. For example, `cursor.execute(query,
params)` — only `query` (index `0`) should be treated as the sink, not
`params`.

### `sanitizers[]`

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique identifier |
| `description` | string | yes | Human-readable explanation |
| `pattern` | string | yes | CodeQL API-graph expression |
| `sanitizes` | list[string] | no | Informational list of mitigated vulnerability types (not used by the query engine) |
| `enabled` | bool | no | Default `true` |

```yaml
sanitizers:
  - name: bleach_clean
    description: "bleach.clean() HTML sanitiser"
    pattern: 'API::moduleImport("bleach").getMember("clean").getACall()'
    sanitizes: [xss]
```

> **Note:** All enabled sanitizers unconditionally block **all** taint flows
> passing through them. The `sanitizes` field is documentation only; per-flow
> sanitisation (blocking only XSS flows, not command injection flows) is not
> yet supported.

### `disabled_builtin_sinks`

Suppress specific built-in CodeQL sink models without removing the rest:

```yaml
disabled_builtin_sinks:
  - PolynomialReDoS::Sink      # too noisy on regex-heavy codebases
  - CookieInjection::Sink
```

To list all available names at runtime:

```bash
python -c "
from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator
print(*TaintQueryGenerator.builtin_sink_names(), sep='\n')
"
```

### Merge behaviour when `--taint-defaults` is active

When a custom config is merged with the defaults:

| Item | Behaviour |
|---|---|
| Sources | Union; custom entry with the same `name` **overrides** the default |
| Sinks | Union; custom entry with the same `name` overrides the default |
| Sanitizers | Union; same override rule |
| `disabled_builtin_sinks` | Union of both lists |
| `exclude_files` / `exclude_functions` | Union of both lists |
| Scalar options (`max_path_length`, `confidence_threshold`, etc.) | Custom value wins |
| Additive booleans (`include_implicit_flows`, `include_safe_flows`) | `OR` — enabling in either config enables globally |

---

## Writing patterns

Patterns are [CodeQL API-graph](https://codeql.github.com/docs/codeql-language-guides/using-the-api-graph-in-python/)
expressions. All string literals inside a pattern **must use double quotes**
(CodeQL does not support single-quoted strings).

### Common building blocks

| Goal | Pattern |
|---|---|
| Module-level function call | `API::moduleImport("os").getMember("system").getACall()` |
| Nested attribute call | `API::moduleImport("os").getMember("path").getMember("join").getACall()` |
| Return value of a call | `API::moduleImport("requests").getMember("get").getReturn()` |
| Attribute of a return value | `API::moduleImport("requests").getMember("get").getReturn().getMember("text")` |
| Built-in function | `API::builtin("input").getACall()` |
| Class instance method | `API::moduleImport("sqlite3").getMember("connect").getReturn().getMember("cursor").getReturn().getMember("execute").getACall()` |

### Source patterns

For sources, the pattern should resolve to the **return value** of the call
(where the untrusted data lives):

```yaml
# input() return value
pattern: 'API::builtin("input").getACall()'

# Flask request argument
pattern: 'API::moduleImport("flask").getMember("request").getMember("args").getMember("get").getACall()'

# Environment variable
pattern: 'API::moduleImport("os").getMember("getenv").getACall()'
```

### Sink patterns

For sinks, the pattern should resolve to the **argument** that carries the
dangerous value. Use `argument_index` to target a specific argument, or omit
it to flag any tainted argument:

```yaml
# Target argument 0 of cursor.execute(query, params)
pattern: 'API::moduleImport("sqlite3").getMember("connect").getReturn().getMember("cursor").getReturn().getMember("execute").getACall()'
argument_index: 0

# Flag any tainted argument (omit argument_index)
pattern: 'API::moduleImport("myapp.shell").getMember("run").getACall()'
```

### Sanitizer patterns

Sanitizer patterns resolve to the **call that produces the safe value**:

```yaml
pattern: 'API::moduleImport("html").getMember("escape").getACall()'
```

---

## Output format

Results are returned as `PyTaintAnalysisResult` (accessible via the library
API or serialised to JSON/msgpack). Each detected flow has this structure:

```json
{
  "flows": [
    {
      "flow_id": "path/to/app.py:10->path/to/app.py:18",
      "vulnerability_type": "SQL Injection",
      "severity": "critical",
      "confidence": "medium",
      "source": {
        "source_type": "user_input",
        "description": "Direct user input via input() function",
        "call_site": {
          "method_name": "input",
          "file_path": "app.py",
          "start_line": 10,
          "end_line": 10,
          "start_column": 8,
          "end_column": 15
        }
      },
      "sink": {
        "sink_type": "sql_execution",
        "description": "SQL Injection",
        "severity": "critical",
        "call_site": {
          "method_name": "execute",
          "file_path": "app.py",
          "start_line": 18,
          "end_line": 18,
          "start_column": 4,
          "end_column": 22
        }
      },
      "path": [
        {
          "location": "app.py:10:8",
          "function_name": "get_user",
          "description": "Source node",
          "step_type": "source"
        },
        {
          "location": "app.py:18:4",
          "function_name": "query_db",
          "description": "Sink node",
          "step_type": "sink"
        }
      ]
    }
  ]
}
```

**Severity levels:**

| Severity | Meaning |
|---|---|
| `critical` | Immediate exploitation likely (SQL/command/code/SSTI/deserialization) |
| `high` | High exploitability (path traversal, XSS, SSRF, XXE, LDAP, NoSQL, …) |
| `medium` | Exploitable under specific conditions (redirect, header injection, ReDoS, …) |
| `low` | Informational / low-impact |

---

## Programmatic API

### Running analysis

```python
from pathlib import Path
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions

options = AnalysisOptions(
    input=Path("/path/to/project"),
    analysis_level=3,
    using_codeql=True,
    taint_config=Path("taint.yaml"),   # optional
    taint_use_defaults=True,           # False = custom only
)

with Codeanalyzer(options) as analyzer:
    result = analyzer.analyze()

taint = result.taint_analysis
print(f"{len(taint.flows)} flows detected")

for flow in taint.flows:
    print(f"[{flow.severity}] {flow.vulnerability_type}")
    print(f"  source: {flow.source.call_site.file_path}:{flow.source.call_site.start_line}")
    print(f"  sink:   {flow.sink.call_site.file_path}:{flow.sink.call_site.start_line}")
```

### Loading and inspecting configuration

```python
from codeanalyzer.config.taint_config_loader import TaintConfigLoader
from codeanalyzer.config.taint_config_defaults import get_default_taint_config
from codeanalyzer.semantic_analysis.codeql.taint_query_generator import TaintQueryGenerator

# Load defaults only
config = TaintConfigLoader.load_config()

# Load custom file, merged with defaults (mode 2)
config = TaintConfigLoader.load_config("taint.yaml", use_defaults=True)

# Load custom file only (mode 3)
config = TaintConfigLoader.load_config("taint.yaml", use_defaults=False)

# Inspect what is active
print(f"Sources:   {len(config.sources)}")
print(f"User sinks:{len(config.sinks)}")
print(f"Built-in sinks: {TaintQueryGenerator.builtin_sink_count()}")
print(f"Disabled built-ins: {config.disabled_builtin_sinks}")
print(f"Sanitizers:{len(config.sanitizers)}")

# All available built-in sink names (for use in disabled_builtin_sinks)
print(TaintQueryGenerator.builtin_sink_names())

# Validate a config and check for problems
issues = TaintConfigLoader.validate_config(config)
for issue in issues:
    print(f"WARNING: {issue}")

# Save current effective config to file (useful for debugging)
TaintConfigLoader.save_config(config, "effective-config.yaml", format="yaml")
```

---

## Troubleshooting

### No flows detected

1. **Check verbosity** — run with `-vv` to see the active config summary and
   which sources/sinks are loaded.
2. **Verify source coverage** — your code may use a web framework already
   covered by `RemoteFlowSource`, or it may use a non-web input not in the
   defaults. Add a custom source for the latter.
3. **Check sanitizers** — a flow that is blocked by a default sanitizer
   (e.g. `html.escape`, `shlex.quote`) will not be reported. Set
   `include_safe_flows: true` temporarily to see sanitised paths.
4. **Check for excluded files** — if `exclude_files` or `exclude_functions`
   is set in a config, those paths are silently skipped.
5. **Confirm CodeQL database** — the CodeQL database is built from the project
   at analysis time. If the database is stale, use `--eager` to rebuild.

### Too many false positives

- Use `disabled_builtin_sinks` to suppress noisy sink classes (e.g.
  `PolynomialReDoS::Sink` on regex-heavy codebases).
- Use `--no-taint-defaults` with a hand-crafted config file to constrain
  analysis to only the flows you care about.
- Use `exclude_files` to skip test or vendor directories.
- Add sanitizer entries for project-specific validation functions.

### Unexpected flows blocked (false negatives)

- Check that the sanitizer pattern actually matches your code — test it by
  temporarily disabling the sanitizer with `enabled: false`.
- CodeQL sanitizers are applied globally. If a sanitizer is too broad (e.g.
  `os.path.normpath` blocking a non-path flow), disable it and add a narrower
  one.

### Config file not loading

- Verify patterns use **double quotes** inside the YAML string. Single quotes
  are a CodeQL syntax error.
- Run `validate_config()` programmatically (see above) to catch empty
  patterns, duplicate names, or missing required fields.
- Check the log output at `-v` level — a `WARNING: Taint config: …` line
  indicates a structural problem found at load time.

### Getting the CodeQL CLI

Taint analysis requires the [CodeQL CLI](https://github.com/github/codeql-cli-binaries/releases).
Download the archive for your platform, unpack it, and ensure the `codeql`
binary is on your `PATH`:

```bash
codeql --version   # should print the CodeQL version
```

The `codeql/python-all` pack is downloaded automatically on first use.
