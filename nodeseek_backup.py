#!/usr/bin/env python3
"""Verified SQLite backup with daily/weekly/monthly retention."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

SOURCE=Path('/root/nodeseek_monitor.db')
BACKUPS=Path('/root/backups/nodeseek')
BACKUPS.mkdir(parents=True,exist_ok=True)
now=datetime.now(timezone.utc)
target=BACKUPS / f"nodeseek-{now:%Y%m%d-%H%M%SZ}.db"

src=sqlite3.connect(SOURCE,timeout=30)
dst=sqlite3.connect(target)
try:
    src.execute('PRAGMA wal_checkpoint(PASSIVE)')
    src.backup(dst)
    result=dst.execute('PRAGMA integrity_check').fetchone()[0]
    if result!='ok': raise RuntimeError(f'backup integrity check failed: {result}')
finally:
    dst.close();src.close()

# Keep: all backups from last 7 days; one newest per ISO week for 4 older weeks;
# one newest per month for 3 older months.
files=sorted(BACKUPS.glob('nodeseek-*.db'),key=lambda p:p.stat().st_mtime,reverse=True)
keep=set(); weeks=set(); months=set()
for p in files:
    dt=datetime.fromtimestamp(p.stat().st_mtime,timezone.utc); age=(now-dt).days
    if age<=7: keep.add(p);continue
    wk=dt.strftime('%G-W%V')
    if age<=35 and wk not in weeks: keep.add(p);weeks.add(wk);continue
    mo=dt.strftime('%Y-%m')
    if age<=120 and mo not in months: keep.add(p);months.add(mo)
for p in files:
    if p not in keep:p.unlink()
print(target)
