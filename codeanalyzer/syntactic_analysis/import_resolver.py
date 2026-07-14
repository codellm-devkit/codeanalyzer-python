"""Static resolution of import spellings against the analyzed module set.

Pure post-pass over a built ``PyApplication``: no filesystem access, no
sys.path semantics — a spelling resolves iff it names a module that was
itself analyzed (issue #82). External/library imports stay unresolved by
design and keep their :PyPackage projection.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Union

from codeanalyzer.schema.py_schema import PyApplication


def _dotted_candidates(app: PyApplication, project_dir: Union[Path, str]) -> Dict[str, str]:
    """dotted module path -> file_key, for every analyzed module.

    ``pkg/util.py`` -> ``pkg.util``; ``pkg/__init__.py`` -> ``pkg``.
    file_keys share project_dir's form (both come from the same CLI arg),
    so os.path.relpath keeps mixed absolute/relative setups consistent.
    """
    mapping: Dict[str, str] = {}
    for file_key in app.symbol_table:
        rel = os.path.relpath(file_key, str(project_dir))
        if rel.startswith(".."):
            continue
        parts = Path(rel).with_suffix("").parts
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            mapping[".".join(parts)] = file_key
    return mapping


def _resolve_one(
    spelling: str, original_name: str, importer_rel_parts: tuple, candidates: Dict[str, str]
) -> Optional[str]:
    if spelling.startswith("."):
        level = len(spelling) - len(spelling.lstrip("."))
        suffix = spelling.lstrip(".")
        # level 1 = the importer's own package; each extra dot walks one up.
        package_parts = importer_rel_parts[:-1]  # drop the filename
        if level - 1 > len(package_parts):
            return None
        base = package_parts[: len(package_parts) - (level - 1)]
        stems = list(base) + (suffix.split(".") if suffix else [])
    else:
        stems = spelling.split(".")
    dotted = ".".join(stems)
    with_name = f"{dotted}.{original_name}" if dotted else original_name
    return candidates.get(with_name) or candidates.get(dotted)


def resolve_imports(app: PyApplication, project_dir: Union[Path, str]) -> None:
    """Stamp ``resolved_module`` on every import of every module, in place."""
    candidates = _dotted_candidates(app, project_dir)
    for file_key, module in app.symbol_table.items():
        rel = os.path.relpath(file_key, str(project_dir))
        importer_parts = Path(rel).parts
        for im in module.imports or []:
            if not im.module:
                im.resolved_module = None
                continue
            original = im.alias or im.name
            im.resolved_module = _resolve_one(im.module, original, importer_parts, candidates)
