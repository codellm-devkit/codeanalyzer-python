![logo](./docs/assets/logo.png)

Python Static Analysis Backend for CLDK

A comprehensive static analysis tool for Python source code that provides symbol table generation, call graph analysis, and semantic analysis using Jedi, CodeQL, and Tree-sitter.

## Installation

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

### Prerequisites

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed

### Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd codeanalyzer-python
   ```

2. Install dependencies using uv:
   ```bash
   uv sync --all-groups
   ```
   
   This will install all dependencies including development and test dependencies.

3. Install the package in development mode:
   ```bash
   uv pip install -e .
   ```

## Usage

The codeanalyzer provides a command-line interface for performing static analysis on Python projects.

### Basic Usage

```bash
codeanalyzer --input /path/to/python/project
```

### Command Line Options

- `-i, --input PATH`: **Required.** Path to the project root directory to analyze.
- `-o, --output PATH`: Output directory for analysis artifacts. If specified, results will be saved to `analysis.json` in this directory.
- `-a, --analysis-level INTEGER`: Analysis depth level (default: 1)
  - `1`: Symbol table generation
  - `2`: Call graph analysis
- `--codeql/--no-codeql`: Enable or disable CodeQL-based analysis (default: disabled)
- `--eager/--lazy`: Analysis mode (default: lazy)
  - `--eager`: Rebuild analysis cache at every run
  - `--lazy`: Use existing cache if available
- `-c, --cache-dir PATH`: Directory to store analysis cache. Defaults to `.cache/codeanalyzer` in current working directory.
- `--clear-cache/--keep-cache`: Clear cache after analysis (default: clear)
- `-v/-q, --verbose/--quiet`: Enable or disable verbose output (default: verbose)

### Examples

1. **Basic analysis with symbol table:**
   ```bash
   codeanalyzer --input ./my-python-project
   ```

   This will print the symbol table to stdout in JSON format to the standard output. If you want to save the output, you can use the `--output` option.

   ```bash
   codeanalyzer --input ./my-python-project --output /path/to/analysis-results
   ```

   Now, you can find the analysis results in `analysis.json` in the specified directory.

2. **Toggle analysis levels with `--analysis-level`:**
   ```bash
   codeanalyzer --input ./my-python-project --analysis-level 1 # Symbol table only
   ```
   Call graph analysis can be enabled by setting the level to `2`:
   ```bash
   codeanalyzer --input ./my-python-project --analysis-level 2 # Symbol table + Call graph
   ```
   ***Note: The `--analysis-level=2` is not yet implemented in this version.***

3. **Analysis with CodeQL enabled:**
   ```bash
   codeanalyzer --input ./my-python-project --codeql
   ```
    This will perform CodeQL-based analysis in addition to the standard symbol table generation. 
    
    ***Note: Not yet fully implemented. Please refrain from using this option until further notice.***

4. **Eager analysis with custom cache directory:**
   ```bash
   codeanalyzer --input ./my-python-project --eager --cache-dir /path/to/custom-cache
   ```
    This will rebuild the analysis cache at every run and store it in `/path/to/custom-cache/.codeanalyzer`. The cache will be cleared by default after analysis unless you specify `--keep-cache`.

    If you provide --cache-dir, the cache will be stored in that directory. If not specified, it defaults to `.codeanalyzer` in the current working directory (`$PWD`).

5. **Quiet mode (minimal output):**
   ```bash
   codeanalyzer --input /path/to/my-python-project --quiet
   ```

### Output

By default, analysis results are printed to stdout in JSON format. When using the `--output` option, results are saved to `analysis.json` in the specified directory.

## Development

### Running Tests

```bash
uv run pytest --pspec -s 
```

### Development Dependencies

The project includes additional dependency groups for development:

- **test**: pytest and related testing tools
- **dev**: development tools like ipdb

Install all groups with:
```bash
uv sync --all-groups
```
