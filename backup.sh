#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$BASE_DIR/backup.py" "$BASE_DIR/feedsentinel.db" "${1:-$BASE_DIR/backups}"
