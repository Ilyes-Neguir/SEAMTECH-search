#!/usr/bin/env bash
# Thin wrapper around scripts/sync_ecc_skills.py (see its docstring).
# Usage: scripts/sync_ecc_skills.sh --check | --list | --update [--commit <sha>]
set -euo pipefail
exec python3 "$(dirname "$0")/sync_ecc_skills.py" "$@"
