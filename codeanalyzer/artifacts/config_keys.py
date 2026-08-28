"""Config-key flatteners (#152). Pure text-in/records-out, mirroring
`artifacts/parsers.py`'s idiom: one dispatcher over per-format internals,
never raising.

Namespace dispatch: an env-family basename (`.env`, `.env.*`, `.flaskenv`)
always wins, regardless of the artifact's declared `format`; otherwise the
`format` field selects yaml/json/toml/ini/properties. Any other format
extracts nothing (not a failure -- there's just nothing to flatten).

Span precision differs by shape: env/properties/ini are line-oriented, so
the parse itself knows the exact defining line. yaml/json/toml are
tree-shaped -- span recovery falls back to a best-effort search for the
final dotted-key segment on its own line (see `_find_key_span`), which can
bind the wrong line when the same leaf name recurs at another nesting level,
or find nothing at all (`span=None`) for a minified/single-line file.
"""
from __future__ import annotations

import configparser
import json
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 CI leg
    import tomli as tomllib

import yaml

from codeanalyzer.schema.ids import config_key_id
from codeanalyzer.schema.py_schema import PyArtifact, PyConfigKey, Span, byte_offsets

# A parsed leaf before it becomes a PyConfigKey: (dotted_key, raw_value, span).
_Entry = Tuple[str, object, Optional[Span]]


# --- dotted-path flattening (yaml/json/toml share this over their parsed
# dict/list trees) --------------------------------------------------------

def _flatten(obj, prefix: str = ""):
    """Yield (dotted_key, leaf_value) pairs; numeric segments for arrays
    (e.g. "services.web.ports.0")."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{prefix}.{i}" if prefix else str(i))
    else:
        yield prefix, obj


def _line_span(text: str, lines: List[str], lineno: int) -> Span:
    """Exact span covering `lineno`'s full text (1-based)."""
    line = lines[lineno - 1]
    lo, hi = byte_offsets(text, lineno, 0, lineno, len(line))
    return Span(start=(lineno, 0), end=(lineno, len(line)), bytes=(lo, hi))


_BEST_EFFORT_KEY_TAIL = r'["\']?\s*[:=]'


def _find_key_span(text: str, lines: List[str], last_segment: str) -> Optional[Span]:
    """Best-effort: the FIRST line (in file order) whose stripped-of-leading
    indentation/list-dash content starts with `last_segment` (optionally
    quoted) followed by `:` or `=` -- covers yaml (`key:`), json (`"key":`),
    and toml (`key =`) without per-format branching. Anchored at column 0
    (not a substring search) so it can't latch onto a leaf name that merely
    appears inside a longer token; the cost is that it also can't see keys
    packed onto a single minified line, which is an accepted v1 gap given
    `span` is `Optional`."""
    pattern = re.compile(r'^[\s\-]*["\']?' + re.escape(last_segment) + _BEST_EFFORT_KEY_TAIL)
    for i, line in enumerate(lines, start=1):
        if pattern.match(line):
            return _line_span(text, lines, i)
    return None


def _flatten_structured(data, text: str, lines: List[str]) -> List[_Entry]:
    return [(k, v, _find_key_span(text, lines, k.rsplit(".", 1)[-1])) for k, v in _flatten(data)]


def _parse_yaml(text: str, lines: List[str]) -> List[_Entry]:
    return _flatten_structured(yaml.safe_load(text) or {}, text, lines)


def _parse_json(text: str, lines: List[str]) -> List[_Entry]:
    return _flatten_structured(json.loads(text), text, lines)


def _parse_toml(text: str, lines: List[str]) -> List[_Entry]:
    return _flatten_structured(tomllib.loads(text), text, lines)


# --- env: KEY=value, `#` comments, `export ` prefix, quote stripping -------

_ENV_LINE = re.compile(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')


def _env_value(raw: str) -> str:
    """The text after `KEY=` on one line -> the value. A quoted value ends
    at its MATCHING closing quote -- anything after that (including a `#`)
    is trailing comment and is discarded, so a `#` INSIDE the quotes (e.g. a
    URL fragment) is never reached by comment-stripping. An unquoted value
    ends at the first unescaped `" #"` (whitespace then `#`); a bare `#`
    stuck directly to a token (no preceding whitespace) is not a comment
    marker and stays in the value."""
    raw = raw.strip()
    if raw and raw[0] in "'\"":
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]
    return re.split(r"\s#", raw, maxsplit=1)[0].strip()


