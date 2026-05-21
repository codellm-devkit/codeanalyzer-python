import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union, List

import ray
from codeanalyzer.utils import logger
from codeanalyzer.schema import PyApplication, PyModule, model_dump_json, model_validate_json
from codeanalyzer.schema.py_schema import PyCallEdge
from codeanalyzer.analysis import run_pipeline
from codeanalyzer.semantic_analysis.call_graph import (
    jedi_call_graph_edges,
    merge_edges,
    resolve_unresolved_constructors,
)
from codeanalyzer.semantic_analysis.codeql import CodeQLLoader
from codeanalyzer.semantic_analysis.codeql.codeql_analysis import CodeQL
from codeanalyzer.semantic_analysis.codeql.codeql_exceptions import CodeQLExceptions
from codeanalyzer.syntactic_analysis.exceptions import SymbolTableBuilderRayError
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder
from codeanalyzer.utils import ProgressBar
from codeanalyzer.options import AnalysisOptions

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
        module_map[str(py_file)] = symbol_table_builder.build_pymodule_from_file(py_file)
    except Exception as e:
        console.log(f"❌ Failed to process {py_file}: {e}")
        raise SymbolTableBuilderRayError(f"Ray processing error for {py_file}: {e}")
    return module_map


class Codeanalyzer:
    """Core functionality for CodeQL analysis.

    Args:
        options (AnalysisOptions): Analysis configuration options containing all necessary parameters.
    """

    def __init__(self, options: AnalysisOptions) -> None:
        self.options = options
        self.project_dir = Path(options.input).resolve()
        self.skip_tests = options.skip_tests
        self.using_codeql = options.using_codeql
        self.rebuild_analysis = options.rebuild_analysis
        self.cache_dir = (
            options.cache_dir.resolve() if options.cache_dir is not None else self.project_dir
        ) / ".codeanalyzer"
        self.clear_cache = options.clear_cache
        self.db_path: Optional[Path] = None
        self.codeql_bin: Optional[Path] = None
        self.codeql_packs_dir: Optional[Path] = None
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
    ) -> subprocess.CompletedProcess:
        """
        Runs a subprocess with real-time output streaming to the logger.

        Args:
            cmd: Command as a list of arguments.
            cwd: Working directory to run the command in.
            capture_output: If True, retains and returns the output.
            check: If True, raises CalledProcessError on non-zero exit.
            suppress_output: If True, silences log output.

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

    def __enter__(self) -> "Codeanalyzer":
        # If no virtualenv is provided, try to create one using requirements.txt or pyproject.toml
        venv_path = self.cache_dir / self.project_dir.name / "virtualenv"
        # Ensure the cache directory exists for this project
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        # Create the virtual environment if it does not exist
        if not venv_path.exists() or self.rebuild_analysis:
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

            for dep_file, pip_args in dependency_files:
                if (self.project_dir / dep_file).exists():
                    logger.info(f"Installing dependencies from {dep_file}")
                    self._cmd_exec_helper(
                        [str(venv_python), "-m", "pip", "install", "-U"] + pip_args + [str(self.project_dir / dep_file)],
                        cwd=self.project_dir,
                        check=True,
                    )

            # Handle Pipenv files
            if (self.project_dir / "Pipfile").exists():
                logger.info("Installing dependencies from Pipfile")
                # Note: This would require pipenv to be installed
                self._cmd_exec_helper(
                    [str(venv_python), "-m", "pip", "install", "pipenv"],
                    cwd=self.project_dir,
                    check=True,
                )
                self._cmd_exec_helper(
                    ["pipenv", "install", "--dev"],
                    cwd=self.project_dir,
                    check=True,
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
                self._cmd_exec_helper(
                    [str(venv_python), "-m", "pip", "install", "-e", str(self.project_dir)],
                    cwd=self.project_dir,
                    check=True,
                )
            else:
                logger.warning("No package definition files found, skipping editable installation")

        if self.using_codeql:
            logger.info(f"(Re-)initializing CodeQL analysis for {self.project_dir}")

            # Resolve the CLI binary before anything else uses it: DB build
            # below needs it, and so does every subsequent query run.
            self.codeql_bin = self._ensure_codeql_bin()
            # Download the standard query library pack (idempotent). The
            # CLI install ships only the language extractors; the
            # ``codeql/python-all`` library pack must be fetched separately.
            self.codeql_packs_dir = self._ensure_codeql_packs(self.codeql_bin)

            cache_root = self.cache_dir / "codeql"
            cache_root.mkdir(parents=True, exist_ok=True)
            self.db_path = cache_root / f"{self.project_dir.name}-db"
            self.db_path.mkdir(exist_ok=True)

            checksum_file = self.db_path / ".checksum"
            current_checksum = self._compute_checksum(self.project_dir)

            def is_cache_valid() -> bool:
                if not (self.db_path / "db-python").exists():
                    return False
                if not checksum_file.exists():
                    return False
                return checksum_file.read_text().strip() == current_checksum

            if self.rebuild_analysis or not is_cache_valid():
                logger.info("Creating new CodeQL database...")

                cmd = [
                    str(self.codeql_bin),
                    "database",
                    "create",
                    str(self.db_path),
                    f"--source-root={self.project_dir}",
                    "--language=python",
                    "--overwrite",
                ]

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                )
                _, err = proc.communicate()

                if proc.returncode != 0:
                    raise CodeQLExceptions.CodeQLDatabaseBuildException(
                        f"Error building CodeQL database:\n{err.decode()}"
                    )

                checksum_file.write_text(current_checksum)

            else:
                logger.info(f"Reusing cached CodeQL DB at {self.db_path}")

        return self

    def __exit__(self, *args, **kwargs) -> None:
        if self.clear_cache and self.cache_dir.exists():
            logger.info(f"Clearing cache directory: {self.cache_dir}")
            shutil.rmtree(self.cache_dir)

    def analyze(self) -> PyApplication:
        """Analyze the project and return a PyApplication with symbol table.
        
        Uses caching to avoid re-analyzing unchanged files.
        """
        cache_file = self.cache_dir / "analysis_cache.json"
        
        # Try to load existing cached analysis 
        cached_pyapplication = None
        if not self.rebuild_analysis and cache_file.exists():
            try:
                cached_pyapplication = self._load_pyapplication_from_cache(cache_file)
                logger.info("Loaded cached analysis")
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}. Rebuilding analysis.")
                cached_pyapplication = None

        # Build symbol table from cached application if available (if no available, the build a new one)
        symbol_table = self._build_symbol_table(cached_pyapplication.symbol_table if cached_pyapplication else {})

        # Build the call graph in four steps:
        #   1. Run CodeQL (when enabled). Produces resolved edges with
        #      ``provenance=["codeql"]`` and augments ``PyCallsite``s
        #      in-place — filling ``callee_signature`` for sites Jedi
        #      couldn't resolve.
        #   2. Heuristic fallback for constructor calls neither Jedi nor
        #      CodeQL could resolve (commonly classes nested inside
        #      functions). Walks the symbol table by class short-name +
        #      scope and writes ``<class>.__init__`` into the site.
        #   3. Derive Jedi edges from the now-fully-augmented symbol
        #      table — these reflect every resolution the symbol table
        #      contains, regardless of which pass put it there.
        #   4. Merge with CodeQL edges; provenance unions for edges both
        #      backends saw.
        codeql_edges = self._get_call_graph(symbol_table, augment_sites=True)
        resolve_unresolved_constructors(symbol_table)
        jedi_edges = jedi_call_graph_edges(symbol_table)
        call_graph = merge_edges(jedi_edges, codeql_edges)

        # Recreate pyapplication
        app = PyApplication.builder().symbol_table(symbol_table).call_graph(call_graph).build()

        # Cache the BASE application (symbol table + Jedi/CodeQL call
        # graph) before running analysis passes. Pass output —
        # entrypoints and synthetic dispatch edges — is deliberately
        # never cached so it cannot go stale when an out-of-tree
        # extension is added, changed, or removed; the pipeline re-runs
        # on every analyze().
        self._save_analysis_cache(app, cache_file)

        # Enrich with the pluggable analysis-pass pipeline: in-tree
        # entrypoint finders plus out-of-tree passes registered via the
        # ``codeanalyzer.analysis_passes`` entry-point group (e.g. the
        # Odoo ORM-dispatch edge synthesizer).
        app = run_pipeline(app)

        return app

    def _load_pyapplication_from_cache(self, cache_file: Path) -> PyApplication:
        """Load cached analysis from file.
        
        Args:
            cache_file: Path to the cache file
            
        Returns:
            PyApplication: The cached application data
        """
        with cache_file.open('r') as f:
            data = f.read()
        return model_validate_json(PyApplication, data)
    
    def _save_analysis_cache(self, app: PyApplication, cache_file: Path) -> None:
        """Save analysis to cache file.
        
        Args:
            app: The PyApplication to cache
            cache_file: Path to save the cache file
        """
        # Ensure cache directory exists
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        
        with cache_file.open('w') as f:
            f.write(model_dump_json(app, indent=2))

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
        
        # Handle single file analysis
        if self.file_name is not None:
            single_file = self.project_dir / self.file_name
            logger.info(f"Analyzing single file: {single_file}")
            
            # Check if file is in cache and unchanged
            file_key = str(single_file)
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
                file_key = str(py_file)
                if file_key in cached_symbol_table and not self.rebuild_analysis:
                    if self._file_unchanged(py_file, cached_symbol_table[file_key]):
                        # Use cached version
                        symbol_table[file_key] = cached_symbol_table[file_key]
                        continue
                files_to_process.append(py_file)
            
            # Process only new/changed files with Ray
            if files_to_process:
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
                    file_key = str(py_file)
                    
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

        logger.info("✅ Symbol table generation complete.")
        return symbol_table

    def _ensure_codeql_packs(self, codeql_bin: Path) -> Path:
        """Materialize a qlpack that depends on ``codeql/python-all``.

        The CodeQL CLI install ships only the language extractors — query
        library packs (and their transitive dependencies like
        ``codeql/concepts``) must be resolved separately. The canonical
        way is to declare the dependency in a ``qlpack.yml`` and run
        ``codeql pack install`` in that directory; CodeQL writes a
        ``codeql-pack.lock.yml`` and downloads everything needed.

        We do this once per project under ``<cache_dir>/codeql/qlpack/``
        and return that directory. The query runner then writes its
        temporary ``.ql`` file inside this pack — colocation makes
        ``import python`` resolve without any ``--additional-packs`` or
        ``--search-path`` gymnastics.
        """
        pack_dir = self.cache_dir / "codeql" / "qlpack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        qlpack_yml = pack_dir / "qlpack.yml"
        lock_file = pack_dir / "codeql-pack.lock.yml"

        if not qlpack_yml.exists():
            qlpack_yml.write_text(
                "name: codeanalyzer-deps\n"
                "version: 1.0.0\n"
                "dependencies:\n"
                '  codeql/python-all: "*"\n'
            )

        if lock_file.exists():
            logger.debug(f"CodeQL pack dependencies already installed in {pack_dir}")
            return pack_dir

        logger.info(f"Installing CodeQL pack dependencies in {pack_dir}.")
        proc = subprocess.Popen(
            [str(codeql_bin), "pack", "install", str(pack_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, err = proc.communicate()
        if proc.returncode != 0:
            raise CodeQLExceptions.CodeQLDatabaseBuildException(
                f"Failed to install CodeQL pack dependencies:\n"
                f"{(err or b'').decode(errors='replace')}"
            )
        return pack_dir

    def _ensure_codeql_bin(self) -> Path:
        """Locate (or download) the CodeQL CLI binary into the project cache.

        Resolution order:
          1. An existing binary inside ``<cache_dir>/codeql/bin/`` —
             reused across runs on the same project.
          2. ``codeql`` already on the user's PATH — picked up verbatim.
          3. Otherwise, download into ``<cache_dir>/codeql/bin/``.

        The project-local cache is preferred over PATH so the version we
        installed earlier wins over whatever the OS ships — keeps behavior
        deterministic when the user has both.
        """
        bin_root = self.cache_dir / "codeql" / "bin"
        bin_root.mkdir(parents=True, exist_ok=True)

        existing = next(
            (p for p in bin_root.rglob("codeql") if p.is_file()),
            None,
        )
        if existing and os.access(existing, os.X_OK):
            logger.debug(f"Reusing cached CodeQL CLI at {existing}")
            return existing.resolve()

        on_path = shutil.which("codeql")
        if on_path:
            logger.debug(f"Using CodeQL CLI from PATH at {on_path}")
            return Path(on_path)

        logger.info(f"CodeQL CLI not found; downloading into {bin_root}.")
        downloaded = CodeQLLoader.download_and_extract_codeql(bin_root)
        if not downloaded.exists() or not os.access(downloaded, os.X_OK):
            raise FileNotFoundError(
                f"CodeQL binary not executable after download: {downloaded}"
            )
        return downloaded

    def _get_call_graph(
        self,
        symbol_table: Dict[str, PyModule],
        augment_sites: bool = False,
    ) -> List[PyCallEdge]:
        """Build CodeQL-resolved call edges and optionally augment sites.

        Returns an empty list when CodeQL isn't enabled or the database
        isn't available. Edges carry ``provenance=["codeql"]`` — merge
        with Jedi-derived edges via ``call_graph.merge_edges``.

        When ``augment_sites`` is True, also mutates
        ``PyCallable.call_sites`` in the symbol table to backfill
        ``callee_signature`` for sites Jedi couldn't resolve. The single
        CodeQL query is shared (cached on the ``CodeQL`` instance) so
        this costs no extra DB work.
        """
        if not self.using_codeql or self.db_path is None:
            return []
        try:
            cq = CodeQL(
                self.project_dir,
                self.db_path,
                codeql_bin=self.codeql_bin,
                codeql_packs_dir=self.codeql_packs_dir,
            )
            edges = cq.build_call_graph_edges(symbol_table)
            if augment_sites:
                cq.augment_call_sites(symbol_table)
            return edges
        except Exception as exc:
            logger.warning(f"CodeQL call-graph extraction failed: {exc}")
            return []