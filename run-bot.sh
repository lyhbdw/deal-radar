#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONUNBUFFERED=1
export DEALRADAR_ENV="${DEALRADAR_ENV:-${FEEDSENTINEL_ENV:-$BASE_DIR/.env}}"
exec python3 "$BASE_DIR/dealradar_bot.py"
