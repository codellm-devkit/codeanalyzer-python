"""Shared, low-level parsing of Python dependency manifests.

The bracket-aware ``pyproject.toml`` deps-array reader and the comment stripper
live here so the framework-detection pass (``entrypoints/detect.py``, which needs
only package *names*) and the artifact layer (which needs name + version spec +
scope) share one parser instead of two that can drift. Pure text helpers only;
no I/O, no analysis models.
"""
from __future__ import annotations

import re
from typing import Optional

# `from flask import Flask` puts the package in `module`; a requirements line is
# `name[extra]==version`. These match the *name* token at the start of a spec.
REQ_NAME = re.compile(r"^\s*['\"]?([A-Za-z0-9_.\-]+)")
DEPS_START = re.compile(r"dependencies\s*=\s*\[")
TABLE_HEADER = re.compile(r"(?m)^[ \t]*\[")
# A quoted package token inside a TOML array; group 1 is the whole spec string.
PKG_STRING = re.compile(r"['\"]([^'\"]+)['\"]")
# Split a PEP 508 / requirements spec into (name, version_spec). The name may
# carry an extras group (`celery[redis]`), dropped from the identity.
SPEC = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")


def strip_comments(text: str) -> str:
    """Drop everything from an unquoted ``#`` to end of line.

    Quote tracking resets each line, so a ``#`` inside a triple-quoted string
    spanning lines could be mis-stripped. TOML dependency arrays don't use those
    in practice; revisit if they do.
    """
    out_lines = []
    for line in text.splitlines():
        in_str = None
        cut = len(line)
        for i, ch in enumerate(line):
            if in_str:
                if ch == in_str:
                    in_str = None
            elif ch in ("'", '"'):
                in_str = ch
            elif ch == "#":
                cut = i
                break
        out_lines.append(line[:cut])
    return "\n".join(out_lines)


def deps_array_span(text: str, key: str = "dependencies") -> Optional[str]:
    """Contents between ``<key> = [`` and its matching ``]``, counting bracket
    depth so a nested ``[...]`` (extras, e.g. ``celery[redis]``) doesn't close
    the span early.

    Bounded by the next TOML table header (a ``[`` starting a line): if the array
    never closes before then, it's unterminated (truncated/corrupt file) and this
    returns None rather than harvesting quoted strings out of the following table.
    """
    start = re.compile(rf"{re.escape(key)}\s*=\s*\[")
    m = start.search(text)
    if not m:
        return None
    boundary = TABLE_HEADER.search(text, m.end())
    limit = boundary.start() if boundary else len(text)
    depth = 1
    in_str = None
    i = m.end()
    while i < limit and depth > 0:
        ch = text[i]
        if in_str:
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return text[m.end() : i - 1]


def split_spec(spec: str) -> tuple[str, Optional[str]]:
    """Split a requirement string into ``(name, version_spec_or_None)``.

    ``requests>=2.20`` → ``("requests", ">=2.20")``; ``celery[redis]`` →
    ``("celery", None)``; a plain name → ``(name, None)``. Extras are dropped
    from the identity (pypi name is scope-free). Returns ``("", None)`` for a
    line with no recognizable name so the caller can skip it.
    """
    m = SPEC.match(spec.strip())
    if not m:
        return "", None
    name = m.group(1)
    rest = m.group(2).strip()
    return name, (rest or None)
