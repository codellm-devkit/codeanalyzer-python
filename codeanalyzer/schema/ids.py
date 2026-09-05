"""Canonical `can://` id construction for schema v2 (durable ids, ≥ callable).
Ordinal ids (< callable) are `ordinal_id(callable_id, tag)`. Pure functions;
ids are opaque handles (the <file> segment itself contains '/')."""
from __future__ import annotations
from typing import List

_SCHEME = "can://python"

def application_id(app_name: str) -> str:
    return f"{_SCHEME}/{app_name}"

def module_id(app_name: str, file_key: str) -> str:
    rel = file_key.replace("\\", "/").lstrip("./")
    return f"{application_id(app_name)}/{rel}"

def child_id(parent_id: str, segment: str) -> str:
    return f"{parent_id}/{segment}"

def callable_sig_segment(name: str, param_names: List[str]) -> str:
    return f"{name}({','.join(param_names)})"

def ordinal_id(callable_id: str, tag: str) -> str:
    return f"{callable_id}@{tag}"


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
    """Language-neutral artifact id: ``can://artifact/<app>/<rel-path>``.

    The first segment is a namespace (a language for code nodes, the literal
    ``artifact`` for files), so sibling analyzers over the same repo emit the
    same id for the same file."""
    return f"can://artifact/{app_name}/{rel_path}"


def config_key_id(artifact_id: str, dotted_key: str) -> str:
    """A ``PyConfigKey`` extracted from an artifact: ``<artifact-id>@key/<dotted.key>``.
    ``dotted_key`` uses numeric segments for array indices (e.g.
    ``services.web.ports.0``); ids are opaque, do not re-split them."""
    return f"{artifact_id}@key/{dotted_key}"


def purl_pypi(name: str) -> str:
    """Package URL for a (PEP 503 normalized) PyPI distribution name."""
    return f"pkg:pypi/{name}"