def _is_env_family(basename: str) -> bool:
    return basename == ".env" or basename.startswith(".env.") or basename == ".flaskenv"


def _parse_env(text: str, lines: List[str]) -> List[_Entry]:
    out: List[_Entry] = []
    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ENV_LINE.match(stripped)
        if not m:
            continue
        key, value = m.group(1), _env_value(m.group(2))
        out.append((key, value, _line_span(text, lines, lineno)))
    return out


# --- properties: key=value / key: value, `\` continuations, `!`/`#` comments

_PROPS_KV = re.compile(r'^(?P<key>[^=:\s]+)\s*[:=]\s*(?P<value>.*)$')


def _parse_properties(text: str, lines: List[str]) -> List[_Entry]:
    out: List[_Entry] = []
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped or stripped.startswith(("#", "!")):
            i += 1
            continue
        start_lineno = i + 1
        parts = [stripped]
        while parts[-1].endswith("\\") and i + 1 < n:
            parts[-1] = parts[-1][:-1]  # drop just the continuation backslash
            i += 1
            parts.append(lines[i].strip())  # continuation: leading whitespace stripped
        m = _PROPS_KV.match("".join(parts))
        if m:
            out.append((m.group("key").strip(), m.group("value").strip(),
                        _line_span(text, lines, start_lineno)))
        i += 1
    return out


# --- ini: configparser with raw=True (preserve `%(x)s`); exact line via a
# lightweight parallel section/key scan (configparser gives no line numbers,
# and `strict=True` already guarantees no duplicate (section, key) pairs) --

_INI_SECTION = re.compile(r'^\[(?P<name>[^]]+)\]\s*$')
_INI_KEY = re.compile(r'^(?P<key>[^\s#;=:][^=:]*?)\s*[:=]')


def _ini_line_map(lines: List[str]) -> Dict[Tuple[str, str], int]:
    out: Dict[Tuple[str, str], int] = {}
    section: Optional[str] = None
    for lineno, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith((";", "#")):
            continue
        m = _INI_SECTION.match(line)
        if m:
            section = m.group("name")
            continue
        m = _INI_KEY.match(line)
        if m and section is not None:
            out.setdefault((section, m.group("key")), lineno)
    return out


def _parse_ini(text: str, lines: List[str]) -> List[_Entry]:
    cp = configparser.ConfigParser()
    cp.optionxform = str  # preserve on-disk case (also needed for the line-map lookup)
    cp.read_string(text)
    line_map = _ini_line_map(lines)
    out: List[_Entry] = []
    # DEFAULT's own keys, unconditionally: `cp.sections()` never includes
    # "DEFAULT" (configparser convention), so a file with only a [DEFAULT]
    # section would otherwise yield zero keys. `cp.items(section, ...)`
    # below ALSO re-inherits every DEFAULT key into each real section
    # (configparser's fallback-lookup semantics) -- so a key defined only in
    # DEFAULT deliberately appears twice: once as `DEFAULT.<key>` and again
    # as `<section>.<key>` per inheriting section. Both are real, distinct
    # facts (the key is DEFINED in DEFAULT; the section's own resolved
    # value equals it), so both stay -- this is intended duplication, not a
    # bug (see docs/design/specs/2026-08-28-config-key-family-design.md
    # Caveats).
    for key, value in cp.defaults().items():
        lineno = line_map.get(("DEFAULT", key))
        span = _line_span(text, lines, lineno) if lineno else None
        out.append((f"DEFAULT.{key}", value, span))
    for section in cp.sections():
        for key, value in cp.items(section, raw=True):
            lineno = line_map.get((section, key)) or line_map.get(("DEFAULT", key))
            span = _line_span(text, lines, lineno) if lineno else None
            out.append((f"{section}.{key}", value, span))
    return out


_NAMESPACE_PARSERS: Dict[str, Callable[[str, List[str]], List[_Entry]]] = {
    "yaml": _parse_yaml, "json": _parse_json, "toml": _parse_toml,
    "ini": _parse_ini, "properties": _parse_properties,
}


# --- reference recognition ---------------------------------------------

