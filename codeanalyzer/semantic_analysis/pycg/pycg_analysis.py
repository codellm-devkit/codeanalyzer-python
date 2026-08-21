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
import fcntl
import hashlib
import importlib.metadata  # noqa: F401
import importlib.util  # noqa: F401
import contextlib
import os
import json  # noqa: F401
import shutil
import tempfile
import time

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Tuple, Union

from codeanalyzer.schema.py_schema import PyCallEdge, PyModule
from codeanalyzer.semantic_analysis.call_graph import iter_callables_in_symbol_table
from codeanalyzer.semantic_analysis.pycg.pycg_exceptions import PyCGExceptions
from codeanalyzer.semantic_analysis.pycg.shard_planner import plan_shards
from codeanalyzer.utils import ProgressBar, logger


# PyCG spells the builtins module ``<builtin>``; Jedi spells it ``builtins``. Left
# unnormalized, one builtin gets two ``@external`` ``can://`` homes and the two
# backends' edges can never coalesce, so a call both resolvers agree on can never
# reach ``prov: ["jedi", "pycg"]`` (#132). PyCG only ever emits the bare module, so
# an exact-match alias is enough -- the dotted forms (``builtins.str`` etc.) are
# Jedi's and are already canonical.
_PYCG_MODULE_ALIASES = {"<builtin>": "builtins"}


def _canonical_endpoint(sig: str) -> str:
    """Rewrite a PyCG endpoint's module segment to the canonical spelling."""
    module, dot, name = sig.rpartition(".")
    if dot and module in _PYCG_MODULE_ALIASES:
        return f"{_PYCG_MODULE_ALIASES[module]}.{name}"
    return sig


def _canonicalize_edges(edges: List[PyCallEdge]) -> List[PyCallEdge]:
    """Canonicalize endpoint spellings, coalescing pairs that collide as a result.

    Two spellings of one target are one edge: weights sum and provenance unions,
    matching ``call_graph.merge_edges``. Deliberately does not route through
    ``_coalesce_edges``, which raises on its duplicate branch (#133).
    """
    merged: Dict[Tuple[str, str], PyCallEdge] = {}
    for edge in edges:
        src = _canonical_endpoint(edge.src)
        dst = _canonical_endpoint(edge.dst)
        key = (src, dst)
        current = merged.get(key)
        if current is None:
            merged[key] = edge.model_copy(update={"src": src, "dst": dst})
        else:
            current.weight += edge.weight
            current.prov = sorted(set(current.prov) | set(edge.prov))
    return list(merged.values())


def _shard_root_path(files: List[str], project_dir: Path) -> Path:
    """Content-derived mini-project root for a shard: same project + same file
    set → same path on every run (determinism, issue #99)."""
    digest = hashlib.sha1(
        "\0".join([str(project_dir), *sorted(files)]).encode("utf-8")
    ).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"canpy_pycg_shard_{digest}"


