"""Config-key flatteners (#152, #165). Pure text-in/records-out, mirroring
`artifacts/parsers.py`'s idiom: one dispatcher over per-format internals,
never raising.

Namespace dispatch: an env-family basename (`.env`, `.env.*`, `.flaskenv`)
always wins, regardless of the artifact's declared `format`; otherwise the
`format` field selects yaml/json/toml/ini/properties/dockerfile. Any other
format extracts nothing (not a failure -- there's just nothing to flatten).

Deployment-env namespaces (#165): a `dockerfile`-format artifact mints TWO
namespaces from one file -- `ENV K=v` directives mint namespace `env` (the
whole point is that `os.environ`/`os.getenv` reads, whose detector rule
prefers namespace `env`, bind to them), `ARG K[=default]` directives mint
namespace `dockerfile` (build-time only, deliberately not bindable by the
env detectors). Both share the bare var name as `key`, so an `ARG X` later
promoted via `ENV X=$X` -- a common idiom -- would collide on `id` (`key`
alone determines it) if both used the plain id shape; the `dockerfile`
mint's id is disambiguated with an internal `arg.` prefix (the `key` FIELD
stays the bare name either way, since nothing about resolution or the
issue's contract cares how the id looks).

A `yaml`-format artifact ALSO gets a supplementary recognition pass after
the normal dotted-path flattening: well-known compose (`services.<name>.
environment` map/list) and k8s (`...env.<idx>.name`/`.value`, matched as a
dotted-path shape at any nesting depth, not schema-anchored) shapes mint
ADDITIONAL namespace-`env` keys keyed on the bare var name, alongside the
normal namespace-`yaml` dotted-path ones -- dual-minting is intentional
(so `os.environ`/`os.getenv` reads bind to compose/k8s-declared vars too),
never deduped away. The `key` FIELD is always the bare var name (matching
`env` namespace's exact-match resolution semantics), but the `id` cannot
reuse that bare name unqualified: a TOP-LEVEL yaml key sharing the same
name as a recognized env var (e.g. a document with both a bare
`COMPOSE_ONLY_KEY:` entry and a `services.web.environment.COMPOSE_ONLY_KEY`
one) would otherwise collide with the plain yaml mint's own bare-key id --
the yaml mint's key is USUALLY a longer dotted path that can't collide, but
not always (a top-level leaf's dotted path IS just its bare name). Same fix
as the dockerfile ARG case: the env-dual-mint's id is disambiguated with an
internal `env.` prefix (`_build_keys`'s `id_key`), the `key` field itself
unaffected. This pass is shape-based, not filename/role gated -- any yaml
artifact whose content happens to match mints the extra keys, matching this
module's general overlay posture (permissive, never a schema validator).

Span precision differs by shape: env/properties/ini/dockerfile are
line-oriented, so the parse itself knows the exact defining line. yaml/
json/toml are tree-shaped -- span recovery falls back to a best-effort
search for the final dotted-key segment on its own line (see
`_find_key_span`), which can bind the wrong line when the same leaf name
recurs at another nesting level, or find nothing at all (`span=None`) for
a minified/single-line file. The compose/k8s env-recognition mint reuses
this same best-effort search keyed on the bare var name: it finds the
defining line for compose's map/list forms (`KEY:`/`KEY=` is the var's own
line) but not k8s's name/value list shape (the var name is a VALUE on a
`name:` line, not a key label there) -- k8s env-mint spans are `None`,
an accepted extension of the existing best-effort gap.
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


# --- compose/k8s env recognition (#165): supplemental namespace="env" mint
# over the SAME flattened (dotted_key, value) pairs the "yaml" namespace
# uses -- see the module docstring for why dual-minting is safe and
# intentional. Shape-matched on the dotted path string, not the yaml tree,
# so both recognizers stay simple regex-over-strings, symmetric with the
# rest of this module's line/path-oriented parsing. -------------------------

_K8S_ENV_NAME = re.compile(r'(?:^|\.)env\.\d+\.name$')
_COMPOSE_ENV = re.compile(r'^services\.[^.]+\.environment\.(.+)$')


def _recognize_env_shapes(flat: List[Tuple[str, object]]) -> List[Tuple[str, object]]:
    """`(key, value)` pairs for every compose/k8s env shape found in `flat`
    (the same `_flatten(data)` pairs the "yaml" namespace flattens) --
    NOT dotted paths, the bare var name, matching `env` namespace semantics
    (`config_use.py` resolves that namespace by exact `key ==` match)."""
    by_path = dict(flat)
    out: List[Tuple[str, object]] = []
    for dotted_key, value in flat:
        if _K8S_ENV_NAME.search(dotted_key):
            sibling = dotted_key[: -len("name")] + "value"  # ...env.<idx>.value
            out.append((_stringify(value), by_path.get(sibling)))
            continue
        m = _COMPOSE_ENV.match(dotted_key)
        if not m:
            continue
        tail = m.group(1)
        if _ENV_KEY_NAME.match(tail):  # map form: tail IS the var name
            out.append((tail, value))
        elif tail.isdigit():  # list form: leaf is "KEY=val" or bare "KEY"
            key, sep, val = _stringify(value).partition("=")
            if _ENV_KEY_NAME.match(key):
                out.append((key, val if sep else None))
    return out


# --- env: KEY=value, `#` comments, `export ` prefix, quote stripping -------

_ENV_LINE = re.compile(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
_ENV_KEY_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')  # shared: dockerfile + compose/k8s recognition


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


# --- dockerfile: `ENV`/`ARG` directives (#165). Line-based, case-insensitive
# instruction keywords (Dockerfile convention is uppercase, the spec itself
# is not case-sensitive); no BuildKit heredoc awareness in v1 -- a heredoc
# body line is just another line that doesn't match `_DOCKER_ENV`/`_DOCKER_ARG`
# and is silently skipped, same as any other unparseable line (overlay
# posture). Multi-stage `FROM ... AS x` scoping is not modeled -- every
# ENV/ARG in the file is scanned regardless of which stage it's in. --------

_DOCKER_ENV = re.compile(r'^ENV\s+(.*)$', re.IGNORECASE)
_DOCKER_ARG = re.compile(r'^ARG\s+(.*)$', re.IGNORECASE)


def _join_continuations(lines: List[str], start_i: int) -> Tuple[str, int]:
    """From `lines[start_i]`, join any backslash-continued following lines
    into one logical instruction line -- same trailing-backslash-drop +
    leading/trailing-whitespace-strip join `_parse_properties` already uses
    for its own continuations. Returns `(joined_text, index of the LAST
    line consumed)`."""
    i, n = start_i, len(lines)
    parts = [lines[i].strip()]
    while parts[-1].endswith("\\") and i + 1 < n:
        parts[-1] = parts[-1][:-1]  # drop just the continuation backslash
        i += 1
        parts.append(lines[i].strip())
    return "".join(parts), i


def _split_ws_respecting_quotes(s: str) -> List[str]:
    r"""Whitespace-split `s`, except inside a matching `'`/`"` span (a quoted
    value may contain spaces) or right after an unquoted `\` -- a backslash
    escapes the next character (`\ ` keeps a literal space in the token
    instead of splitting there, `\\` collapses to one literal backslash),
    mirroring Docker's own shell-style ENV splitting (moby's `Rex\ The\
    Dog` example). A dangling trailing `\` with nothing to escape is kept
    literally rather than raising -- a real trailing continuation backslash
    is already stripped upstream by `_join_continuations`, so this is only
    a defensive fallback. Quote characters stay IN the returned tokens,
    stripped afterward by `_env_value` so there is one quote-stripping
    implementation, not two."""
    tokens: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch == "\\":
            i += 1
            buf.append(s[i] if i < n else ch)
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        tokens.append("".join(buf))
    return tokens


def _dockerfile_env_entries(text: str, lines: List[str]) -> List[_Entry]:
    """`ENV` directives -> `(KEY, value, span)`. Handles `ENV K=v`, multi-key
    `ENV a=1 b=2`, and the legacy single-key `ENV K v` space form (Docker's
    own disambiguation rule: the token right after `ENV` decides the form --
    a `=` in it means one-or-more `key=value` pairs; no `=` means the
    legacy form, where the key is the first word and the REST of the line
    is the value). The legacy form's value is taken VERBATIM -- unlike the
    `key=value` form, real Docker does no quote processing there at all
    (moby's `parseNameVal`), so `ENV NAME "John Doe"` keeps its quotes; the
    key/value separator is general whitespace (a tab is as legal as a
    space), not a literal `" "`."""
    out: List[_Entry] = []
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#") or not _DOCKER_ENV.match(stripped):
            i += 1
            continue
        start_lineno = i + 1
        joined, i = _join_continuations(lines, i)
        rest = _DOCKER_ENV.match(joined).group(1).strip()
        span = _line_span(text, lines, start_lineno)
        first_token = rest.split(None, 1)[0] if rest else ""
        if "=" in first_token:
            for tok in _split_ws_respecting_quotes(rest):
                key, sep, raw_val = tok.partition("=")
                if sep and _ENV_KEY_NAME.match(key):
                    out.append((key, _env_value(raw_val), span))
        else:
            parts = rest.split(None, 1)
            if len(parts) == 2 and _ENV_KEY_NAME.match(parts[0]):
                out.append((parts[0], parts[1], span))
        i += 1
    return out


def _dockerfile_arg_entries(text: str, lines: List[str]) -> List[_Entry]:
    """`ARG KEY[=default]` -> `(KEY, value, span)`; no `=default` means
    `value=None` (#165's ARG semantics -- distinct from `_stringify`'s "" for
    a modeled null elsewhere in this module: an ARG default that is simply
    ABSENT is not the same fact as a key explicitly set to an empty value)."""
    out: List[_Entry] = []
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#") or not _DOCKER_ARG.match(stripped):
            i += 1
            continue
        start_lineno = i + 1
        joined, i = _join_continuations(lines, i)
        rest = _DOCKER_ARG.match(joined).group(1).strip()
        span = _line_span(text, lines, start_lineno)
        key, sep, raw_default = rest.partition("=")
        key = key.strip()
        if _ENV_KEY_NAME.match(key):
            out.append((key, _env_value(raw_default) if sep else None, span))
        i += 1
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
_REF_PERCENT = re.compile(r'%\([A-Za-z_][A-Za-z0-9_.]*\)s')


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


def _build_keys(
    artifact_id: str, namespace: str, entries: List[_Entry], capture_value: bool,
    *, raw_value: bool = False, id_key: Optional[Callable[[str], str]] = None,
) -> List[PyConfigKey]:
    """Coalesce `entries` (last dotted-key occurrence in file order wins --
    the existing L1 duplicate-key precedent, e.g. a redefined env var) into
    `PyConfigKey` records for one namespace.

    `raw_value=True` (dockerfile) passes the parsed value straight through
    instead of `_stringify`-ing it, so an ARG's absent default surfaces as
    `value=None` rather than `_stringify`'s "" for a modeled null -- dockerfile
    values are already plain parsed text/`None`, never a yaml/json/toml
    bool/None that needs that coercion.

    `id_key` remaps `dotted_key` for ID CONSTRUCTION only -- the `.key` FIELD
    always stays the bare `dotted_key`. Two call sites need it, both to keep
    a bare-name mint from colliding with another mint that happens to use
    the same bare name for its OWN id: a Dockerfile ARG's id (`ARG X` then
    `ENV X=$X` is a common promotion idiom -- both would otherwise mint id
    `.../@key/X`), and a yaml artifact's compose/k8s env-dual-mint id (a
    top-level yaml key sharing a name with a recognized env var would
    otherwise collide with the plain yaml mint's own bare-key id). Every
    other namespace omits it, preserving today's id shape."""
    coalesced: Dict[str, Tuple[object, Optional[Span]]] = {}
    for dotted_key, value, span in entries:
        coalesced[dotted_key] = (value, span)
    keys = []
    for dotted_key, (value, span) in coalesced.items():
        text_value = value if raw_value else _stringify(value)
        keys.append(PyConfigKey(
            id=config_key_id(artifact_id, id_key(dotted_key) if id_key else dotted_key),
            key=dotted_key, namespace=namespace,
            value=text_value if capture_value else None,
            span=span, references=_find_references(text_value or ""),
        ))
    return keys


# --- public API -----------------------------------------------------------

def is_config_eligible(artifact: PyArtifact) -> bool:
    """Whether `artifact` is worth extracting config keys from: an env-family
    basename (`.env`/`.env.*`/`.flaskenv`, regardless of declared format), a
    `dockerfile`-format artifact (#165), or a namespace-bearing format
    (yaml/json/toml/ini/properties). A binary artifact is never eligible --
    there is no decodable text to flatten, and a rule-matched-but-undecodable
    file downgrades to `format="binary"` regardless of its basename (see
    discovery.py), so the binary check wins even over an env-family name.

    Callers (core.py's wiring) use this to skip the on-disk read + parse
    attempt entirely on artifacts that can never yield config keys, rather
    than relying on `extract_config_keys`'s own not-applicable `([], True)`
    return after already having paid for the read."""
    if artifact.format == "binary":
        return False
    basename = artifact.path.rsplit("/", 1)[-1]
    return (
        _is_env_family(basename)
        or artifact.format == "dockerfile"
        or artifact.format in _NAMESPACE_PARSERS
    )


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
    sorted by `key` (L1 determinism) -- a dockerfile or dual-minted yaml
    artifact concatenates its namespace groups before this one sort, and
    Python's sort is stable, so a same-`key` tie across namespaces still
    resolves deterministically (env before dockerfile; yaml before env).

    `value` is populated only when `capture_value` is True; `key`,
    `namespace`, `span`, and `references` are extracted unconditionally
    either way (references are recognized in the raw leaf value regardless
    of whether that value is exposed)."""
    basename = artifact.path.rsplit("/", 1)[-1]
    try:
        lines = full_text.splitlines()
        if _is_env_family(basename):
            keys = _build_keys(artifact.id, "env", _parse_env(full_text, lines), capture_value)
        elif artifact.format == "dockerfile":
            keys = _build_keys(
                artifact.id, "env", _dockerfile_env_entries(full_text, lines), capture_value,
                raw_value=True,
            ) + _build_keys(
                artifact.id, "dockerfile", _dockerfile_arg_entries(full_text, lines), capture_value,
                raw_value=True, id_key=lambda k: f"arg.{k}",
            )
        elif artifact.format == "yaml":
            # Two independent parses (like every other format here -- each
            # piece parses what IT needs, no shared-state shortcut): `_parse_
            # yaml` for the plain dotted-path entries, a second `safe_load`
            # for the raw tree the env-shape recognizer walks.
            flat = list(_flatten(yaml.safe_load(full_text) or {}))
            env_entries = [
                (k, v, _find_key_span(full_text, lines, k)) for k, v in _recognize_env_shapes(flat)
            ]
            keys = (
                _build_keys(artifact.id, "yaml", _parse_yaml(full_text, lines), capture_value)
                + _build_keys(
                    artifact.id, "env", env_entries, capture_value,
                    id_key=lambda k: f"env.{k}",
                )
            )
        else:
            parser = _NAMESPACE_PARSERS.get(artifact.format)
            if parser is None:
                return [], True
            keys = _build_keys(artifact.id, artifact.format, parser(full_text, lines), capture_value)
        keys.sort(key=lambda k: k.key)
        return keys, True
    except Exception:
        return [], False
