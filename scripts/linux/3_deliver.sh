#!/usr/bin/env bash
# 3 of 3: run one delivery mission (needs 1 and 2 running). Extra args go to go.py, e.g. --dry-run.
cd "$(dirname "$0")/../.." || exit 1
source "${JEDAI_VENV:-$HOME/venvs/jedai}/bin/activate" || exit 1
exec python scripts/go.py deliver "$@"
