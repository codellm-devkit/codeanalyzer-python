import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union, List

import time

import ray
from codeanalyzer.utils import logger
from codeanalyzer.schema import (
    Analysis,
    PyApplication,
    PyExternalSymbol,
    PyModule,
    model_dump_json,
    model_validate_json,
)
from codeanalyzer.schema.assign_ids import assign_ids
from codeanalyzer.schema.l1_body import populate_l1_body
from codeanalyzer.schema.l2_callees import backfill_callees
from codeanalyzer.schema.call_graph_ids import reidentify_call_graph
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.semantic_analysis.call_graph import (
    filter_external_edges,
    jedi_call_graph_edges,
    merge_edges,
    resolve_unresolved_constructors,
)
from codeanalyzer.semantic_analysis.pycg import PyCG, PyCGExceptions
from codeanalyzer.syntactic_analysis.exceptions import SymbolTableBuilderRayError
from codeanalyzer.syntactic_analysis.import_resolver import resolve_imports
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder
from codeanalyzer.utils import ProgressBar
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.provenance import analyzer_info, repository_info

def _ensure_ray() -> None:
    """Initialize Ray with the driver's pinned hash seed in the workers.

    An implicit auto-init would not carry PYTHONHASHSEED into worker
    interpreters, so PyCG shards (and Jedi inference) run there with random
    set-iteration order and the emitted edges vary run to run (issue #99)."""
    if not ray.is_initialized():
        ray.init(
            runtime_env={
                "env_vars": {
                    "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "0")
                }
            },
        )


@ray.remote
def _process_file_with_ray(py_file: Union[Path, str], project_dir: Union[Path, str], virtualenv: Union[Path, str, None]) -> Dict[str, PyModule]:
    """Processes files in the project directory using Ray for distributed processing.
    
    Args:
        py_file (Union[Path, str]): Path to the Python file to process.
        project_dir (Union[Path, str]): Path to the project directory.
        virtualenv (Union[Path, str, None]): Path to the virtual environment directory.
    Returns:
        Dict[str, PyModule]: A dictionary mapping file paths to PyModule objects.
    """
    from rich.console import Console
    console = Console()
    module_map: Dict[str, PyModule] = {}
    try:
        py_file = Path(py_file)
        symbol_table_builder = SymbolTableBuilder(project_dir, virtualenv)
        module_map[str(py_file.relative_to(Path(project_dir)))] = symbol_table_builder.build_pymodule_from_file(py_file)
    except Exception as e:
        console.log(f"❌ Failed to process {py_file}: {e}")
        raise SymbolTableBuilderRayError(f"Ray processing error for {py_file}: {e}")
    return module_map


