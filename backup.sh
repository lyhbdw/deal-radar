#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DB_FILE="$BASE_DIR/dealradar.db"
[ -f "$DB_FILE" ] || DB_FILE="$BASE_DIR/feedsentinel.db"
exec python3 "$BASE_DIR/backup.py" "$DB_FILE" "${1:-$BASE_DIR/backups}"
