#!/usr/bin/env bash
# Cross-platform POSIX wrapper for Alpha's guarded source updater.
# The root Makefile invokes this through its Git Bash/POSIX runner contract.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON_BIN="${ALPHA_UPDATER_PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN=python
fi
exec "$PYTHON_BIN" "$ROOT/scripts/auto_update.py" "$@"
