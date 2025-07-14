![logo](https://github.com/codellm-devkit/codeanalyzer-python/blob/main/docs/assets/logo.png?raw=true)

# A Python Static Analysis Toolkit (and Library)

A comprehensive static analysis tool for Python source code that provides symbol table generation, call graph analysis, and semantic analysis using Jedi, CodeQL, and Tree-sitter.

## Installation

```bash
pip install codeanalyzer-python
```

### Prerequisites

- Python 3.12 or higher

#### System Package Requirements

The tool creates virtual environments internally using Python's built-in `venv` module.

**Ubuntu/Debian systems:**
```bash
sudo apt update
sudo apt install python3.12-venv python3-dev build-essential
```

**Fedora/RHEL/CentOS systems:**
```bash
sudo dnf group install "Development Tools"
sudo dnf install python3-pip python3-venv python3-devel
```
or on older versions:
```bash
sudo yum groupinstall "Development Tools"
sudo yum install python3-pip python3-venv python3-devel
```

**macOS systems:**
```bash
# Install Xcode Command Line Tools (for compilation)
xcode-select --install

# If using Homebrew Python (recommended)
brew install python@3.12

# If using pyenv (popular Python version manager)
# First ensure pyenv is properly installed and configured
pyenv install 3.12.0  # or latest 3.12.x version
pyenv global 3.12.0   # or pyenv local 3.12.0 for project-specific

# If using system Python, you may need to install certificates
/Applications/Python\ 3.12/Install\ Certificates.command
```

> **Note:** These packages are required as the tool uses Python's built-in `venv` module to create isolated environments for analysis.

## Usage

The codeanalyzer provides a command-line interface for performing static analysis on Python projects.

### Basic Usage

```bash
codeanalyzer --input /path/to/python/project
```

### Command Line Options

To view the available options and commands, run `codeanalyzer --help`. You should see output similar to the following:

```bash
❯ codeanalyzer --help

 Usage: codeanalyzer [OPTIONS] COMMAND [ARGS]...

 Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.


╭─ Options ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ *  --input           -i                  PATH            Path to the project root directory. [default: None] [required]   │
│    --output          -o                  PATH            Output directory for artifacts. [default: None]                  │
│    --format          -f                  [json|msgpack]  Output format: json or msgpack. [default: json]                  │
│    --analysis-level  -a                  INTEGER         1: symbol table, 2: call graph. [default: 1]                     │
│    --codeql              --no-codeql                     Enable CodeQL-based analysis. [default: no-codeql]               │
│    --eager               --lazy                          Enable eager or lazy analysis. Defaults to lazy. [default: lazy] │
│    --cache-dir       -c                  PATH            Directory to store analysis cache. [default: None]               │
│    --clear-cache         --keep-cache                    Clear cache after analysis. [default: clear-cache]               │
│                      -v                  INTEGER         Increase verbosity: -v, -vv, -vvv [default: 0]                   │
│    --help                                                Show this message and exit.                                      │
╰─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

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

2. **Change output format to msgpack:**
   ```bash
   codeanalyzer --input ./my-python-project --output /path/to/analysis-results --format msgpack
   ```

   This will save the analysis results in `analysis.msgpack` in the specified directory.

3. **Toggle analysis levels with `--analysis-level`:**
   ```bash
   codeanalyzer --input ./my-python-project --analysis-level 1 # Symbol table only
   ```
   Call graph analysis can be enabled by setting the level to `2`:
   ```bash
   codeanalyzer --input ./my-python-project --analysis-level 2 # Symbol table + Call graph
   ```
   ***Note: The `--analysis-level=2` is not yet implemented in this version.***

4. **Analysis with CodeQL enabled:**
   ```bash
   codeanalyzer --input ./my-python-project --codeql
   ```
    This will perform CodeQL-based analysis in addition to the standard symbol table generation.

    ***Note: Not yet fully implemented. Please refrain from using this option until further notice.***

5. **Eager analysis with custom cache directory:**
   ```bash
   codeanalyzer --input ./my-python-project --eager --cache-dir /path/to/custom-cache
   ```
    This will rebuild the analysis cache at every run and store it in `/path/to/custom-cache/.codeanalyzer`. The cache will be cleared by default after analysis unless you specify `--keep-cache`.

    If you provide --cache-dir, the cache will be stored in that directory. If not specified, it defaults to `.codeanalyzer` in the current working directory (`$PWD`).

6. **Quiet mode (minimal output):**
   ```bash
   codeanalyzer --input /path/to/my-python-project --quiet
   ```

## Output

By default, analysis results are printed to stdout in JSON format. When using the `--output` option, results are saved to `analysis.json` in the specified directory. If you use the `--format=msgpack` option, the results will be saved in `analysis.msgpack`, which is a binary format that can be more efficient for storage and transmission.

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management during development.

### Development Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
![logo](https://github.com/codellm-devkit/codeanalyzer-python/blob/main/docs/assets/logo.png?raw=true)

# A Python Static Analysis Toolkit (and Library)

A comprehensive static analysis tool for Python source code that provides symbol table generation, call graph analysis, and semantic analysis using Jedi, CodeQL, and Tree-sitter.

## Installation

```bash
pip install codeanalyzer-python
```

### Prerequisites

- Python 3.12 or higher

#### System Package Requirements

The tool creates virtual environments internally using Python's built-in `venv` module.

**Ubuntu/Debian systems:**
```bash
sudo apt update
sudo apt install python3.12-venv python3-dev build-essential
```

**Fedora/RHEL/CentOS systems:**
```bash
sudo dnf group install "Development Tools"
sudo dnf install python3-pip python3-venv python3-devel
```
or on older versions:
```bash
sudo yum groupinstall "Development Tools"
sudo yum install python3-pip python3-venv python3-devel
```

**macOS systems:**
```bash
# Install Xcode Command Line Tools (for compilation)
xcode-select --install

# If using Homebrew Python (recommended)
brew install python@3.12

# If using pyenv (popular Python version manager)
# First ensure pyenv is properly installed and configured
pyenv install 3.12.0  # or latest 3.12.x version
pyenv global 3.12.0   # or pyenv local 3.12.0 for project-specific

# If using system Python, you may need to install certificates
/Applications/Python\ 3.12/Install\ Certificates.command
```

> **Note:** These packages are required as the tool uses Python's built-in `venv` module to create isolated environments for analysis.

## Usage

The codeanalyzer provides a command-line interface for performing static analysis on Python projects.

### Basic Usage

```bash
codeanalyzer --input /path/to/python/project
```

### Command Line Options

To view the available options and commands, run `codeanalyzer --help`. You should see output similar to the following:

```bash
❯ codeanalyzer --help

 Usage: codeanalyzer [OPTIONS] COMMAND [ARGS]...

 Static Analysis on Python source code using Jedi, CodeQL and Tree sitter.


╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ *  --input           -i                  PATH     Path to the project root directory. [default: None] [required]   │
│    --output          -o                  PATH     Output directory for artifacts. [default: None]                  │
│    --format          -f           [json|msgpack]  Output format: json or msgpack. [default: json].                 │
│    --analysis-level  -a                  INTEGER  1: symbol table, 2: call graph. [default: 1]                     │
│    --codeql              --no-codeql              Enable CodeQL-based analysis. [default: no-codeql]               │
│    --eager               --lazy                   Enable eager or lazy analysis. Defaults to lazy. [default: lazy] │
│    --cache-dir       -c                  PATH     Directory to store analysis cache. [default: None]               │
│    --clear-cache         --keep-cache             Clear cache after analysis. [default: clear-cache]               │
│                      -v                  INTEGER  Increase verbosity: -v, -vv, -vvv [default: 0]                   │
│    --help                                         Show this message and exit.                                      │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

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

5. **Save output in msgpack format:**
   ```bash
   codeanalyzer --input ./my-python-project --output /path/to/analysis-results --format msgpack
   ```

### Output

By default, analysis results are printed to stdout in JSON format. When using the `--output` option, results are saved to `analysis.json` in the specified directory.

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management during development.

### Development Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)

2. Clone the repository:
   ```bash
   git clone https://github.com/codellm-devkit/codeanalyzer-python
   cd codeanalyzer-python
   ```

3. Install dependencies using uv:
   ```bash
   uv sync --all-groups
   ```
   This will install all dependencies including development and test dependencies.

### Running from Source

When developing, you can run the tool directly from source:

```bash
uv run codeanalyzer --input /path/to/python/project
```

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

2. Clone the repository:
   ```bash
   git clone https://github.com/codellm-devkit/codeanalyzer-python
   cd codeanalyzer-python
   ```

3. Install dependencies using uv:
   ```bash
   uv sync --all-groups
   ```
   This will install all dependencies including development and test dependencies.

### Running from Source

When developing, you can run the tool directly from source:

```bash
uv run codeanalyzer --input /path/to/python/project
```

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
