#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
BACKUP="${1:?usage: restore.sh BACKUP_DB}"
[ -f "$BACKUP" ] || { echo "backup not found: $BACKUP" >&2; exit 1; }
if systemctl is-active --quiet feedsentinel-bot.service || systemctl is-active --quiet feedsentinel-monitor.service; then
  echo 'stop FeedSentinel services before restore' >&2; exit 1
fi
python3 - "$BACKUP" "$BASE_DIR/feedsentinel.db" <<'PY'
import sqlite3, sys
src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2])
try: src.backup(dst)
finally: dst.close(); src.close()
check=sqlite3.connect(sys.argv[2]); result=check.execute('pragma integrity_check').fetchone()[0]; check.close()
if result != 'ok': raise SystemExit(f'integrity check failed: {result}')
print('restored',sys.argv[2])
PY
