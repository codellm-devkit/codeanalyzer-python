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

"""PyCG-based call graph construction for analysis level 2.

PyCG (Apache-2.0, ICSE 2021) uses iterative inter-procedural name-pointer
analysis to produce a call graph with ~99% precision and ~69% recall on
micro-benchmarks.  Its dotted namespace format (``module.Class.method``)
aligns directly with the ``PyCallable.signature`` space used by the symbol
table, so no name translation is needed for in-source callees.

Callees not found in the symbol table are treated as ghost nodes — the same
convention used by :func:`call_graph.to_digraph`.
"""

# Python 3.13 compatibility: PyCG installs a custom import hook and calls
# importlib.invalidate_caches() during analysis.  In Python 3.13, that call
# triggers lazy loading of importlib.metadata → json → json.decoder, which
# re-enters PyCG's hook before its import graph is ready.  Pre-importing
# these modules at import time ensures they're already in sys.modules when
# PyCG's hook is active, preventing the re-entrant ImportManagerError.
import importlib.metadata  # noqa: F401
import importlib.util  # noqa: F401
import json  # noqa: F401

from collections import Counter
from pathlib import Path
from typing import Dict, List, Set, Union

from codeanalyzer.schema.py_schema import PyCallEdge, PyModule
from codeanalyzer.semantic_analysis.call_graph import iter_callables_in_symbol_table
from codeanalyzer.semantic_analysis.pycg.pycg_exceptions import PyCGExceptions
from codeanalyzer.utils import logger


class _PyCGCallableResolver:
    """Maps a PyCG dotted namespace string to a ``PyCallable.signature``.

    PyCG names callables as ``module.Class.method`` relative to the package
    root, which is identical to our ``PyCallable.signature`` format.  A
    direct dict lookup is therefore sufficient; this class exists to hold
    the index and make the ghost-node fallback explicit.
    """

    def __init__(self, known: Set[str]) -> None:
        self._known = known

    @classmethod
    def from_symbol_table(
        cls, symbol_table: Dict[str, PyModule]
    ) -> "_PyCGCallableResolver":
        known = {c.signature for c in iter_callables_in_symbol_table(symbol_table)}
        return cls(known)

    def resolve(self, pycg_name: str) -> str:
        """Return the canonical signature for *pycg_name*.

        If the name is in the symbol table it is returned verbatim.
        Otherwise it is returned as-is so the edge is preserved as a
        ghost (external / library) node in the call graph.
        """
        return pycg_name


class PyCG:
    """Thin wrapper around PyCG's ``CallGraphGenerator``.

    Args:
        project_dir: Root of the Python project to analyse.
        skip_tests: When ``True``, files whose path contains ``test`` or
            ``conftest`` are excluded from the entry-point list.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        skip_tests: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.skip_tests = skip_tests

    # Directory names that should never be fed to PyCG as entry points.
    _SKIP_DIRS: frozenset = frozenset({
        ".codeanalyzer", ".git", "__pycache__",
        "venv", ".venv", "virtualenv", "env", ".env",
        "node_modules", "dist", "build", ".tox", ".nox",
        "site-packages",
    })

    def _collect_entry_points(self) -> List[str]:
        """Return absolute paths of project Python files, excluding caches and venvs."""
        paths = []
        for p in self.project_dir.rglob("*.py"):
            # Skip any file whose path passes through a filtered directory.
            if any(part in self._SKIP_DIRS for part in p.parts):
                continue
            rel = str(p)
            if self.skip_tests and (
                "/test" in rel or "\\test" in rel or "conftest" in rel
            ):
                continue
            paths.append(str(p))
        return paths

    def build_call_graph_edges(
        self, symbol_table: Dict[str, PyModule]
    ) -> List[PyCallEdge]:
        """Run PyCG and return ``PyCallEdge`` entries with ``provenance=["pycg"]``.

        Edges are coalesced on ``(source, target)`` — ``weight`` equals the
        number of times PyCG reports the same (caller, callee) pair (always 1
        per unique pair in PyCG's output).  Ghost callees (not in the symbol
        table) are preserved so external / library edges appear in the graph.

        Returns an empty list and logs a warning if pycg is not installed or
        if the analysis raises an unexpected exception.
        """
        try:
            # The PyPI distribution installs the package directory as `PyCG/`
            # (mixed case). Python's importer does case-sensitive directory
            # lookup even on macOS HFS+, so `import pycg` fails on some
            # environments while `import PyCG` works.  We try both names and
            # normalise `pycg` in sys.modules so PyCG's own internal
            # `from pycg import utils` resolves regardless of which name the
            # finder used first.
            import importlib
            import sys

            CallGraphGenerator = None
            for pkg_name in ("pycg", "PyCG"):
                try:
                    mod = importlib.import_module(pkg_name)
                    sys.modules.setdefault("pycg", mod)
                    sys.modules.setdefault("PyCG", mod)
                    from importlib import import_module as _imp
                    _pycg_mod = _imp(f"{pkg_name}.pycg")
                    CallGraphGenerator = _pycg_mod.CallGraphGenerator
                    break
                except ImportError:
                    continue

            if CallGraphGenerator is None:
                raise ImportError("pycg package not found under 'pycg' or 'PyCG'")
        except ImportError as exc:
            raise PyCGExceptions.PyCGImportError(
                "pycg is not installed — run `pip install pycg`"
            ) from exc

        entry_points = self._collect_entry_points()
        if not entry_points:
            logger.debug("PyCG: no Python files found under %s", self.project_dir)
            return []

        try:
            cg = CallGraphGenerator(
                entry_points=entry_points,
                package=str(self.project_dir),
                max_iter=-1,
                operation="call-graph",
            )
            cg.analyze()
        except Exception as exc:
            raise PyCGExceptions.PyCGAnalysisError(
                f"PyCG analysis failed: {exc}"
            ) from exc

        resolver = _PyCGCallableResolver.from_symbol_table(symbol_table)
        edge_counts: Counter = Counter()
        for src, dst in cg.output_edges():
            source_sig = resolver.resolve(src)
            target_sig = resolver.resolve(dst)
            edge_counts[(source_sig, target_sig)] += 1

        edges = [
            PyCallEdge(
                source=src,
                target=dst,
                weight=count,
                provenance=["pycg"],
            )
            for (src, dst), count in edge_counts.items()
        ]
        logger.debug("PyCG: produced %d call edges", len(edges))
        return edges
