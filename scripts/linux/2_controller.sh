#!/usr/bin/env bash
# 2 of 3: start the key controller. Leave this terminal open; F8 stops it.
cd "$(dirname "$0")/../.." || exit 1
source "${JEDAI_VENV:-$HOME/venvs/jedai}/bin/activate" || exit 1
exec python scripts/run_controller.py
