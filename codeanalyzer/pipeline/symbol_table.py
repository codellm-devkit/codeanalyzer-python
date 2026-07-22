import hashlib
import os
import time
from pathlib import Path
from typing import Dict, Optional, Union

import ray

from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import PyModule
from codeanalyzer.syntactic_analysis.exceptions import SymbolTableBuilderRayError
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder
from codeanalyzer.utils import ProgressBar, logger


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


def _file_unchanged(file_path: Path, cached_module: PyModule) -> bool:
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


def build_symbol_table(
    project_dir: Path,
    virtualenv: Optional[Path],
    options: AnalysisOptions,
    cached_symbol_table: Optional[Dict[str, PyModule]] = None,
) -> Dict[str, PyModule]:
    """Build the symbol table for the project (moved from Codeanalyzer).

    This scans the project directory, identifies Python files,
    and constructs a symbol table containing information about classes,
    functions, and variables defined in those files.

    Args:
        project_dir: Root directory of the project.
        virtualenv: Path to the virtual environment directory, if any.
        options: Analysis configuration options.
        cached_symbol_table: Previously cached ``Dict[str, PyModule]`` symbol
            table whose unchanged files are reused.

    Returns:
        Dict[str, PyModule]: A dictionary mapping file paths to PyModule objects.
    """
    if cached_symbol_table is None:
        cached_symbol_table = {}

    symbol_table: Dict[str, PyModule] = {}
    t0_st = time.perf_counter()

    # Handle single file analysis
    if options.file_name is not None:
        single_file = project_dir / options.file_name
        logger.info(f"Analyzing single file: {single_file}")

        # Check if file is in cache and unchanged
        file_key = str(single_file.relative_to(project_dir))
        if file_key in cached_symbol_table and not options.rebuild_analysis:
            # Compute file checksum to see if it changed
            if _file_unchanged(single_file, cached_symbol_table[file_key]):
                logger.info(f"Using cached analysis for {single_file}")
                symbol_table[file_key] = cached_symbol_table[file_key]
                return symbol_table

        # File is new or changed, analyze it
        try:
            symbol_table_builder = SymbolTableBuilder(project_dir, virtualenv)
            py_module = symbol_table_builder.build_pymodule_from_file(single_file)
            symbol_table[file_key] = py_module
            logger.info("✅ Single file analysis complete.")
            return symbol_table
        except Exception as e:
            logger.error(f"Failed to process {single_file}: {e}")
            return symbol_table

    # Get all Python files first to show accurate progress
    py_files = []
    for py_file in project_dir.rglob("*.py"):
        rel_path = py_file.relative_to(project_dir)
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
        if options.skip_tests and (
            "test" in path_parts
            or "tests" in path_parts
            or filename.startswith("test_")
            or filename.endswith("_test.py")
        ):
            continue

        py_files.append(py_file)

    if options.using_ray:
        logger.info("Using Ray for distributed symbol table generation.")
        # Separate files into cached and new/changed
        files_to_process = []
        for py_file in py_files:
            file_key = str(py_file.relative_to(project_dir))
            if file_key in cached_symbol_table and not options.rebuild_analysis:
                if _file_unchanged(py_file, cached_symbol_table[file_key]):
                    # Use cached version
                    symbol_table[file_key] = cached_symbol_table[file_key]
                    continue
            files_to_process.append(py_file)

        # Process only new/changed files with Ray
        if files_to_process:
            _ensure_ray()
            futures = [_process_file_with_ray.remote(py_file, project_dir, str(virtualenv) if virtualenv else None) for py_file in files_to_process]

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
        symbol_table_builder = SymbolTableBuilder(project_dir, virtualenv)
        files_processed = 0
        files_from_cache = 0

        with ProgressBar(len(py_files), "Building symbol table") as progress:
            for py_file in py_files:
                file_key = str(py_file.relative_to(project_dir))

                # Check if file is cached and unchanged
                if file_key in cached_symbol_table and not options.rebuild_analysis:
                    if _file_unchanged(py_file, cached_symbol_table[file_key]):
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

    if py_files and not symbol_table:
        logger.error(
            "Every one of the %d discovered Python files failed to process — "
            "the symbol table is empty. This usually means the analysis "
            "environment's interpreter is newer than the installed jedi/parso "
            "stack supports (#107); check the per-file errors above.",
            len(py_files),
        )

    logger.info(
        "✅ Symbol table: %d modules in %.1fs",
        len(symbol_table), time.perf_counter() - t0_st,
    )
    return symbol_table
