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
