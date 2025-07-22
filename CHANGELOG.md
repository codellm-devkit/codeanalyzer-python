# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.13] - 2025-07-22

### Improved
- **CLI Help Documentation**: Comprehensive help text added for all command-line options
  - Added descriptive help messages for all CLI parameters including `--output`, `--format`, `--analysis-level`, etc.
  - Enhanced user experience with clear option descriptions in `--help` output
  - Improved CLI parameter organization using `Annotated` type hints for better maintainability
  - Added case-insensitive support for `--format` option
  - Updated verbosity option help to clearly indicate multiple usage (`-v`, `-vv`, `-vvv`)

### Technical Details
- Refactored CLI function signature to use consistent `Annotated` type hint pattern
- Added comprehensive help text for all 12 command-line options
- Improved code organization and type safety in CLI parameter definitions

## [0.1.12] - 2025-07-21

### Changed
- **BREAKING CHANGE**: Refactored `Codeanalyzer` constructor to use `AnalysisOptions` dataclass [in response to #12](https://github.com/codellm-devkit/codeanalyzer-python/issues/12)
  - Replaced multiple individual parameters with single `AnalysisOptions` object for cleaner API
  - Improved type safety and configuration management through centralized options structure
  - Enhanced maintainability and extensibility for future configuration additions
  - Updated CLI integration to create and pass `AnalysisOptions` instance
  - Maintained backward compatibility in terms of functionality while improving code architecture

### Added
- New `AnalysisOptions` dataclass in `codeanalyzer.options` module [in response to #12](https://github.com/codellm-devkit/codeanalyzer-python/issues/12)
  - Centralized configuration structure with all analysis parameters
  - Type-safe configuration with proper defaults and validation
  - Support for `OutputFormat` enum integration
  - Clean separation between CLI and library configuration handling

### Technical Details
- Added new `codeanalyzer.options` package with `AnalysisOptions` dataclass
- Updated `Codeanalyzer.__init__()` to accept single `options` parameter instead of 9 individual parameters
- Modified CLI handler in `__main__.py` to create `AnalysisOptions` instance from command line arguments
- Improved code organization and maintainability for configuration management
- Enhanced API design following best practices for parameter object patterns

## [0.1.11] - 2025-07-21

### Fixed
- **CRITICAL**: Fixed NumPy build failure on Python 3.12+ (addresses [#19](https://github.com/codellm-devkit/codeanalyzer-python/issues/19))
  - Updated NumPy dependency constraints to handle Python 3.12+ compatibility
  - Split NumPy version constraints into three tiers:
    - `numpy>=1.21.0,<1.24.0` for Python < 3.11
    - `numpy>=1.24.0,<2.0.0` for Python 3.11.x
    - `numpy>=1.26.0,<2.0.0` for Python 3.12+ (requires NumPy 1.26+ which supports Python 3.12)
  - Resolves `ModuleNotFoundError: No module named 'distutils'` errors on Python 3.12+
  - Ensures compatibility with Python 3.12 which removed `distutils` from the standard library
- Fixed Pydantic v1/v2 compatibility issues in JSON serialization throughout codebase
  - Added comprehensive Pydantic version detection and compatibility layer
  - Introduced `model_dump_json()` and `model_validate_json()` helper functions for cross-version compatibility
  - Fixed `PyApplication.parse_raw()` deprecated method usage (replaced with `model_validate_json()`)
  - Updated CLI output methods to use compatible serialization functions
  - Resolved forward reference updates only for Pydantic v1 (v2 handles these automatically)

### Changed
- Enhanced Pydantic compatibility infrastructure in schema module
  - Added runtime Pydantic version detection using `importlib.metadata`
  - Created compatibility abstraction layer for JSON serialization/deserialization
  - Improved forward reference resolution logic to work with both Pydantic v1 and v2
  - Updated all JSON serialization calls to use new compatibility functions
  - Better error handling for missing Pydantic dependency

### Technical Details
- Added `packaging` dependency for robust version comparison
- Enhanced schema module with runtime version detection and compatibility helpers
- Updated core analysis caching system to use compatible Pydantic JSON methods
- Improved CLI output formatting with cross-version Pydantic support

## [0.1.10] - 2025-07-20

### Added
- Ray distributed processing support for parallel symbol table generation (addresses [#16](https://github.com/codellm-devkit/codeanalyzer-python/issues/16))
- `--ray/--no-ray` CLI flag to enable/disable Ray-based distributed analysis
- `--skip-tests/--include-tests` CLI flag to control whether test files are analyzed (improves analysis performance)
- `--file-name` CLI flag for single file analysis (addresses part of [#16](https://github.com/codellm-devkit/codeanalyzer-python/issues/16))
- Incremental caching system with SHA256-based file change detection
  - Automatic caching of analysis results to `analysis_cache.json`
  - File-level caching with content hash validation to avoid re-analyzing unchanged files
  - Significant performance improvements for subsequent analysis runs
  - Cache reuse statistics logging
- Custom exception classes for better error handling in symbol table building:
  - `SymbolTableBuilderException` (base exception)
  - `SymbolTableBuilderFileNotFoundError` (file not found errors)
  - `SymbolTableBuilderParsingError` (parsing errors)
  - `SymbolTableBuilderRayError` (Ray processing errors)
- Enhanced PyModule schema with metadata fields for caching:
  - `last_modified` timestamp tracking
  - `content_hash` for precise change detection
- Progress bar support for both serial and parallel processing modes
- Enhanced test fixtures including xarray project for comprehensive testing
- Comprehensive `__init__.py` exports for syntactic analysis module
- Smart dependency installation with conditional logic:
  - Only installs requirements files when they exist (requirements.txt, requirements-dev.txt, dev-requirements.txt, test-requirements.txt)
  - Only performs editable installation when package definition files are present (pyproject.toml, setup.py, setup.cfg)
  - Improved virtual environment setup with better dependency detection and installation logic

### Changed
- **BREAKING CHANGE**: Updated Python version requirement from `>=3.10` to `>=3.9` for broader compatibility (closes [#17](https://github.com/codellm-devkit/codeanalyzer-python/issues/17))
- **BREAKING CHANGE**: Updated dependency versions with more conservative constraints for better stability:
  - `pydantic` downgraded from `>=2.11.7` to `>=1.8.0,<2.0.0` for stability
  - `pandas` constrained to `>=1.3.0,<2.0.0`
  - `numpy` constrained to `>=1.21.0,<1.24.0`
  - `rich` constrained to `>=12.6.0,<14.0.0`
  - `typer` constrained to `>=0.9.0,<1.0.0`
  - Other dependencies updated with conservative version ranges for better compatibility
- Major Architecture Enhancement: Complete rewrite of analysis caching system
  - `analyze()` method now implements intelligent caching with PyApplication serialization
  - Symbol table building redesigned to support incremental updates and cache reuse
  - File change detection using SHA256 content hashing for maximum accuracy
- Enhanced `Codeanalyzer` constructor signature to accept `file_name` parameter for single file analysis
- Refactored symbol table building from monolithic `build()` method to cache-aware file-level processing
- Enhanced `Codeanalyzer` constructor signature to accept `skip_tests` and `using_ray` parameters
- Improved error handling with proper context managers in core analyzer
- Updated CLI to use Pydantic v1 compatible JSON serialization methods
- Reorganized syntactic analysis module structure with proper exception handling and exports
- Enhanced virtual environment detection with better fallback mechanisms
- Symbol table builder now sets metadata fields (`last_modified`, `content_hash`) for all PyModule objects

### Fixed
- Fixed critical symbol table bug for nested functions (closes [#15](https://github.com/codellm-devkit/codeanalyzer-python/issues/15))
  - Corrected `_callables()` method recursion logic to properly capture both outer and inner functions
  - Previously, only inner/nested functions were being captured in the symbol table
  - Now correctly processes module-level functions, class methods, and all nested function definitions
- Fixed nested method/function signature generation in symbol table builder
  - Corrected `_callables()` method to properly build fully qualified signatures for nested structures
  - Fixed issue where nested functions and methods were getting incorrect signatures (e.g., `main.__init__` instead of `main.outer_function.NestedClass.__init__`)
  - Added `prefix` parameter to `_callables()` and `_add_class()` methods to maintain proper nesting context
  - Signatures now correctly reflect the full nested hierarchy (e.g., `main.outer_function.NestedClass.nested_class_method.method_nested_function`)
  - Updated class method processing to pass class signature as prefix to nested callable processing
  - Improved path relativization to project directory for cleaner signature generation
- Fixed Pydantic v2 compatibility issues by reverting to v1 API (`json()` instead of `model_dump_json()`)
- Fixed missing import statements and type annotations throughout the codebase
- Fixed symbol table builder to support individual file processing for distributed execution
- Improved error handling in virtual environment detection and Python interpreter resolution
- Fixed schema type annotations to use proper string keys for better serialization
- Enhanced import ordering and removed unnecessary blank lines in CLI module
- Improved virtual environment setup reliability:
  - Fixed unnecessary pip installs by adding conditional logic to only install when dependencies are available
  - Only attempts to install requirements files if they actually exist in the project
  - Only performs editable installation when package definition files are present
  - Prevents errors and warnings from attempting to install non-existent dependencies

### Technical Details
- Added Ray as a core dependency for distributed computing capabilities (addresses [#16](https://github.com/codellm-devkit/codeanalyzer-python/issues/16))
- Implemented `@ray.remote` decorator for parallel file processing
- Comprehensive caching system implementation:
  - `_load_pyapplication_from_cache()` and `_save_analysis_cache()` methods for PyApplication serialization
  - `_file_unchanged()` method with SHA256 content hash validation
  - Cache-aware symbol table building with selective file processing
  - Automatic cache statistics and performance reporting
- Enhanced progress tracking for both serial and parallel execution modes with Rich progress bars
- Updated schema to use `Dict[str, PyModule]` instead of `dict[Path, PyModule]` for better serialization
- Extended PyModule schema with optional `last_modified` and `content_hash` fields for caching metadata
- Added comprehensive exception hierarchy for better error classification and handling
- Refactored symbol table building into modular, file-level processing suitable for distribution
- Enhanced Python interpreter detection with support for multiple version managers (pyenv, conda, asdf)
- Added `hashlib` integration for file content hashing throughout the codebase
- Enhanced virtual environment setup logic:
  - Modified `_add_class()` method to accept `prefix` parameter and pass class signature to method processing
  - Updated `_callables()` method signature to include `prefix` parameter for nested context tracking  
  - Enhanced signature building logic to use prefix when available, falling back to Jedi resolution for top-level definitions
  - Fixed recursive calls to pass current signature as prefix for proper nesting hierarchy
  - Implemented conditional dependency installation with existence checks for requirements files and package definition files

### Notes
- This release significantly addresses the performance improvements requested in [#16](https://github.com/codellm-devkit/codeanalyzer-python/issues/16):
  - ✅ Ray parallelization implemented
  - ✅ Incremental caching with SHA256-based change detection implemented  
  - ✅ `--file-name` option for single-file analysis implemented
  - ❌ `--nproc` options not yet included (still uses all available cores with Ray)
- ✅ Critical bug fix for nested function detection ([#15](https://github.com/codellm-devkit/codeanalyzer-python/issues/15)) is now included in this version
- Expected performance improvements: 2-10x faster on subsequent runs depending on code change frequency
- Enhanced symbol table accuracy ensures all function definitions are properly captured
- Virtual environment setup is now more robust and only installs dependencies when they are actually available

## [0.1.9] - 2025-07-14

### Fixed
- Fixed `AttributeError: 'OutputFormat' object has no attribute 'casefold'` when using `--format` flag with case-insensitive options
- Changed `OutputFormat` enum to inherit from `str` to support typer's case-insensitive string processing

## [0.1.8] - 2025-07-14

### Added
- Added missing config module with OutputFormat enum for better code organization
- Added proper `__init__.py` and `config.py` files to the config directory

### Fixed
- Fixed missing config directory files that were not previously tracked in git

## [0.1.7] - 2025-07-14

### Changed
- Relaxed Python version requirement from `==3.10.*` to `>=3.10` for improved flexibility
- Enhanced compatibility to support Python 3.10+ versions while maintaining backward compatibility

## [0.1.6] - 2025-07-14

### Changed
- **BREAKING CHANGE**: Updated Python version requirement from `>=3.12` to `==3.10` for improved backwards compatibility
- Enhanced backwards compatibility by supporting Python 3.10 environments commonly used in enterprise and CI/CD systems

### Fixed
- Fixed Python version compatibility issue that was unnecessarily blocking installation on Python 3.10 and 3.11 systems
- Resolved adoption barriers for users on older but still supported Python versions

### Technical Notes
- All codebase features are fully compatible with Python 3.10 (ast.unparse, built-in generics, type hints)
- No Python 3.11+ or 3.12+ specific features are used in the implementation
- All dependencies support Python 3.10+

## [0.2.0] - 2025-07-11

### Changed
- **BREAKING CHANGE**: Renamed `AnalyzerCore` class to `Codeanalyzer` for better library naming consistency
- Refactored core class to support direct library import: `from codeanalyzer import Codeanalyzer`
- Updated all internal references and documentation to use the new class name
- Enhanced library interface for programmatic usage while maintaining CLI compatibility

### Added
- Direct library import support allowing users to import and use `Codeanalyzer` as a library
- Proper `__all__` export in `__init__.py` for clean package interface

## [0.1.5] - 2025-07-11

### Fixed
- Fixed `TypeError` when calling `BaseModel.model_dump_json()` with unsupported `separators` argument (Issue #8)
- Fixed ANSI color codes appearing in CLI test output despite `color=False` setting in CI/CD environments (Issue #9)
- Replaced deprecated `astor.to_source()` with built-in `ast.unparse()` for better Python 3.12+ compatibility

### Changed
- Updated JSON output formatting to use `indent=None` for compact output instead of unsupported `separators` parameter
- Enhanced CLI test configuration to explicitly disable colors using environment variables (`NO_COLOR=1`, `TERM=dumb`)
- Improved test robustness for CI/CD environments, particularly GitHub Actions
- Updated test assertions to properly validate JSON output structure and content

### Removed
- Removed `astor` dependency in favor of Python's built-in `ast.unparse()` functionality

### Added
- Comprehensive coverage configuration in `pyproject.toml` with proper source mapping and exclusions
- Enhanced test fixtures with improved project root path calculation
- Proper logging configuration for test environments
- Coverage reporting with HTML output and configurable thresholds

## [0.1.4] - 2025-07-11

### Added
- MessagePack output format support for ultra-compressed analysis results (achieving 80-90% size reduction)
- `--format` flag allowing users to choose between JSON and MessagePack output formats
- Built-in decompression logic for MessagePack files
- Enhanced output file handling with format-specific extensions

### Changed
- Improved output file compression and serialization performance
- Enhanced schema handling for better serialization efficiency
- Updated CLI to support multiple output formats

### Fixed
- Performance issues with large analysis result files through compression

## [0.1.3] - 2025-07-10

### Fixed
- Fixed broken logo display on PyPI package page (Issue #4)
- Updated README.md to use absolute URLs instead of relative paths for images

### Changed
- Updated package metadata for better PyPI presentation

## [0.1.2] - 2025-07-10

### Fixed
- Fixed CLI installation and execution issues (Issue #2)
- Resolved package import problems in installed package

### Changed
- Reorganized project structure from `src/codeanalyzer` to `codeanalyzer` for better packaging
- Updated build configuration in `pyproject.toml` to properly include all package files
- Moved test directory from `src/test` to `test` for cleaner project structure

## [0.1.1] - 2025-07-10

### Added
- Input path validation in CLI main function to check if the provided input path exists
- Logger import for better error handling and logging capabilities
- Comprehensive system package requirements documentation in README.md
- Platform-specific installation instructions for Ubuntu/Debian, Fedora/RHEL/CentOS, and macOS
- Enhanced error reporting in command execution helper with detailed error output logging
- Support for multiple Python version managers (pyenv, conda, asdf) in base interpreter detection

### Changed
- Improved CLI error handling with proper logging when input path does not exist
- Test configuration updates:
  - Removed unused app import from conftest.py
  - Updated test_cli_call_symbol_table to use verbose flag (-v) instead of --quiet flag
- Improved virtual environment creation error handling with informative error messages
- Enhanced `_get_base_interpreter()` method with robust Python interpreter detection across different environments
- Updated README.md with detailed prerequisite packages including development tools and Python headers

### Fixed
- CLI now properly exits with error code 1 when input path doesn't exist instead of proceeding with invalid path
- Virtual environment creation issues on systems missing python3-venv package
- Type safety issues in subprocess command execution by ensuring all arguments are strings
- Cross-platform compatibility for Python interpreter detection

## [0.1.0] - Initial Release

### Added
- Initial release of codeanalyzer
- Static analysis capabilities for Python source code using Jedi, CodeQL, and Tree-sitter
- Command-line interface with typer
- Support for symbol table analysis and call graph generation
- Configurable analysis levels (1: symbol table, 2: call graph)
- CodeQL integration with optional enable/disable
- Caching system with configurable cache directory
- Output artifacts in JSON format
- Verbosity controls for logging
- Eager/lazy analysis modes
