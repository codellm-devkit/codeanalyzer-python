#!/bin/sh
# codeanalyzer installer — installs the codeanalyzer-python (`codeanalyzer`) CLI as an
# isolated tool. Mirrors the cargo-dist installer pattern, but because this is a pure-Python
# package (published to PyPI) it installs via uv / pipx / pip rather than downloading a binary.
#
# Usage:
#   curl --proto '=https' --tlsv1.2 -LsSf https://github.com/codellm-devkit/codeanalyzer-python/releases/latest/download/codeanalyzer-installer.sh | sh
#
# Environment overrides:
#   CODEANALYZER_VERSION   release version, e.g. 0.2.0     (default: latest on PyPI)
#   CODEANALYZER_NEO4J     set to 1 to include the [neo4j] extra (live Bolt push driver)
#   CODEANALYZER_INSTALLER force a backend: uv | pipx | pip (default: auto-detect)
set -eu

PKG="codeanalyzer-python"
BIN="codeanalyzer"
VERSION="${CODEANALYZER_VERSION:-}"

# `pkg[extra]==version` / `pkg[extra]` — assemble the PyPI requirement string.
extra=""
[ "${CODEANALYZER_NEO4J:-0}" = "1" ] && extra="[neo4j]"
if [ -n "$VERSION" ]; then
  spec="${PKG}${extra}==${VERSION}"
else
  spec="${PKG}${extra}"
fi

pick_backend() {
  if [ -n "${CODEANALYZER_INSTALLER:-}" ]; then
    echo "$CODEANALYZER_INSTALLER"; return
  fi
  if command -v uv >/dev/null 2>&1; then echo uv; return; fi
  if command -v pipx >/dev/null 2>&1; then echo pipx; return; fi
  echo pip
}

backend="$(pick_backend)"
echo "codeanalyzer: installing $spec via $backend ..."

case "$backend" in
  uv)
    # `uv tool install` puts an isolated CLI on the uv tool path (~/.local/bin by default).
    uv tool install --force "$spec"
    ;;
  pipx)
    pipx install --force "$spec"
    ;;
  pip)
    # Fall back to a user install. Prefer python3; require it to exist.
    if command -v python3 >/dev/null 2>&1; then py=python3; elif command -v python >/dev/null 2>&1; then py=python; else
      echo "codeanalyzer: need python3 (or uv / pipx) to install" >&2; exit 1
    fi
    "$py" -m pip install --user --upgrade "$spec"
    ;;
  *)
    echo "codeanalyzer: unknown installer backend '$backend' (use uv | pipx | pip)" >&2
    exit 1
    ;;
esac

if command -v "$BIN" >/dev/null 2>&1; then
  echo "codeanalyzer: installed — $("$BIN" --help >/dev/null 2>&1 && echo "run '$BIN --help' to get started")"
else
  echo "codeanalyzer: installed, but '$BIN' is not on your PATH yet."
  echo "codeanalyzer: add your tool bin dir to PATH (e.g. export PATH=\"\$HOME/.local/bin:\$PATH\")."
fi
