"""Canonical `can://` id construction for schema v2 (durable ids, ≥ callable).
Ordinal ids (< callable) are `ordinal_id(callable_id, tag)`. Pure functions;
ids are opaque handles (the <file> segment itself contains '/').

The **application is the outermost segment** and the language sits inside it:
``can://<app>/python/<file>/<type>/<callable-sig>``. So ``can://<app>`` is a
prefix of every id this analyzer mints for that application — code, externals
and artifacts alike — which is what the prefix-scoped destructive statements
(#173) rely on. Nothing may be identified by its *language* prefix any more:
an application named ``python`` mints ``can://python/python/...``, so a test
for ``can://python/`` no longer means "a python id"; test the scheme instead."""
from __future__ import annotations
from typing import List, Optional

SCHEME = "can://"

# This analyzer's language segment, which sits INSIDE the app rather than above it.
LANG = "python"

def application_id(app_name: str) -> str:
    """``can://<app>`` — the application root, and the prefix every id below it shares."""
    return f"{SCHEME}{app_name}"

def module_id(app_name: str, file_key: str) -> str:
    """``can://<app>/python/<relative-file-key>`` (separators normalized to ``/``)."""
    rel = file_key.replace("\\", "/").lstrip("./")
    return f"{application_id(app_name)}/{LANG}/{rel}"

def child_id(parent_id: str, segment: str) -> str:
    return f"{parent_id}/{segment}"

def callable_sig_segment(name: str, param_names: List[str]) -> str:
    return f"{name}({','.join(param_names)})"

def ordinal_id(callable_id: str, tag: str) -> str:
    return f"{callable_id}@{tag}"


def external_id(app_id: str, module: Optional[str], name: str) -> str:
    """``can://<app>/@external/<module>/<name>`` — the home of a call-graph
    endpoint that is not declared in the symbol table (an imported library or
    builtin member). ``module`` is ``None`` for a dot-less signature, which drops
    the segment.

    Language-NEUTRAL, like ``artifact``: ``@external`` sits in the position the
    language occupies for code nodes, so sibling analyzers over the same ``<app>``
    name a library symbol identically and it is one node in a merged graph. The
    cost is real and was accepted deliberately — two analyzers' notions of
    ``os.getcwd`` are not necessarily the same thing, and merging them says they
    are. TypeScript's form; java follows it."""
    base = f"{app_id}/@external"
    return f"{base}/{module}/{name}" if module else f"{base}/{name}"


def global_ordinal(callable_id: str, local_key: str) -> str:
    """The GLOBAL ordinal id of a body node from its LOCAL key: synthetic keys
    (`@entry`, `@formal_in:0`) already carry the `@`; positional keys (`15:2`,
    `15:2/actual_in:0`) get one. This is the :PyBodyNode merge key and, since
    #176, `BodyNode.id` — the one implementation both projections share."""
    return f"{callable_id}{local_key}" if local_key.startswith("@") else f"{callable_id}@{local_key}"


def stamp_body_ids(callable) -> None:
    """Stamp `id` on every body node and parameter of one callable (#176).
    Idempotent; each body emitter calls it after writing its nodes."""
    for key, node in callable.body.items():
        node.id = global_ordinal(callable.id, key)
    for i, p in enumerate(callable.parameters or []):
        p.id = ordinal_id(callable.id, f"formal_in:{i}")


def artifact_id(app_name: str, rel_path: str) -> str:
    """Language-neutral artifact id: ``can://<app>/artifact/<rel-path>``.

    ``artifact`` is a reserved segment in the same position the language
    occupies for code nodes, so sibling analyzers over the same repo (same
    ``<app>``) still emit the same id for the same file — and, unlike the old
    ``can://artifact/<app>/...``, it now sits inside the application prefix."""
    return f"{application_id(app_name)}/artifact/{rel_path}"


def config_key_id(artifact_id: str, dotted_key: str) -> str:
    """A ``PyConfigKey`` extracted from an artifact: ``<artifact-id>@key/<dotted.key>``.
    ``dotted_key`` uses numeric segments for array indices (e.g.
    ``services.web.ports.0``); ids are opaque, do not re-split them."""
    return f"{artifact_id}@key/{dotted_key}"


def purl_pypi(name: str) -> str:
    """Package URL for a (PEP 503 normalized) PyPI distribution name."""
    return f"pkg:pypi/{name}"
