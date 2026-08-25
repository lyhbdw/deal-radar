#!/usr/bin/env python3
"""Create a consistent daily SQLite backup; retain the latest 7 snapshots."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

SOURCE = Path('/root/nodeseek_monitor.db')
BACKUPS = Path('/root/backups/nodeseek')
KEEP = 7

BACKUPS.mkdir(parents=True, exist_ok=True)
name = datetime.now(timezone.utc).strftime('nodeseek-%Y%m%d-%H%M%SZ.db')
target = BACKUPS / name
src = sqlite3.connect(SOURCE)
# Checkpoint WAL into main DB so the backup contains all committed data
# and the WAL file gets truncated.
src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
dst = sqlite3.connect(target)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()

for old in sorted(BACKUPS.glob('nodeseek-*.db'))[:-KEEP]:
    old.unlink()
print(target)
