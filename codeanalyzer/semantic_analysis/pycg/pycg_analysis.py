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

**Sharding** (``shard=True``) runs PyCG independently per Python package
root instead of over the entire project.  This keeps each shard under the
500-file ceiling by bounding PyCG's recursive import-following to the
package boundary.  Cross-shard imports become ghost nodes (same quality as
Jedi-only edges for those call sites).  Edge names are normalised back to
project-relative dotted paths so they align with the symbol table.
"""

# Python 3.13 compatibility: PyCG installs a custom import hook and calls
# importlib.invalidate_caches() during analysis.  In Python 3.13, that call
# triggers lazy loading of importlib.metadata → json → json.decoder, which
# re-enters PyCG's hook before its import graph is ready.  Pre-importing
# these modules at import time ensures they're already in sys.modules when
# PyCG's hook is active, preventing the re-entrant ImportManagerError.
import importlib.metadata  # noqa: F401
import importlib.util  # noqa: F401
import contextlib
import json  # noqa: F401
import signal

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Union


@contextlib.contextmanager
def _shard_timeout(seconds: int) -> Generator[None, None, None]:
    """Context manager that raises ``TimeoutError`` if the body runs longer than *seconds*.

    Uses SIGALRM on POSIX (macOS / Linux).  On platforms without SIGALRM
    (Windows) the context manager is a no-op — shards can still be bounded
    by the file-count ceiling.

    Must be called from the main thread (SIGALRM restriction).
    """
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(signum: int, frame: object) -> None:
        raise TimeoutError(f"shard timed out after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

from codeanalyzer.schema.py_schema import PyCallEdge, PyModule
from codeanalyzer.semantic_analysis.call_graph import iter_callables_in_symbol_table
from codeanalyzer.semantic_analysis.pycg.pycg_exceptions import PyCGExceptions
from codeanalyzer.utils import logger


def _apply_pycg_posonly_patch() -> None:
    """Monkey-patch PyCG's PreProcessor to handle Python 3.8+ positional-only params.

    PyCG's ``_get_fun_defaults`` computes the default-argument start index as
    ``len(node.args.args) - len(node.args.defaults)``.  In Python 3.8+,
    ``node.args.defaults`` covers the LAST ``len(defaults)`` arguments of
    ``posonlyargs + args`` combined, not just ``args``.  When any positional-
    only argument has a default (e.g. ``def f(a=1, b=2, /):``), the start
    index becomes too negative, causing ``IndexError: list index out of range``
    during PyCG's pre-processing pass.

    This function replaces ``PreProcessor._get_fun_defaults`` with a corrected
    implementation the first time it is called.  Subsequent calls are no-ops.
    """
    try:
        import sys
        preprocessor_mod = sys.modules.get("pycg.processing.preprocessor") \
            or sys.modules.get("PyCG.processing.preprocessor")
        if preprocessor_mod is None:
            import importlib
            for pkg_name in ("pycg", "PyCG"):
                try:
                    preprocessor_mod = importlib.import_module(
                        f"{pkg_name}.processing.preprocessor"
                    )
                    break
                except ImportError:
                    continue
        if preprocessor_mod is None:
            return

        PreProcessor = preprocessor_mod.PreProcessor
        if getattr(PreProcessor, "_posonly_patched", False):
            return

        def _patched_get_fun_defaults(self, node):  # type: ignore[override]
            defaults = {}
            # Combine posonlyargs (Python 3.8+) with regular args so that the
            # start index is computed over the full positional parameter list.
            all_args = getattr(node.args, "posonlyargs", []) + node.args.args
            start = len(all_args) - len(node.args.defaults)
            for cnt, d in enumerate(node.args.defaults, start=start):
                if not d:
                    continue
                self.visit(d)
                if 0 <= cnt < len(all_args):
                    defaults[all_args[cnt].arg] = self.decode_node(d)

            start = len(node.args.kwonlyargs) - len(node.args.kw_defaults)
            for cnt, d in enumerate(node.args.kw_defaults, start=start):
                if not d:
                    continue
                self.visit(d)
                if 0 <= cnt < len(node.args.kwonlyargs):
                    defaults[node.args.kwonlyargs[cnt].arg] = self.decode_node(d)
            return defaults

        PreProcessor._get_fun_defaults = _patched_get_fun_defaults  # type: ignore[method-assign]
        PreProcessor._posonly_patched = True  # type: ignore[attr-defined]
        logger.debug("PyCG: applied positional-only-param default patch (Python 3.8+ fix)")
    except Exception:
        pass


def _import_pycg() -> Any:
    """Import PyCG's CallGraphGenerator, trying both 'pycg' and 'PyCG' package names.

    The PyPI distribution installs as ``PyCG/`` (mixed case). Python's importer
    is case-sensitive even on macOS HFS+, so we try both names and normalise
    ``pycg`` in sys.modules so PyCG's own ``from pycg import utils`` resolves
    regardless of which name the finder used first.

    Returns the ``CallGraphGenerator`` class.
    Raises ``PyCGExceptions.PyCGImportError`` if neither name is importable.
    """
    import importlib
    import sys

    for pkg_name in ("pycg", "PyCG"):
        try:
            mod = importlib.import_module(pkg_name)
            sys.modules.setdefault("pycg", mod)
            sys.modules.setdefault("PyCG", mod)
            pycg_mod = importlib.import_module(f"{pkg_name}.pycg")
            return pycg_mod.CallGraphGenerator
        except ImportError:
            continue

    raise PyCGExceptions.PyCGImportError(
        "pycg is not installed — run `pip install pycg`"
    )


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
        shard: When ``True``, run PyCG independently per Python package
            root instead of over the whole project.  Required for projects
            that exceed the 500-file ceiling.
        shard_ceiling: Maximum file count per shard.  Shards exceeding this
            limit are skipped.  Defaults to ``_PYCG_SHARD_CEILING`` (100).
        shard_timeout: Per-shard wall-clock timeout in seconds.  A shard that
            exceeds this limit is skipped.  0 disables the timeout.  Defaults
            to ``_PYCG_SHARD_TIMEOUT`` (120).  POSIX only; no-op on Windows.
    """

    # PyCG's pointer analysis is practical only up to this many files.
    # Its per-iteration cost grows super-linearly; on very large projects
    # even a single pass can take tens of minutes.
    _PYCG_FILE_CEILING: int = 500

    # Separate, tighter ceiling applied per shard in sharding mode.
    # A shard covers one Python package root; PyCG follows imports only
    # within that boundary.  Even so, packages with deep class hierarchies
    # or heavily interconnected imports can cause PyCG's pointer fixpoint
    # to diverge well before the whole-project ceiling.  100 files is the
    # conservative default; override via --pycg-shard-ceiling.
    _PYCG_SHARD_CEILING: int = 100

    # Per-shard wall-clock timeout (seconds).  PyCG's fixpoint is bimodal:
    # either it converges in seconds or it diverges and never finishes.
    # This timeout acts as a final safety net after the file-count ceiling.
    # 120 seconds is generous enough for any legitimately complex shard
    # while still catching non-converging ones.  Override via
    # --pycg-shard-timeout.  Set to 0 to disable.
    _PYCG_SHARD_TIMEOUT: int = 120

    # Directory names that should never be fed to PyCG as entry points.
    _SKIP_DIRS: frozenset = frozenset({
        ".codeanalyzer", ".git", "__pycache__",
        "venv", ".venv", "virtualenv", "env", ".env",
        "node_modules", "dist", "build", ".tox", ".nox",
        "site-packages",
    })

    def __init__(
        self,
        project_dir: Union[str, Path],
        skip_tests: bool = True,
        shard: bool = False,
        shard_ceiling: Optional[int] = None,
        shard_timeout: Optional[int] = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.skip_tests = skip_tests
        self.shard = shard
        self.shard_ceiling = (
            shard_ceiling if shard_ceiling is not None else self._PYCG_SHARD_CEILING
        )
        self.shard_timeout = (
            shard_timeout if shard_timeout is not None else self._PYCG_SHARD_TIMEOUT
        )
        self._CallGraphGenerator: Optional[Any] = None

    # ------------------------------------------------------------------
    # Entry-point collection
    # ------------------------------------------------------------------

    def _collect_entry_points(self) -> List[str]:
        """Return absolute paths of project Python files, excluding caches and venvs."""
        paths = []
        for p in self.project_dir.rglob("*.py"):
            # Skip any file whose path passes through a filtered directory.
            if any(part in self._SKIP_DIRS for part in p.parts):
                continue
            # Skip test files using exact path-component matching, consistent
            # with core.py's _build_symbol_table filter.  Substring matching
            # (e.g. "/test" in full_path_str) incorrectly excludes files in
            # paths like "test/fixtures/..." that are source files, not tests.
            rel_parts = p.relative_to(self.project_dir).parts
            if self.skip_tests and (
                "test" in rel_parts
                or "tests" in rel_parts
                or p.stem.startswith("test_")
                or p.name.endswith("_test.py")
                or p.name == "conftest.py"
            ):
                continue
            paths.append(str(p))
        return paths

    # ------------------------------------------------------------------
    # Package-root helpers for sharding
    # ------------------------------------------------------------------

    @staticmethod
    def _find_package_root(file_path: Path, project_dir: Path) -> Path:
        """Return the top-level Python package directory that owns *file_path*.

        Walks upward from the file's directory toward *project_dir*, returning
        the highest ancestor that still contains an ``__init__.py``.  Files
        at the project root (no ``__init__.py`` in any parent) are placed in
        a shard rooted at *project_dir* itself.

        Examples::

            project/addons/account/models/res.py  →  project/addons/account/
            project/src/flask/app.py              →  project/src/flask/
            project/standalone_script.py          →  project/
        """
        package_root = file_path.parent
        current = file_path.parent
        while current != project_dir:
            if not (current / "__init__.py").exists():
                break
            package_root = current
            current = current.parent
        return package_root

    @staticmethod
    def _package_prefix(pkg_root: Path, project_dir: Path) -> str:
        """Dot-separated path from *project_dir* to *pkg_root*.

        This prefix is prepended to PyCG's package-relative edge names so
        they become project-relative and align with the symbol table::

            pkg_root = project/addons/account/  →  "addons.account"
            pkg_root = project/src/flask/       →  "src.flask"
            pkg_root = project/                 →  ""   (no prefix needed)
        """
        rel = pkg_root.relative_to(project_dir)
        return ".".join(rel.parts)

    # ------------------------------------------------------------------
    # Core PyCG runner
    # ------------------------------------------------------------------

    def _ensure_pycg_loaded(self) -> None:
        """Import PyCG and apply compatibility patches (idempotent)."""
        if self._CallGraphGenerator is not None:
            return
        self._CallGraphGenerator = _import_pycg()
        # Python 3.8+ positional-only-param fix and Python 3.13 import-hook fix.
        _apply_pycg_posonly_patch()

    def _run_pycg_batch(
        self,
        entry_points: List[str],
        package_dir: Path,
        resolver: "_PyCGCallableResolver",
        prefix: str = "",
    ) -> List[PyCallEdge]:
        """Run PyCG on *entry_points* with *package_dir* as the package root.

        *prefix* is a dot-separated path prepended to every edge name emitted
        by PyCG so that shard-relative names become project-relative.  Pass
        ``""`` when *package_dir* is the project root (names already match).

        Raises ``PyCGExceptions.PyCGAnalysisError`` on any PyCG failure.
        """
        assert self._CallGraphGenerator is not None
        try:
            cg = self._CallGraphGenerator(
                entry_points=entry_points,
                package=str(package_dir),
                max_iter=-1,
                operation="call-graph",
            )
            cg.analyze()
        except TimeoutError:
            raise  # propagate directly so _build_sharded logs a clean timeout message
        except Exception as exc:
            raise PyCGExceptions.PyCGAnalysisError(
                f"PyCG analysis failed: {exc}"
            ) from exc

        edge_counts: Counter = Counter()
        for src, dst in cg.output_edges():
            if prefix:
                src = f"{prefix}.{src}"
                dst = f"{prefix}.{dst}"
            edge_counts[(resolver.resolve(src), resolver.resolve(dst))] += 1

        return [
            PyCallEdge(source=src, target=dst, weight=count, provenance=["pycg"])
            for (src, dst), count in edge_counts.items()
        ]

    # ------------------------------------------------------------------
    # Sharded analysis
    # ------------------------------------------------------------------

    def _build_sharded(
        self,
        entry_points: List[str],
        resolver: "_PyCGCallableResolver",
    ) -> List[PyCallEdge]:
        """Run PyCG per Python package shard and merge the results.

        Groups entry points by their top-level package root.  Each shard
        whose size is within ``self.shard_ceiling`` is analysed independently
        with its package directory as the PyCG ``package`` root, which limits
        recursive import-following to that package boundary.  Shards that
        exceed the shard ceiling are skipped with a warning (framework modules
        with deep mixin hierarchies can cause PyCG's fixpoint to diverge).

        Edge names are normalised to project-relative dotted paths so they
        match the symbol table's ``PyCallable.signature`` namespace.
        """
        shards: Dict[Path, List[str]] = defaultdict(list)
        for ep in entry_points:
            pkg_root = self._find_package_root(Path(ep), self.project_dir)
            shards[pkg_root].append(ep)

        logger.debug(
            "PyCG: sharding %d files into %d package shard(s)",
            len(entry_points), len(shards),
        )

        all_edges: List[PyCallEdge] = []
        skipped = 0
        for pkg_root, files in shards.items():
            n = len(files)
            pkg_label = str(pkg_root.relative_to(self.project_dir)) or "."
            if n > self.shard_ceiling:
                logger.warning(
                    "PyCG shard '%s': %d files exceeds shard ceiling of %d — skipped",
                    pkg_label, n, self.shard_ceiling,
                )
                skipped += 1
                continue
            prefix = self._package_prefix(pkg_root, self.project_dir)
            try:
                with _shard_timeout(self.shard_timeout):
                    edges = self._run_pycg_batch(files, pkg_root, resolver, prefix=prefix)
                all_edges.extend(edges)
                logger.debug(
                    "PyCG shard '%s': %d edges from %d files",
                    pkg_label, len(edges), n,
                )
            except TimeoutError as exc:
                logger.warning(
                    "PyCG shard '%s' timed out after %ds — skipped",
                    pkg_label, self.shard_timeout,
                )
                skipped += 1
            except PyCGExceptions.PyCGAnalysisError as exc:
                logger.warning("PyCG shard '%s' failed — skipped: %s", pkg_label, exc)
                skipped += 1

        if skipped:
            logger.warning(
                "PyCG: %d shard(s) were skipped (exceeded %d-file ceiling, "
                "%ds timeout, or failed)",
                skipped, self.shard_ceiling, self.shard_timeout,
            )

        # Merge duplicate (source, target) pairs that appear in multiple shards.
        merged: Dict[tuple, PyCallEdge] = {}
        for edge in all_edges:
            key = (edge.source, edge.target)
            if key in merged:
                existing = merged[key]
                merged[key] = PyCallEdge(
                    source=existing.source,
                    target=existing.target,
                    weight=existing.weight + edge.weight,
                    provenance=existing.provenance,
                )
            else:
                merged[key] = edge

        result = list(merged.values())
        logger.debug(
            "PyCG: sharding produced %d edges (%d before dedup) from %d/%d shard(s)",
            len(result), len(all_edges), len(shards) - skipped, len(shards),
        )
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        When ``self.shard=True`` and the project exceeds the 500-file ceiling,
        PyCG is run per Python package root (see :meth:`_build_sharded`).
        When ``self.shard=False`` and the project exceeds the ceiling, PyCG is
        skipped and an empty list is returned (Jedi-only fallback).
        """
        try:
            self._ensure_pycg_loaded()
        except PyCGExceptions.PyCGImportError:
            raise

        entry_points = self._collect_entry_points()
        if not entry_points:
            logger.debug("PyCG: no Python files found under %s", self.project_dir)
            return []

        n_files = len(entry_points)
        resolver = _PyCGCallableResolver.from_symbol_table(symbol_table)

        if n_files > self._PYCG_FILE_CEILING:
            if self.shard:
                logger.debug(
                    "PyCG: %d entry points — running sharded analysis", n_files
                )
                return self._build_sharded(entry_points, resolver)
            else:
                logger.warning(
                    "PyCG: %d entry points exceeds ceiling of %d — "
                    "skipping pointer analysis (Jedi-only edges will be used). "
                    "Re-run with --pycg-shard to analyse per package shard.",
                    n_files, self._PYCG_FILE_CEILING,
                )
                return []

        # Small project (≤ ceiling): whole-project analysis.
        logger.debug("PyCG: %d entry points — running whole-project analysis", n_files)
        try:
            edges = self._run_pycg_batch(
                entry_points, self.project_dir, resolver, prefix=""
            )
        except PyCGExceptions.PyCGAnalysisError as exc:
            raise

        logger.debug("PyCG: produced %d call edges", len(edges))
        return edges