class Codeanalyzer:
    """Core static analysis engine for Python projects.

    Args:
        options (AnalysisOptions): Analysis configuration options containing all necessary parameters.
    """

    def __init__(self, options: AnalysisOptions) -> None:
        self.options = options
        self.project_dir = Path(options.input).resolve()
        self.skip_tests = options.skip_tests
        self.analysis_level = options.analysis_level
        self.rebuild_analysis = options.rebuild_analysis
        self.no_venv = options.no_venv
        self.cache_dir = (
            options.cache_dir.resolve() if options.cache_dir is not None else self.project_dir
        ) / ".codeanalyzer"
        self.clear_cache = options.clear_cache
        self.virtualenv: Optional[Path] = None
        self.using_ray: bool = options.using_ray
        self.file_name: Optional[Path] = options.file_name

    @staticmethod
    def _cmd_exec_helper(
        cmd: List[str],
        cwd: Optional[Path] = None,
        capture_output: bool = True,
        check: bool = True,
        suppress_output: bool = False,
        log_on_failure: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        Runs a subprocess with real-time output streaming to the logger.

        Args:
            cmd: Command as a list of arguments.
            cwd: Working directory to run the command in.
            capture_output: If True, retains and returns the output.
            check: If True, raises CalledProcessError on non-zero exit.
            suppress_output: If True, silences per-line debug output.
            log_on_failure: If False, suppresses the error-level log on
                non-zero exit (use when the caller handles the exception and
                will emit its own diagnostic).

        Returns:
            subprocess.CompletedProcess
        """
        logger.info(f"Running: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        assert process.stdout is not None  # for type checking
        output_lines = []

        for line in process.stdout:
            line = line.rstrip()
            if not suppress_output:
                logger.debug(line)
            if capture_output:
                output_lines.append(line)

        returncode = process.wait()

        if check and returncode != 0:
            error_output = "\n".join(output_lines)
            if log_on_failure:
                logger.error(f"Command failed with exit code {returncode}: {' '.join(cmd)}")
                if error_output:
                    logger.error(f"Command output:\n{error_output}")
            raise subprocess.CalledProcessError(returncode, cmd, output=error_output)

        return subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout="\n".join(output_lines) if capture_output else None,
            stderr=None,
        )

    @staticmethod
    def _get_base_interpreter() -> Path:
        """Get the base Python interpreter path.

        This method finds a suitable base Python interpreter that can be used
        to create virtual environments, even when running from within a virtual environment.
        It supports various Python version managers like pyenv, conda, asdf, etc.

        Returns:
            Path: The base Python interpreter path.

        Raises:
            RuntimeError: If no suitable Python interpreter can be found.
        """
        # If we're not in a virtual environment, use the current interpreter
        if sys.prefix == sys.base_prefix:
            return Path(sys.executable)

        # We're inside a virtual environment; need to find the base interpreter

        # First, check if user explicitly set SYSTEM_PYTHON
        system_python = os.getenv("SYSTEM_PYTHON")
        if system_python:
            system_python_path = Path(system_python)
            if system_python_path.exists() and system_python_path.is_file():
                return system_python_path

        # Try to get the base interpreter from sys.base_executable (Python 3.3+)
        if hasattr(sys, "base_executable") and sys.base_executable:
            base_exec = Path(sys.base_executable)
            if base_exec.exists() and base_exec.is_file():
                return base_exec

        # Try to find Python interpreters using shlex.which
        python_candidates = []

        # Use shutil.which to find python3 and python in PATH
        for python_name in ["python3", "python"]:
            python_path = shutil.which(python_name)
            if python_path:
                candidate = Path(python_path)
                # Skip if this is the current virtual environment's python
                if not str(candidate).startswith(sys.prefix):
                    python_candidates.append(candidate)

        # Check pyenv installation
        pyenv_root = os.getenv("PYENV_ROOT")
        if pyenv_root:
            pyenv_python = Path(pyenv_root) / "shims" / "python"
            if pyenv_python.exists():
                python_candidates.append(pyenv_python)

        # Check default pyenv location
        home_pyenv = Path.home() / ".pyenv" / "shims" / "python"
        if home_pyenv.exists():
            python_candidates.append(home_pyenv)

        # Check conda base environment
        conda_base = os.getenv("CONDA_PREFIX")
        if conda_base:
            conda_python = Path(conda_base) / "bin" / "python"
            if conda_python.exists():
                python_candidates.append(conda_python)

        # Check asdf
        asdf_dir = os.getenv("ASDF_DIR")
        # If ASDF_DIR is set, use its shims directory
        # Otherwise, check if asdf is installed in the default location
        if asdf_dir:
            asdf_python = Path(asdf_dir) / "shims" / "python"
            if asdf_python.exists():
                python_candidates.append(asdf_python)

        # Test candidates to find a working Python interpreter
        for candidate in python_candidates:
            try:
                # Test if the interpreter works and can create venv
                result = subprocess.run(
                    [str(candidate), "-c", "import venv; print('OK')"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and "OK" in result.stdout:
                    return candidate
            except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
                continue

        # If nothing works, raise an informative error
        raise RuntimeError(
            f"Could not find a suitable base Python interpreter. "
            f"Current environment: {sys.executable} (prefix: {sys.prefix}). "
            f"Please set the SYSTEM_PYTHON environment variable to point to "
            f"a working Python interpreter that can create virtual environments."
        )

    @staticmethod
    def _uv_bin() -> Optional[str]:
        """Path to the uv binary bundled with the ``uv`` PyPI package (a declared
        dependency, so always present in our install -- including inside a Docker
        image). We deliberately ignore any uv on PATH so the analyzer always uses
        the pinned, vendored uv. Returns ``None`` only if the package is somehow
        missing (callers fall back to pip)."""
        try:
            from uv import find_uv_bin
            return str(find_uv_bin())
        except Exception:
            return None

    def _install_into_venv(self, venv_python: Path, args: List[str]) -> None:
        """Install packages into the target venv, preferring uv for speed (parallel
        downloads + a shared global cache) and falling back to the venv's own pip
        when uv is unavailable.

        Raises ``subprocess.CalledProcessError`` on failure; callers in
        ``__enter__`` catch this and warn-and-continue so a single failing
        package (e.g. a C extension that needs system libs) does not abort the
        entire analysis.
        """
        uv = self._uv_bin()
        if uv:
            cmd = [uv, "pip", "install", "--python", str(venv_python), *args]
        else:
            cmd = [str(venv_python), "-m", "pip", "install", *args]
        self._cmd_exec_helper(
            cmd, cwd=self.project_dir, check=True,
            suppress_output=True, log_on_failure=False,
        )

    def __enter__(self) -> "Codeanalyzer":
        # If no virtualenv is provided, try to create one using requirements.txt or pyproject.toml
        venv_path = self.cache_dir / self.project_dir.name / "virtualenv"
        # Ensure the cache directory exists for this project
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        if self.no_venv:
            logger.info(
                "--no-venv: using the ambient Python environment "
                "(skipping virtualenv creation and dependency installation)"
            )
        # Create the virtual environment if it does not exist
        if not self.no_venv and (not venv_path.exists() or self.rebuild_analysis):
            logger.info(f"(Re-)creating virtual environment at {venv_path}")
            self._cmd_exec_helper(
                [str(self._get_base_interpreter()), "-m", "venv", str(venv_path)],
                check=True,
            )
            # Find python in the virtual environment
            venv_python = venv_path / "bin" / "python"

            # First, install dependencies from various dependency files
            dependency_files = [
                ("requirements.txt", ["-r"]),
                ("requirements-dev.txt", ["-r"]),
                ("dev-requirements.txt", ["-r"]),
                ("test-requirements.txt", ["-r"]),
            ]

            for dep_file, _ in dependency_files:
                if (self.project_dir / dep_file).exists():
                    logger.info(f"Installing dependencies from {dep_file}")
                    try:
                        self._install_into_venv(
                            venv_python,
                            ["--upgrade", "-r", str(self.project_dir / dep_file)],
                        )
                    except subprocess.CalledProcessError as exc:
                        logger.warning(
                            f"Dependency installation from {dep_file} failed "
                            f"(exit {exc.returncode}) — continuing without it. "
                            "Jedi type resolution may be incomplete."
                        )

            # Handle Pipenv files
            if (self.project_dir / "Pipfile").exists():
                logger.info("Installing dependencies from Pipfile")
                try:
                    self._install_into_venv(venv_python, ["pipenv"])
                    self._cmd_exec_helper(
                        ["pipenv", "install", "--dev"],
                        cwd=self.project_dir,
                        check=True,
                    )
                except subprocess.CalledProcessError as exc:
                    logger.warning(
                        f"Pipenv installation failed (exit {exc.returncode}) — continuing without it."
                    )

            # Handle conda environment files
            conda_files = ["conda.yml", "environment.yml"]
            for conda_file in conda_files:
                if (self.project_dir / conda_file).exists():
                    logger.info(f"Found {conda_file} - note that conda environments should be handled outside this tool")
                    break

            # Now install the project itself in editable mode (only if package definition exists)
            package_definition_files = [
                "pyproject.toml",    # Modern Python packaging (PEP 518/621)
                "setup.py",          # Traditional setuptools
                "setup.cfg",         # Setup configuration
            ]

            if any((self.project_dir / file).exists() for file in package_definition_files):
                logger.info("Installing project in editable mode")
                try:
                    self._install_into_venv(venv_python, ["-e", str(self.project_dir)])
                except subprocess.CalledProcessError as exc:
                    logger.warning(
                        f"Editable install failed (exit {exc.returncode}) — "
                        "continuing without it. Jedi type resolution may be incomplete."
                    )
            else:
                logger.warning("No package definition files found, skipping editable installation")

        # Point Jedi at the analysis venv so it resolves the project's third-party
        # imports. This runs on both a fresh build and a lazy reuse of an existing
        # venv -- previously self.virtualenv stayed None, so the install above was
        # never actually used by the symbol-table builder. With --no-venv we leave
        # it None so Jedi resolves against the ambient interpreter instead.
        if not self.no_venv and venv_path.exists():
            self.virtualenv = venv_path

        return self

    def __exit__(self, *args, **kwargs) -> None:
        if self.clear_cache and self.cache_dir.exists():
            logger.info(f"Clearing cache directory: {self.cache_dir}")
            shutil.rmtree(self.cache_dir)

    @staticmethod
    def _home_external_symbols(app, app_id, sig_to_id):
        """Home every call-graph endpoint that is not a declared class/callable
        onto a ``can://…/@external/<module>/<name>`` id (the keystone edge-endpoint
        id home). Registers each homed id in ``sig_to_id`` so callee backfill and
        call-graph re-identity map the dotted signature to it, and returns the
        id-keyed external-symbol map. ``name``/``module`` are derived from the
        signature (best effort: split on the last dot)."""
        externals: Dict[str, PyExternalSymbol] = {}
        for edge in app.call_graph:
            for sig in (edge.src, edge.dst):
                if sig in sig_to_id:
                    continue
                module, name = sig.rsplit(".", 1) if "." in sig else (None, sig)
                ext_id = f"{app_id}/@external/{module}/{name}" if module else \
                    f"{app_id}/@external/{name}"
                sig_to_id[sig] = ext_id
                externals[ext_id] = PyExternalSymbol(
                    id=ext_id, name=name, module=module
                )
        return externals

    def analyze(self) -> Analysis:
        """Analyze the project and return the v2 ``Analysis`` envelope.

        Uses caching to avoid re-analyzing unchanged files.
        """
        cache_file = self.cache_dir / "analysis_cache.json"

        # Try to load existing cached analysis
        cached = None
        if not self.rebuild_analysis and cache_file.exists():
            try:
                cached = self._load_pyapplication_from_cache(cache_file)
                if cached is not None:
                    logger.info("Loaded cached analysis")
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}. Rebuilding analysis.")
                cached = None

        if not self._cache_analyzer_matches(cached, analyzer_info(self.analysis_level).version):
            if cached is not None:
                logger.info("Analysis cache written by a different analyzer version; rebuilding.")
            cached = None

        # Build symbol table from cached application if available (if no available, the build a new one)
        symbol_table = self._build_symbol_table(cached.application.symbol_table if cached else {})

        resolve_unresolved_constructors(symbol_table)

        # Level 1: Jedi call graph.
        t0_jedi = time.perf_counter()
        jedi_edges = jedi_call_graph_edges(symbol_table)
        call_graph = list(jedi_edges)
        logger.info("✅ Jedi: %d edges in %.1fs", len(call_graph), time.perf_counter() - t0_jedi)

        if self.analysis_level >= 2:
            # Level 2: also add PyCG edges. The Jedi edges double as the
            # coupling graph that drives coupling-aware PyCG sharding.
            pycg_edges = self._get_pycg_call_graph(symbol_table, jedi_edges)
            call_graph = merge_edges(call_graph, pycg_edges)

        call_graph = filter_external_edges(call_graph, symbol_table)
        # Canonical edge order: backend iteration order (PyCG dicts, Counter
        # insertion) is not a contract — sort so identical edge SETS always
        # serialize identically (issue #99 determinism gate), and so the
        # external-symbol homing below assigns ids in a stable order.
        call_graph.sort(key=lambda e: (e.src, e.dst))

        # Recreate pyapplication
        app = (
            PyApplication.builder()
            .symbol_table(symbol_table)
            .call_graph(call_graph)
            .build()
        )

        # Every run re-resolves import spellings against the analyzed module
        # set -- pure and cheap; cached modules from older caches default to
        # resolved_module=None and get stamped here (issue #82).
        resolve_imports(app, self.project_dir)

        # Single choke point for provenance: every produced app (fresh symbol
        # table or reused-from-cache) passes through here before being cached
        # or returned, so repository provenance always reflects *this* checkout
        # even when the symbol table itself came from the on-disk cache. The
        # analyzer identity rides the envelope below (keystone home).
        app.repository = repository_info(self.project_dir)

        app_name = self.options.app_name or self.project_dir.name
        sig_to_id = assign_ids(app, app_name)
        # Home call-graph endpoints that are not declared in the symbol table
        # (imported library / builtin members) onto @external ids once, so the
        # JSON and Neo4j backends share one authoritative external-symbol set
        # and every edge endpoint joins the id space (no dangling endpoints).
        app.external_symbols = self._home_external_symbols(app, app.id, sig_to_id)
        populate_l1_body(app)
        if self.analysis_level >= 2:
            backfill_callees(app, sig_to_id)
        reidentify_call_graph(app, sig_to_id)

        # L3: intraprocedural dataflow (CFG/CDG/DDG) emitted onto the v2 tree.
        if self.analysis_level >= 3:
            from codeanalyzer.dataflow.builder import (
                build_function_pdgs,
                emit_l3_body,
            )
            from codeanalyzer.dataflow.syntactic import SyntacticOracle

            infos, _func_asts = build_function_pdgs(
                app,
                k=self.options.graph_field_depth,
                oracle_factory=lambda c, fast: SyntacticOracle(),
            )
            emit_l3_body(app, infos, sig_to_id, set(self.options.graphs.split(",")))

        # L4: interprocedural dataflow (param vertices + summary + param_in/out)
        # layered on top of the L3 syntactic overlay (L3 ⊆ L4). Scalpel is the
        # primary may-alias oracle, with the type-based total fallback.
        if self.analysis_level >= 4:
            from codeanalyzer.dataflow.builder import (
                _base_types,
                build_program_graphs,
                emit_ddg_pointsto_delta,
                emit_l4,
            )
            from codeanalyzer.dataflow.scalpel_oracle import make_alias_oracle

            ir = build_program_graphs(
                app,
                k=self.options.graph_field_depth,
                oracle_factory=lambda c, fast: make_alias_oracle(
                    c, fast, _base_types(c)
                ),
            )
            emit_l4(app, ir, sig_to_id)
            # Semantic ddg delta: the alias-derived def-use edges the real
            # oracle adds beyond the L3 syntactic set, tagged prov=["points-to"].
            # ``infos`` are the syntactic (L3) PDGs from the >=3 block above.
            emit_ddg_pointsto_delta(app, infos, ir, sig_to_id)

        # Build the v2 envelope, then persist it (the cache stores the full
        # ``Analysis`` envelope so a reused cache round-trips schema_version).
        # k_limit is an L3+ envelope key: below the dataflow levels it stays
        # None and exclude_none drops it from the payload.
        analysis = Analysis(
            max_level=self.analysis_level,
            k_limit=self.options.graph_field_depth if self.analysis_level >= 3 else None,
            analyzer=analyzer_info(self.analysis_level),
            application=app,
        )
        self._save_analysis_cache(analysis, cache_file)

        return analysis

    @staticmethod
    def _cache_analyzer_matches(cached: Optional[Analysis], current_version: str) -> bool:
        """A cache written by another analyzer version (or before versions were
        recorded) may lack fields the current models populate — pydantic fills
        silent defaults, which would masquerade as analyzed absence. The
        analyzer identity lives on the envelope (keystone home)."""
        return (
            cached is not None
            and cached.analyzer is not None
            and cached.analyzer.version == current_version
        )

    def _load_pyapplication_from_cache(self, cache_file: Path) -> Optional[Analysis]:
        """Load a cached v2 ``Analysis`` envelope from file.

        A cache written by an older (v1) analyzer stored a bare
        ``PyApplication`` with no ``schema_version``; such a payload no longer
        validates as an ``Analysis`` (or carries the wrong ``schema_version``).
        In that case we log and return ``None`` so the caller treats it as a
        cache miss and rebuilds from scratch — rather than crashing.

        Args:
            cache_file: Path to the cache file

        Returns:
            Optional[Analysis]: The cached envelope, or ``None`` if the cache is
            stale/incompatible and should be rebuilt.
        """
        with cache_file.open('r') as f:
            data = f.read()
        try:
            cached = model_validate_json(Analysis, data)
        except Exception:
            logger.info("stale/incompatible analysis cache — rebuilding")
            return None
        if getattr(cached, "schema_version", None) != "2.0.0":
            logger.info("stale/incompatible analysis cache (schema_version) — rebuilding")
            return None
        # The cache keys only on file hash/mtime/size, not on level, so a cache
        # built at a different analysis_level would leak higher-level body/edge
        # content (or omit content when the cached level is lower). Reject the
        # mismatch and force a full rebuild at the requested level.
        if cached.max_level != self.analysis_level:
            logger.info(
                f"cache built at level {cached.max_level} != requested "
                f"{self.analysis_level} — rebuilding"
            )
            return None
        return cached

    def _save_analysis_cache(self, analysis: Analysis, cache_file: Path) -> None:
        """Save the v2 ``Analysis`` envelope to the cache file.

        Args:
            analysis: The Analysis envelope to cache
            cache_file: Path to save the cache file
        """
        # Ensure cache directory exists
        cache_file.parent.mkdir(parents=True, exist_ok=True)

        with cache_file.open('w') as f:
            f.write(model_dump_json(analysis, indent=2))

        logger.info(f"Analysis cached to {cache_file}")

    def _file_unchanged(self, file_path: Path, cached_module: PyModule) -> bool:
        """Check if a file has changed since it was cached.
        
        Args:
            file_path: Path to the file to check
            cached_module: The cached PyModule for this file
            
        Returns:
            bool: True if file is unchanged, False otherwise
        """
        try:
            # Check last modified time and file size
            if (cached_module.last_modified is not None and
                cached_module.file_size is not None and
                cached_module.last_modified == file_path.stat().st_mtime and
                cached_module.file_size == file_path.stat().st_size):
                return True
            # Also check content hash for extra safety
            if cached_module.content_hash is not None:
                content_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
                return content_hash == cached_module.content_hash

            # No cached metadata mismatch, assume file changed
            return False
            
        except Exception as e:
            logger.debug(f"Error checking file {file_path}: {e}")
            return False

    def _compute_checksum(self, root: Path) -> str:
        """Compute SHA256 checksum of all Python source files in a project directory. If somethings changes, the
        checksum will change and thus the analysis will be redone.

        Args:
            root (Path): Root directory of the project.

        Returns:
            str: SHA256 checksum of all Python files in the project.
        """
        sha256 = hashlib.sha256()
        for py_file in sorted(root.rglob("*.py")):
            sha256.update(py_file.read_bytes())
        return sha256.hexdigest()

    def _build_symbol_table(self, cached_symbol_table: Optional[Dict[str, PyModule]] = None) -> Dict[str, PyModule]:
        """Builds the symbol table for the project.

        This method scans the project directory, identifies Python files,
        and constructs a symbol table containing information about classes,
        functions, and variables defined in those files.
        
        Args:
            cached_app: Previously cached PyApplication to reuse unchanged files
        
        Returns:
            Dict[str, PyModule]: A dictionary mapping file paths to PyModule objects.
        """
        symbol_table: Dict[str, PyModule] = {}
        t0_st = time.perf_counter()

        # Handle single file analysis
        if self.file_name is not None:
            single_file = self.project_dir / self.file_name
            logger.info(f"Analyzing single file: {single_file}")

            # Check if file is in cache and unchanged
            file_key = str(single_file.relative_to(self.project_dir))
            if file_key in cached_symbol_table and not self.rebuild_analysis:
                # Compute file checksum to see if it changed
                if self._file_unchanged(single_file, cached_symbol_table[file_key]):
                    logger.info(f"Using cached analysis for {single_file}")
                    symbol_table[file_key] = cached_symbol_table[file_key]
                    return symbol_table
            
            # File is new or changed, analyze it
            try:
                symbol_table_builder = SymbolTableBuilder(self.project_dir, self.virtualenv)
                py_module = symbol_table_builder.build_pymodule_from_file(single_file)
                symbol_table[file_key] = py_module
                logger.info("✅ Single file analysis complete.")
                return symbol_table
            except Exception as e:
                logger.error(f"Failed to process {single_file}: {e}")
                return symbol_table
        
        # Get all Python files first to show accurate progress
        py_files = []
        for py_file in self.project_dir.rglob("*.py"):
            rel_path = py_file.relative_to(self.project_dir)
            path_parts = rel_path.parts
            filename = py_file.name

            # Skip directories we don't care about
            if (
                "site-packages" in path_parts
                or ".venv" in path_parts
                or ".codeanalyzer" in path_parts
            ):
                continue

            # Skip test files if enabled
            if self.skip_tests and (
                "test" in path_parts
                or "tests" in path_parts
                or filename.startswith("test_")
                or filename.endswith("_test.py")
            ):
                continue

            py_files.append(py_file)

        if self.using_ray:
            logger.info("Using Ray for distributed symbol table generation.")
            # Separate files into cached and new/changed
            files_to_process = []
            for py_file in py_files:
                file_key = str(py_file.relative_to(self.project_dir))
                if file_key in cached_symbol_table and not self.rebuild_analysis:
                    if self._file_unchanged(py_file, cached_symbol_table[file_key]):
                        # Use cached version
                        symbol_table[file_key] = cached_symbol_table[file_key]
                        continue
                files_to_process.append(py_file)
            
            # Process only new/changed files with Ray
            if files_to_process:
                _ensure_ray()
                futures = [_process_file_with_ray.remote(py_file, self.project_dir, str(self.virtualenv) if self.virtualenv else None) for py_file in files_to_process]
                
                with ProgressBar(len(futures), "Building symbol table (parallel)") as progress:
                    pending = futures[:]
                    while pending:
                        done, pending = ray.wait(pending, num_returns=1)
                        result = ray.get(done[0])
                        if result:
                            symbol_table.update(result)
                        progress.advance()
        else:
            logger.info("Building symbol table serially.")
            symbol_table_builder = SymbolTableBuilder(self.project_dir, self.virtualenv)
            files_processed = 0
            files_from_cache = 0
            
            with ProgressBar(len(py_files), "Building symbol table") as progress:
                for py_file in py_files:
                    file_key = str(py_file.relative_to(self.project_dir))
                    
                    # Check if file is cached and unchanged
                    if file_key in cached_symbol_table and not self.rebuild_analysis:
                        if self._file_unchanged(py_file, cached_symbol_table[file_key]):
                            symbol_table[file_key] = cached_symbol_table[file_key]
                            files_from_cache += 1
                            progress.advance()
                            continue
                    
                    # File is new or changed, analyze it
                    try:
                        py_module = symbol_table_builder.build_pymodule_from_file(py_file)
                        symbol_table[file_key] = py_module
                        files_processed += 1
                    except Exception as e:
                        logger.error(f"Failed to process {py_file}: {e}")
                    progress.advance()
            
            if files_from_cache > 0:
                logger.info(f"Reused {files_from_cache} files from cache, processed {files_processed} new/changed files")

        logger.info(
            "✅ Symbol table: %d modules in %.1fs",
            len(symbol_table), time.perf_counter() - t0_st,
        )
        return symbol_table

    def _get_pycg_call_graph(
        self,
        symbol_table: Dict[str, PyModule],
        jedi_edges: List[PyCallEdge],
    ) -> List[PyCallEdge]:
        """Build PyCG-resolved call edges.

        Runs PyCG's iterative name-pointer analysis over the whole project
        and returns edges with ``prov=["pycg"]``.  Falls back to an
        empty list and logs a warning on any failure so the caller can
        continue with Jedi-only edges.

        *jedi_edges* are the level-1 call edges; under the ``jedi`` shard
        strategy they drive coupling-aware partitioning (see
        :func:`shard_planner.plan_shards`).
        """
        try:
            pycg = PyCG(
                self.project_dir,
                skip_tests=self.skip_tests,
                shard=self.options.pycg_shard,
                shard_ceiling=self.options.pycg_shard_ceiling,
                shard_timeout=self.options.pycg_shard_timeout,
                shard_strategy=self.options.pycg_shard_strategy,
                max_iter=self.options.pycg_max_iter,
                using_ray=self.using_ray,
            )
            return pycg.build_call_graph_edges(symbol_table, jedi_edges=jedi_edges)
        except PyCGExceptions.PyCGImportError as exc:
            logger.warning(f"PyCG not installed — level 2 edges will be Jedi-only: {exc}")
            return []
        except PyCGExceptions.PyCGAnalysisError as exc:
            logger.warning(f"PyCG analysis failed — level 2 edges will be Jedi-only: {exc}")
            logger.debug("PyCG full traceback:", exc_info=True)
            return []