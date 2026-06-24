#!/bin/sh
# canpy installer — installs the codeanalyzer-python (`canpy`) CLI as an isolated
# tool. Mirrors the cargo-dist installer pattern, but because this is a pure-Python
# package (published to PyPI) it installs via uv / pipx / pip rather than downloading a binary.
#
# Usage:
#   curl --proto '=https' --tlsv1.2 -LsSf https://github.com/codellm-devkit/codeanalyzer-python/releases/latest/download/canpy-installer.sh | sh
#
# Environment overrides:
#   CANPY_VERSION     release version, e.g. 0.2.0     (default: latest on PyPI)
#   CANPY_NEO4J       set to 1 to include the [neo4j] extra (live Bolt push driver)
#   CANPY_INSTALLER   force a backend: uv | pipx | pip (default: auto-detect)
set -eu

PKG="codeanalyzer-python"
BIN="canpy"
VERSION="${CANPY_VERSION:-}"

# `pkg[extra]==version` / `pkg[extra]` — assemble the PyPI requirement string.
extra=""
[ "${CANPY_NEO4J:-0}" = "1" ] && extra="[neo4j]"
if [ -n "$VERSION" ]; then
  spec="${PKG}${extra}==${VERSION}"
else
  spec="${PKG}${extra}"
fi

pick_backend() {
  if [ -n "${CANPY_INSTALLER:-}" ]; then
    echo "$CANPY_INSTALLER"; return
  fi
  if command -v uv >/dev/null 2>&1; then echo uv; return; fi
  if command -v pipx >/dev/null 2>&1; then echo pipx; return; fi
  echo pip
}

backend="$(pick_backend)"
echo "canpy: installing $spec via $backend ..."

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
      echo "canpy: need python3 (or uv / pipx) to install" >&2; exit 1
    fi
    "$py" -m pip install --user --upgrade "$spec"
    ;;
  *)
    echo "canpy: unknown installer backend '$backend' (use uv | pipx | pip)" >&2
    exit 1
    ;;
esac

if command -v "$BIN" >/dev/null 2>&1; then
  echo "canpy: installed — $("$BIN" --help >/dev/null 2>&1 && echo "run '$BIN --help' to get started")"
else
  echo "canpy: installed, but '$BIN' is not on your PATH yet."
  echo "canpy: add your tool bin dir to PATH (e.g. export PATH=\"\$HOME/.local/bin:\$PATH\")."
fi
