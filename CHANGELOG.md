# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
