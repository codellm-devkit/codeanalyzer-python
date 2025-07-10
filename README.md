![logo](./docs/assets/logo.png)

Python Static Analysis Backend for CLDK

A comprehensive static analysis tool for Python source code that provides symbol table generation, call graph analysis, and semantic analysis using Jedi, CodeQL, and Tree-sitter.

## Installation

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Python 3.12 or higher. You can use `uv` to install Python if it's not already installed:
  ```bash
  uv python install 3.12
  ```

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/codellm-devkit/codeanalyzer-python
   cd codeanalyzer-python
   ```

2. Install dependencies using uv:
   ```bash
   uv sync --all-groups
   ```
   This will install all dependencies including development and test dependencies.

## Usage

The codeanalyzer provides a command-line interface for performing static analysis on Python projects.

### Basic Usage

```bash
uv run codeanalyzer --input /path/to/python/project
```

### Command Line Options

To view the available options and commands, run `uv run codeanalyzer --help`. You should see output similar to the following:

```bash
❯ uv run codeanalyzer --help

 Usage: codeanalyzer [OPTIONS] COMMAND [ARGS]...

 Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.


╭─ Options ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ *  --input           -i                    PATH     Path to the project root directory. [default: None] [required]                                                                                                                                                                                           │
│    --output          -o                    PATH     Output directory for artifacts. [default: None]                                                                                                                                                                                                          │
│    --analysis-level  -a                    INTEGER  1: symbol table, 2: call graph. [default: 1]                                                                                                                                                                                                             │
│    --codeql              --no-codeql                Enable CodeQL-based analysis. [default: no-codeql]                                                                                                                                                                                                       │
│    --eager               --lazy                     Enable eager or lazy analysis. Eager will rebuild the analysis cache at every run and lazy will use the cache if available. Defaults to lazy. [default: lazy]                                                                                            │
│    --cache-dir       -c                    PATH     Directory to store analysis cache. If not specified, the cache will be stored in the current working directory under `.codeanalyzer`. Defaults to None. [default: None]                                                                                  │
│    --clear-cache         --keep-cache               Clear cache after analysis. [default: clear-cache]                                                                                                                                                                                                       │
│    --verbose         -v  --quiet       -q           Enable verbose output. [default: v]                                                                                                                                                                                                                      │
│    --help                                           Show this message and exit.                                                                                                                                                                                                                              │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### Examples

1. **Basic analysis with symbol table:**
   ```bash
   uv run codeanalyzer --input ./my-python-project
   ```

   This will print the symbol table to stdout in JSON format to the standard output. If you want to save the output, you can use the `--output` option.

   ```bash
   uv run codeanalyzer --input ./my-python-project --output /path/to/analysis-results
   ```

   Now, you can find the analysis results in `analysis.json` in the specified directory.

2. **Toggle analysis levels with `--analysis-level`:**
   ```bash
   uv run codeanalyzer --input ./my-python-project --analysis-level 1 # Symbol table only
   ```
   Call graph analysis can be enabled by setting the level to `2`:
   ```bash
   uv run codeanalyzer --input ./my-python-project --analysis-level 2 # Symbol table + Call graph
   ```
   ***Note: The `--analysis-level=2` is not yet implemented in this version.***

3. **Analysis with CodeQL enabled:**
   ```bash
   uv run codeanalyzer --input ./my-python-project --codeql
   ```
    This will perform CodeQL-based analysis in addition to the standard symbol table generation. 
    
    ***Note: Not yet fully implemented. Please refrain from using this option until further notice.***

4. **Eager analysis with custom cache directory:**
   ```bash
   uv run codeanalyzer --input ./my-python-project --eager --cache-dir /path/to/custom-cache
   ```
    This will rebuild the analysis cache at every run and store it in `/path/to/custom-cache/.codeanalyzer`. The cache will be cleared by default after analysis unless you specify `--keep-cache`.

    If you provide --cache-dir, the cache will be stored in that directory. If not specified, it defaults to `.codeanalyzer` in the current working directory (`$PWD`).

5. **Quiet mode (minimal output):**
   ```bash
   uv run codeanalyzer --input /path/to/my-python-project --quiet
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
