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


# Repository-artifact layer: application-anchored, non-source nodes. The
# `@artifact/` marker keeps these out of the callable `signatureOf` id space
# (like `@external/` homes) so they never collide with a source-tree id.
def artifact_id(app_name: str, rel_path: str) -> str:
    # Normalize separators and drop a leading "./" or "/" ONLY -- not a character
    # strip: dotfiles are exactly what this layer inventories (`.env`,
    # `.flaskenv`, `.github/workflows/...`), so their leading dot must survive.
    rel = rel_path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    return f"{application_id(app_name)}/@artifact/{rel}"


def dependency_id(artifact_node_id: str, native_name: str) -> str:
    return child_id(artifact_node_id, native_name)


def config_key_id(artifact_node_id: str, dotted_key: str) -> str:
    return child_id(artifact_node_id, dotted_key)