def _materialize_shard_root(
    files: List[str],
    project_dir: Path,
) -> Tuple[Path, List[str]]:
    """Build a temporary symlink mini-project for a shard; return ``(root, eps)``.

    PyCG bounds its import-following to the ``package`` directory — only
    modules whose resolved file lives under that root are followed; everything
    else becomes a ghost node (``ImportManager``: ``if self.mod_dir not in
    mod.__file__: return``).  A coupling-derived shard is an arbitrary set of
    files that need not form a directory, so we mirror the project layout into
    a temp dir holding symlinks to exactly the shard's files plus the
    ``__init__.py`` chain each needs for package resolution.  Running PyCG with
    this mirror as the package root confines analysis to the shard while
    emitting project-relative edge names (so ``prefix=""`` — no rename needed).

    The caller owns the returned *root* and must ``shutil.rmtree`` it.
    """
    # Deterministic root: PyCG's capped fixpoint (--pycg-max-iter) is
    # order-sensitive, and its internal state keys on absolute module paths —
    # a random mkdtemp suffix changes those strings every run and shifts the
    # iteration frontier, making the emitted edge set vary run-to-run
    # (issue #99). Deriving the directory name from the shard's content keeps
    # the path (and thus the analysis input) identical across runs. Callers
    # that may run concurrently on the same shard serialize on the sidecar
    # lock (see _shard_symlink_root).
    root = _shard_root_path(files, project_dir)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    entry_points: List[str] = []
    linked_inits: Set[Path] = set()
    for f in sorted(files):
        src = Path(f).resolve()
        try:
            rel = src.relative_to(project_dir)
        except ValueError:
            continue  # defensively skip files outside the project
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            dst.symlink_to(src)
        entry_points.append(str(dst))

        # Symlink the __init__.py chain from project root down to this file's
        # package so PyCG/importlib can resolve the dotted module name.  These
        # add ~0 analysis cost (usually empty) and keep out-of-shard siblings
        # unresolved → ghost nodes.
        for i in range(len(rel.parent.parts) + 1):
            pkg_rel = Path(*rel.parent.parts[:i])
            real_init = project_dir / pkg_rel / "__init__.py"
            link_init = root / pkg_rel / "__init__.py"
            if real_init.exists() and link_init not in linked_inits:
                link_init.parent.mkdir(parents=True, exist_ok=True)
                if not link_init.exists():
                    link_init.symlink_to(real_init.resolve())
                linked_inits.add(link_init)
    return root, entry_points


@contextlib.contextmanager
def _shard_symlink_root(
    files: List[str],
    project_dir: Path,
) -> Generator[Tuple[Path, List[str]], None, None]:
    """Context-manager wrapper around :func:`_materialize_shard_root`.

    Yields ``(root, entry_points)`` and removes the temp tree on exit.

    The root path is content-derived (determinism, issue #99), so two
    concurrent analyses of the same shard — e.g. a test suite and a manual
    run on one project — would collide on it (one rmtree's the tree the
    other is mid-analysis on). An exclusive flock on a sidecar lockfile
    serializes them; distinct projects/shards hash to distinct roots and
    never contend.
    """
    digest_root = _shard_root_path(files, project_dir)
    lock_path = digest_root.with_name(digest_root.name + ".lock")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        root, entry_points = _materialize_shard_root(files, project_dir)
        try:
            yield root, entry_points
        finally:
            shutil.rmtree(root, ignore_errors=True)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _analyze_with_convergence(cg: Any) -> bool:
    """Run ``cg.analyze()``; return whether its fixpoint converged.

    PyCG's loop is ``while (max_iter < 0 or iter_cnt < max_iter) and not
    has_converged()``, so the convergence check runs *before* each pass and
    never after the last one.  Reading it correctly needs care in two places:

    * **Asking after ``analyze()`` returns does not work.**  ``analyze`` runs a
      ``CallGraphProcessor`` pass past the loop, so a post-hoc call compares
      state that pass has already moved against the snapshot taken before it,
      and can report divergence for a shard that converged.
    * **The last recorded value alone does not work either.**  When the cap is
      what stops the loop, the ``and`` short-circuits and the check is skipped,
      leaving the ``False`` that admitted the final pass — which mislabels a
      shard that reached its fixpoint on exactly pass ``max_iter``.

    So raise the cap by one and cut the loop off from inside the check: at the
    point PyCG would have stopped, evaluate convergence once more (this is
    "did the final pass change anything") and then return ``True`` to stop the
    loop before the extra pass can run.  The verdict is a function of the input
    alone, unlike the wall-clock timeout it replaces (#145).
    """
    max_iter = cg.max_iter
    if max_iter == 0:
        # Degenerate: PyCG is asked for zero fixpoint passes, so the result is
        # an under-approximation by configuration rather than by divergence.
        # Re-splitting cannot improve it -- sub-shards get the same cap -- so
        # calling it a runaway would only buy pointless re-analysis.
        cg.analyze()
        return True

    had_own_attr = "has_converged" in vars(cg)
    original = cg.has_converged
    last: List[bool] = []

    def _recording() -> bool:
        result = bool(original())
        last.append(result)
        # PyCG would stop here on the cap without asking again; we asked, so
        # stop the loop ourselves rather than let the raised cap buy a pass.
        if max_iter >= 0 and len(last) > max_iter:
            return True
        return result

    cg.has_converged = _recording
    if max_iter >= 0:
        cg.max_iter = max_iter + 1
    try:
        cg.analyze()
    finally:
        cg.max_iter = max_iter
        if had_own_attr:
            cg.has_converged = original
        else:
            del cg.has_converged
    return last[-1] if last else True


