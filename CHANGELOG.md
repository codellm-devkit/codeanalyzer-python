# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
