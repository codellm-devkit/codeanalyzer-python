# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2025-07-10

### Added
- Input path validation in CLI main function to check if the provided input path exists
- Logger import for better error handling and logging capabilities

### Changed
- Improved CLI error handling with proper logging when input path does not exist
- Test configuration updates:
  - Removed unused app import from conftest.py 
  - Updated test_cli_call_symbol_table to use verbose flag (-v) instead of --quiet flag

### Fixed
- CLI now properly exits with error code 1 when input path doesn't exist instead of proceeding with invalid path

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