def _pycg_shard_worker(
    entry_points: List[str],
    package_dir: str,
    prefix: str,
    max_iter: int = -1,
) -> Tuple[List[tuple], bool]:
    """Run PyCG on one shard; called in a Ray worker process.

    Returns ``(triples, converged)`` -- a list of ``(source, target, weight)``
    tuples that the caller converts to :class:`PyCallEdge` objects, and whether
    PyCG reached its fixpoint rather than stopping at ``max_iter`` (#145).
    This function is a plain
    module-level callable so it can be pickled by Ray without capturing any
    class-level state.  *max_iter* caps PyCG's fixpoint passes (-1 = unbounded).
    """
    import importlib
    import sys

    # Python 3.13 compatibility pre-imports (mirroring the top-level block).
    import importlib.metadata  # noqa: F401
    import importlib.util  # noqa: F401
    import json  # noqa: F401
    from collections import Counter as _WorkerCounter

    CallGraphGenerator = None
    for pkg_name in ("pycg", "PyCG"):
        try:
            mod = importlib.import_module(pkg_name)
            sys.modules.setdefault("pycg", mod)
            sys.modules.setdefault("PyCG", mod)
            pycg_mod = importlib.import_module(f"{pkg_name}.pycg")
            CallGraphGenerator = pycg_mod.CallGraphGenerator
            break
        except ImportError:
            continue

    if CallGraphGenerator is None:
        raise RuntimeError("pycg is not installed in Ray worker — run `pip install pycg`")

    _apply_pycg_posonly_patch()

    cg = CallGraphGenerator(
        entry_points=entry_points,
        package=package_dir,
        max_iter=max_iter,
        operation="call-graph",
    )
    converged = _analyze_with_convergence(cg)

    edge_counts = _WorkerCounter()
    for src, dst in cg.output_edges():
        if prefix:
            src = f"{prefix}.{src}"
            dst = f"{prefix}.{dst}"
        edge_counts[(src, dst)] += 1

    return [(src, dst, count) for (src, dst), count in edge_counts.items()], converged


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

    # Cap on PyCG's outer fixpoint passes.  PyCG runs PostProcessor until the
    # def/scope/MRO state stops changing; its abstract domain (field-sensitive
    # access paths, no k-limiting or widening) has no ascending-chain bound, so
    # on heavy metaclass/mixin code (e.g. an ORM) the def set can balloon into
    # the thousands and each O(defs^2) pass costs seconds — convergence, if it
    # comes, takes many passes.  A finite cap turns "loop until killed" into a
    # sound-but-incomplete result that still returns the edges found so far.
    # 50 is generous — well-behaved code converges in well under 20 passes —
    # while bounding the pathological case.  Override via --pycg-max-iter;
    # -1 restores PyCG's unbounded run-to-convergence behaviour.
    _PYCG_MAX_ITER: int = 50

    # Iterative decomposition of runaway shards: a shard whose fixpoint stopped
    # at --pycg-max-iter instead of converging is re-partitioned at half the
    # budget and re-run, down to this file-count floor.  Below the floor — or
    # for an atomic import cycle that won't split — the shard keeps the edges
    # its capped fixpoint did derive.  The floor is 1 because a lone divergent
    # file is exactly the case worth isolating from its neighbours (#145).
    _PYCG_DECOMP_FLOOR: int = 1
    _PYCG_MAX_DECOMP_ROUNDS: int = 6

    # Directory names that should never be fed to PyCG as entry points, nor
    # followed into during import resolution (an in-tree .codeanalyzer venv /
    # site-packages lives under project_dir and would otherwise be pulled into
    # the package bound and analysed — see _shard_symlink_root).
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
        shard_strategy: str = "jedi",
        max_iter: Optional[int] = None,
        using_ray: bool = False,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.skip_tests = skip_tests
        self.shard = shard
        self.shard_ceiling = (
            shard_ceiling if shard_ceiling is not None else self._PYCG_SHARD_CEILING
        )
        self.max_iter = max_iter if max_iter is not None else self._PYCG_MAX_ITER
        # "jedi": partition the Jedi module graph (SCC + Louvain) so coupled
        # modules co-compute and few edges are severed (see shard_planner).
        # "package": legacy one-shard-per-package-directory grouping.
        self.shard_strategy = shard_strategy
        self.using_ray = using_ray
        self._CallGraphGenerator: Optional[Any] = None
        self._resolver: Optional["_PyCGCallableResolver"] = None

    @staticmethod
    def _coalesce_edges(edges: List[PyCallEdge]) -> List[PyCallEdge]:
        """Sum weights of duplicate ``(src, dst)`` pairs across shards.

        Provenance is the sorted union, matching ``call_graph.merge_edges`` --
        the two must not coalesce differently. Inputs are left untouched; the
        merged edge is a copy.
        """
        merged: Dict[tuple, PyCallEdge] = {}
        for edge in edges:
            key = (edge.src, edge.dst)
            current = merged.get(key)
            if current is None:
                merged[key] = edge.model_copy()
            else:
                current.weight += edge.weight
                current.prov = sorted(set(current.prov) | set(edge.prov))
        return list(merged.values())

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
        # Sorted for run-to-run stability: rglob yields filesystem order, and
        # PyCG's capped fixpoint is sensitive to entry-point order (issue #99).
        return sorted(paths)

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
    ) -> Tuple[List[PyCallEdge], bool]:
        """Run PyCG on *entry_points* with *package_dir* as the package root.

        Returns ``(edges, converged)``; ``converged`` is False when PyCG stopped
        at ``max_iter`` instead of reaching its fixpoint (#145).

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
                max_iter=self.max_iter,
                operation="call-graph",
            )
            converged = _analyze_with_convergence(cg)
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
            PyCallEdge(src=src, dst=dst, weight=count, prov=["pycg"])
            for (src, dst), count in edge_counts.items()
        ], converged

    # ------------------------------------------------------------------
    # Sharded analysis
    # ------------------------------------------------------------------

    def _build_sharded_planned(
        self,
        jedi_edges: List[PyCallEdge],
        symbol_table: Dict[str, PyModule],
        resolver: "_PyCGCallableResolver",
    ) -> List[PyCallEdge]:
        """Coupling-aware sharding with iterative decomposition of runaways.

        Shards are chosen to *minimise the call edges severed between shards*:
        :func:`shard_planner.plan_shards` condenses the Jedi call graph by
        strongly-connected component (so import cycles never split) and clusters
        it with Louvain so tightly-coupled modules land together.  Each shard is
        run through PyCG via a symlinked mini-project that bounds analysis to its
        files.

        PyCG's fixpoint diverges on heavy metaclass/mixin clusters, and a uniform
        ceiling would force *every* shard small (severing many edges) just to tame
        the few that run away.  Instead we start coarse (low cut, high recall on
        healthy code) and **only re-decompose the shards that did not converge**:
        each runaway's files are re-partitioned at half the budget and re-run,
        down to a floor.  A smaller shard has a smaller fixpoint to reach, so
        splitting recovers the edges the capped pass missed while paying cut on
        its internal seams alone.

        A shard is a runaway when its fixpoint stopped at ``--pycg-max-iter``
        rather than converging — a function of the input, so the same project
        decomposes the same way every run.  This used to be a wall-clock
        timeout, which made *which* shards were dropped depend on machine load
        and Ray scheduling (#145).

        The residue that still diverges at the floor (or is an atomic cycle that
        won't split) keeps the edges its capped fixpoint produced: a truncated
        fixpoint is a sound under-approximation, so those edges are real and
        dropping them was pure recall loss.
        """
        self._resolver = resolver
        plan = plan_shards(
            symbol_table, jedi_edges, budget=self.shard_ceiling, merge_small=True
        )
        m = plan.metrics
        logger.info(
            "PyCG: planned %d shard(s) from Jedi module graph "
            "(cut_ratio=%.3f, max_shard=%d files, %d modules)",
            int(m["num_shards"]), m["cut_ratio"],
            int(m["max_shard_files"]), int(m["modules"]),
        )

        runner = (
            self._run_fileset_shards_ray if self.using_ray
            else self._run_fileset_shards_seq
        )
        all_edges: List[PyCallEdge] = []
        shards = plan.shards
        budget = self.shard_ceiling
        converged_total = 0
        irreducible_files = 0
        round_no = 0

        while shards:
            label = "decomposition round %d (budget %d, %d shard(s))" % (
                round_no, budget, len(shards),
            )
            logger.info("PyCG: %s", label)
            edges, runaways = runner(shards)
            all_edges.extend(edges)
            converged_total += len(shards) - len(runaways)
            if not runaways:
                break

            next_budget = max(self._PYCG_DECOMP_FLOOR, budget // 2)
            stop_decomposing = (
                round_no >= self._PYCG_MAX_DECOMP_ROUNDS or next_budget >= budget
            )

            next_shards: List[List[str]] = []
            for rf, partial in runaways:
                # Re-partition this runaway's files alone, at a tighter budget.
                # An atomic cycle (or a lone file) that won't shrink is
                # irreducible — keep the capped-fixpoint edges it did produce.
                # A truncated fixpoint is a sound under-approximation, so
                # keeping it strictly beats the previous behaviour of dropping
                # the shard to zero edges (#145). Its sub-shards supersede it
                # when it CAN be split, so the partial is only used here.
                sub_st = {f: symbol_table[f] for f in rf if f in symbol_table}
                if stop_decomposing or len(rf) <= 1:
                    irreducible_files += len(rf)
                    all_edges.extend(partial)
                    continue
                sub_plan = plan_shards(sub_st, jedi_edges, budget=next_budget)
                if len(sub_plan.shards) <= 1:
                    # did not actually split (one atomic SCC) — keep its partial
                    irreducible_files += len(rf)
                    all_edges.extend(partial)
                    continue
                next_shards.extend(sub_plan.shards)

            if not next_shards:
                break
            logger.info(
                "PyCG: %d shard(s) ran away — decomposing into %d sub-shard(s) "
                "at budget %d", len(runaways), len(next_shards), next_budget,
            )
            shards, budget = next_shards, next_budget
            round_no += 1

        if irreducible_files:
            logger.warning(
                "PyCG: %d file(s) in irreducibly-divergent shards kept their "
                "capped-fixpoint edges (sound under-approximation)", irreducible_files,
            )

        result = self._coalesce_edges(all_edges)
        logger.info(
            "PyCG: %d edges from %d converged shard(s) over %d round(s) "
            "(%d before dedup, Jedi-planned%s)",
            len(result), converged_total, round_no + 1, len(all_edges),
            ", Ray-parallel" if self.using_ray else "",
        )
        return result

    def _run_fileset_shards_seq(
        self, shards: List[List[str]],
    ) -> Tuple[List[PyCallEdge], List[Tuple[List[str], List[PyCallEdge]]]]:
        """Run each file-set shard sequentially; return ``(edges, runaways)``.

        A shard is a *runaway* when PyCG stopped at ``max_iter`` instead of
        reaching its fixpoint, or when it raised. Both are deterministic
        functions of the input -- unlike the wall-clock timeout this replaced,
        which made the surviving edge set depend on machine load (#145).

        Each runaway carries the edges it *did* produce. A capped fixpoint is a
        sound under-approximation, so if decomposition cannot split the shard
        further the caller keeps that partial rather than discarding it.
        """
        resolver = self._resolver
        edges_all: List[PyCallEdge] = []
        runaways: List[Tuple[List[str], List[PyCallEdge]]] = []
        with ProgressBar(len(shards), "Building call graph shards", item_label="shards") as progress:
            for files in shards:
                try:
                    with _shard_symlink_root(files, self.project_dir) as (root, eps):
                        edges, converged = self._run_pycg_batch(
                            eps, root, resolver, prefix=""
                        )
                    if converged:
                        edges_all.extend(edges)
                    else:
                        runaways.append((files, edges))
                except PyCGExceptions.PyCGAnalysisError:
                    runaways.append((files, []))
                progress.advance()
        return edges_all, runaways

    def _run_fileset_shards_ray(
        self, shards: List[List[str]],
    ) -> Tuple[List[PyCallEdge], List[Tuple[List[str], List[PyCallEdge]]]]:
        """Ray-parallel variant of :meth:`_run_fileset_shards_seq`.

        Each shard is materialised as a symlink mini-project up front (the trees
        must outlive their remote tasks) and submitted as a Ray task. Every task
        is collected -- there is no wall-clock deadline. A shard is a runaway
        only when PyCG stopped at ``max_iter`` or the task raised, both
        deterministic in the input (#145). The previous deadline cancelled
        whichever tasks happened to be slowest, so the surviving edge set varied
        with machine load: three runs over one fixture produced 48,595 / 43,431 /
        40,224 edges.
        """
        import os
        import ray
        from codeanalyzer.core import _ensure_ray
        _ensure_ray()

        os.environ.setdefault("RAY_IGNORE_UNHANDLED_ERRORS", "1")
        remote_fn = ray.remote(_pycg_shard_worker)

        roots: List[Path] = []
        lock_fds: List[int] = []
        futures: List[Any] = []
        meta: Dict[Any, List[str]] = {}  # ObjectRef -> shard file list
        edges_all: List[PyCallEdge] = []
        runaways: List[Tuple[List[str], List[PyCallEdge]]] = []
        try:
            with ProgressBar(len(shards), "Building call graph shards (parallel)", item_label="shards") as progress:
                for files in shards:
                    # Deterministic roots can collide across concurrent
                    # analyses of the same project — the driver holds each
                    # shard's sidecar lock for the whole Ray fan-out (released
                    # in the finally below with the root cleanup).
                    lock_fd = os.open(
                        str(_shard_root_path(files, self.project_dir).with_suffix(".lock")),
                        os.O_CREAT | os.O_RDWR,
                    )
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                    lock_fds.append(lock_fd)
                    root, eps = _materialize_shard_root(files, self.project_dir)
                    roots.append(root)
                    fut = remote_fn.remote(eps, str(root), "", self.max_iter)
                    futures.append(fut)
                    meta[fut] = files

                # No deadline: every task is collected. PyCG is bounded by
                # `max_iter`, so a shard terminates on its own; bounding it again
                # by the clock is what made the output load-dependent (#145).
                pending = list(futures)
                while pending:
                    ready, pending = ray.wait(pending, num_returns=1)
                    fut = ready[0]
                    try:
                        triples, converged = ray.get(fut)
                        edges = [
                            PyCallEdge(src=s, dst=t, weight=w, prov=["pycg"])
                            for s, t, w in triples
                        ]
                        if converged:
                            edges_all.extend(edges)
                        else:
                            runaways.append((meta[fut], edges))
                    except Exception:
                        runaways.append((meta[fut], []))
                    progress.advance()
        finally:
            for root in roots:
                shutil.rmtree(root, ignore_errors=True)
            for fd in lock_fds:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                except OSError:
                    pass
        return edges_all, runaways

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

        if self.using_ray:
            return self._build_sharded_ray(shards)

        all_edges: List[PyCallEdge] = []
        skipped = 0
        with ProgressBar(len(shards), "Building call graph shards", item_label="shards") as progress:
            for pkg_root, files in shards.items():
                n = len(files)
                pkg_label = str(pkg_root.relative_to(self.project_dir)) or "."
                if n > self.shard_ceiling:
                    logger.warning(
                        "PyCG shard '%s': %d files exceeds shard ceiling of %d — skipped",
                        pkg_label, n, self.shard_ceiling,
                    )
                    skipped += 1
                    progress.advance()
                    continue
                prefix = self._package_prefix(pkg_root, self.project_dir)
                try:
                    # No wall-clock bound: PyCG terminates on `max_iter`, and
                    # timing out here made the edge set load-dependent (#145).
                    # A capped fixpoint still yields sound edges, so keep them.
                    edges, converged = self._run_pycg_batch(
                        files, pkg_root, resolver, prefix=prefix
                    )
                    all_edges.extend(edges)
                    if not converged:
                        logger.debug(
                            "PyCG shard '%s': fixpoint capped at max_iter", pkg_label,
                        )
                    logger.debug(
                        "PyCG shard '%s': %d edges from %d files",
                        pkg_label, len(edges), n,
                    )
                except PyCGExceptions.PyCGAnalysisError as exc:
                    logger.warning("PyCG shard '%s' failed — skipped: %s", pkg_label, exc)
                    skipped += 1
                progress.advance()

        if skipped:
            logger.warning(
                "PyCG: %d shard(s) were skipped (exceeded %d-file ceiling "
                "or failed)",
                skipped, self.shard_ceiling,
            )

        # Merge duplicate (source, target) pairs that appear in multiple shards.
        merged: Dict[tuple, PyCallEdge] = {}
        for edge in all_edges:
            key = (edge.src, edge.dst)
            if key in merged:
                existing = merged[key]
                merged[key] = PyCallEdge(
                    source=existing.source,
                    target=existing.target,
                    weight=existing.weight + edge.weight,
                    prov=existing.prov,
                )
            else:
                merged[key] = edge

        result = list(merged.values())
        logger.info(
            "PyCG: %d edges from %d/%d shard(s) (%d before dedup)",
            len(result), len(shards) - skipped, len(shards), len(all_edges),
        )
        return result

    def _build_sharded_ray(self, shards: Dict[Path, List[str]]) -> List[PyCallEdge]:
        """Ray-parallel variant of the sequential shard loop.

        All eligible shards are submitted as Ray remote tasks simultaneously
        and every one is collected.  PyCG is bounded by ``max_iter``, so a
        shard terminates on its own; there is no wall-clock deadline (#145).
        """
        import os
        import ray
        from codeanalyzer.core import _ensure_ray
        _ensure_ray()

        # force-cancel kills worker processes; suppress Ray's "worker died
        # unexpectedly" noise since the death is intentional here.
        os.environ.setdefault("RAY_IGNORE_UNHANDLED_ERRORS", "1")

        remote_fn = ray.remote(_pycg_shard_worker)
        futures: List[Any] = []
        meta: Dict[Any, tuple] = {}  # ObjectRef -> (pkg_label, n_files)
        skipped = 0

        all_edges: List[PyCallEdge] = []
        with ProgressBar(len(shards), "Building call graph shards (parallel)", item_label="shards") as progress:
            for pkg_root, files in shards.items():
                n = len(files)
                pkg_label = str(pkg_root.relative_to(self.project_dir)) or "."
                if n > self.shard_ceiling:
                    logger.warning(
                        "PyCG shard '%s': %d files exceeds shard ceiling of %d — skipped",
                        pkg_label, n, self.shard_ceiling,
                    )
                    skipped += 1
                    progress.advance()
                    continue
                prefix = self._package_prefix(pkg_root, self.project_dir)
                fut = remote_fn.remote(files, str(pkg_root), prefix, self.max_iter)
                futures.append(fut)
                meta[fut] = (pkg_label, n)

            # Collect results one shard at a time so the progress bar ticks per
            # completed shard. Every task is collected -- no deadline. PyCG is
            # bounded by `max_iter`, so bounding it again by the clock only made
            # the surviving edge set depend on machine load (#145).
            pending = list(futures)
            while pending:
                ready, pending = ray.wait(pending, num_returns=1)
                fut = ready[0]
                pkg_label, n = meta[fut]
                try:
                    triples, converged = ray.get(fut)
                    edges = [
                        PyCallEdge(src=s, dst=t, weight=w, prov=["pycg"])
                        for s, t, w in triples
                    ]
                    all_edges.extend(edges)
                    logger.debug(
                        "PyCG shard '%s': %d edges from %d files (Ray)%s",
                        pkg_label, len(edges), n,
                        "" if converged else " [fixpoint capped at max_iter]",
                    )
                except Exception as exc:
                    logger.warning("PyCG shard '%s' failed — skipped: %s", pkg_label, exc)
                    skipped += 1
                progress.advance()

        if skipped:
            logger.warning(
                "PyCG: %d shard(s) were skipped (exceeded %d-file ceiling "
                "or failed)",
                skipped, self.shard_ceiling,
            )

        merged: Dict[tuple, PyCallEdge] = {}
        for edge in all_edges:
            key = (edge.src, edge.dst)
            if key in merged:
                existing = merged[key]
                merged[key] = PyCallEdge(
                    source=existing.source,
                    target=existing.target,
                    weight=existing.weight + edge.weight,
                    prov=existing.prov,
                )
            else:
                merged[key] = edge

        result = list(merged.values())
        logger.info(
            "PyCG: %d edges from %d/%d shard(s) (%d before dedup, Ray-parallel)",
            len(result), len(shards) - skipped, len(shards), len(all_edges),
        )
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_call_graph_edges(
        self,
        symbol_table: Dict[str, PyModule],
        jedi_edges: Optional[List[PyCallEdge]] = None,
    ) -> List[PyCallEdge]:
        """Run PyCG and return ``PyCallEdge`` entries with ``prov=["pycg"]``.

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
        t0 = time.perf_counter()

        if n_files > self._PYCG_FILE_CEILING:
            if self.shard:
                if self.shard_strategy == "jedi" and jedi_edges is not None:
                    logger.info(
                        "PyCG: starting Jedi-planned sharded analysis (%d files)",
                        n_files,
                    )
                    edges = self._build_sharded_planned(
                        jedi_edges, symbol_table, resolver
                    )
                else:
                    mode = "Ray-parallel" if self.using_ray else "sequential"
                    logger.info(
                        "PyCG: starting per-package sharded analysis (%d files, %s)",
                        n_files, mode,
                    )
                    edges = self._build_sharded(entry_points, resolver)
            else:
                logger.warning(
                    "PyCG: %d entry points exceeds ceiling of %d — "
                    "skipping pointer analysis (Jedi-only edges will be used). "
                    "Re-run with --pycg-shard to analyse per package shard.",
                    n_files, self._PYCG_FILE_CEILING,
                )
                return []
        else:
            # Small project (≤ ceiling): whole-project analysis.  Run inside a
            # symlink mini-project mirroring only the (already SKIP_DIRS-filtered)
            # entry points, so PyCG's package bound covers project source alone.
            # Pointing PyCG at project_dir directly would put an in-tree
            # .codeanalyzer venv / site-packages *under* mod_dir, and PyCG would
            # follow imports into those dependencies and explode the analysis.
            logger.info("PyCG: starting whole-project call graph analysis (%d files)", n_files)
            with _shard_symlink_root(entry_points, self.project_dir) as (root, eps):
                edges, converged = self._run_pycg_batch(eps, root, resolver, prefix="")
                if not converged:
                    logger.warning(
                        "PyCG: fixpoint capped at max_iter=%d — edges are a sound "
                        "under-approximation", self.max_iter,
                    )

        edges = _canonicalize_edges(edges)
        elapsed = time.perf_counter() - t0
        logger.info("✅ PyCG: %d edges in %.1fs", len(edges), elapsed)
        return edges