_REF_TEMPLATE = re.compile(r'\$\{\{[^}]*\}\}')
_REF_BRACED = re.compile(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}')
_REF_BARE = re.compile(r'\$[A-Za-z_][A-Za-z0-9_]*')
_REF_PERCENT = re.compile(r'%\([A-Za-z_][A-Za-z0-9_]*\)s')


def _find_references(text: str) -> List[str]:
    """Raw reference tokens in `text`, order of appearance, deduplicated.
    `${{ ... }}` is matched (and masked out of the working copy) FIRST so a
    `${VAR}`/`$VAR` nested inside a template expression is not also counted
    as a standalone reference; `%(name)s` never overlaps a `$`-sigil form so
    it needs no masking."""
    found: List[Tuple[int, str]] = []
    working = text
    for pattern in (_REF_TEMPLATE, _REF_BRACED, _REF_BARE):
        for m in pattern.finditer(working):
            found.append((m.start(), m.group(0)))
        working = pattern.sub(lambda m: " " * len(m.group(0)), working)
    for m in _REF_PERCENT.finditer(text):
        found.append((m.start(), m.group(0)))
    found.sort(key=lambda pair: pair[0])
    seen = set()
    out: List[str] = []
    for _, token in found:
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):  # yaml/json/toml spell it lowercase on disk
        return "true" if value else "false"
    return str(value)


# --- public API -----------------------------------------------------------

def is_config_eligible(artifact: PyArtifact) -> bool:
    """Whether `artifact` is worth extracting config keys from: an env-family
    basename (`.env`/`.env.*`/`.flaskenv`, regardless of declared format), or
    a namespace-bearing format (yaml/json/toml/ini/properties). A binary
    artifact is never eligible -- there is no decodable text to flatten, and
    a rule-matched-but-undecodable file downgrades to `format="binary"`
    regardless of its basename (see discovery.py), so the binary check wins
    even over an env-family name.

    Callers (core.py's wiring) use this to skip the on-disk read + parse
    attempt entirely on artifacts that can never yield config keys, rather
    than relying on `extract_config_keys`'s own not-applicable `([], True)`
    return after already having paid for the read."""
    if artifact.format == "binary":
        return False
    basename = artifact.path.rsplit("/", 1)[-1]
    return _is_env_family(basename) or artifact.format in _NAMESPACE_PARSERS


def extract_config_keys(
    artifact: PyArtifact, full_text: str, capture_value: bool,
) -> Tuple[List[PyConfigKey], bool]:
    """Flatten `artifact`'s config format into `PyConfigKey` records, reading
    `full_text` (the real on-disk text -- never the possibly-truncated
    `artifact.source`).

    Returns `(keys, ok)`: the same two-tuple shape as
    `artifacts.parsers.parse_manifest`'s `(records, partial)`, but the
    OPPOSITE polarity -- here `ok` is `True` on success (including the
    not-applicable case: a format with no flattener yields `([], True)`) and
    `False` only when parsing raised. Never raises: every code path below is
    covered by one `try`/`except`, so a malformed file degrades to `([],
    False)` instead of an exception escaping to the caller. `keys` is always
    sorted by `key` (L1 determinism).

    `value` is populated only when `capture_value` is True; `key`,
    `namespace`, `span`, and `references` are extracted unconditionally
    either way (references are recognized in the raw leaf value regardless
    of whether that value is exposed)."""
    basename = artifact.path.rsplit("/", 1)[-1]
    if _is_env_family(basename):
        namespace, parser = "env", _parse_env
    else:
        parser = _NAMESPACE_PARSERS.get(artifact.format)
        if parser is None:
            return [], True
        namespace = artifact.format

    try:
        lines = full_text.splitlines()
        entries = parser(full_text, lines)
        # Last occurrence wins on a duplicate dotted key (env/properties can
        # legally redefine a key later in the file; a repeat would otherwise
        # collide on `id`, which is derived from `key` alone).
        coalesced: Dict[str, Tuple[object, Optional[Span]]] = {}
        for dotted_key, value, span in entries:
            coalesced[dotted_key] = (value, span)
        keys = [
            PyConfigKey(
                id=config_key_id(artifact.id, dotted_key), key=dotted_key,
                namespace=namespace,
                value=_stringify(value) if capture_value else None,
                span=span, references=_find_references(_stringify(value)),
            )
            for dotted_key, (value, span) in coalesced.items()
        ]
        keys.sort(key=lambda k: k.key)
        return keys, True
    except Exception:
        return [], False
