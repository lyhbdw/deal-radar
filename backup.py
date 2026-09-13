#!/usr/bin/env python3
"""Create a consistent SQLite backup while the services are running."""
import gzip, shutil, sqlite3, sys
from datetime import datetime
from pathlib import Path

base=Path(__file__).resolve().parent
default_db = base / 'dealradar.db' if (base / 'dealradar.db').exists() else base / 'feedsentinel.db'
source = Path(sys.argv[1]) if len(sys.argv) > 1 else default_db
outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else base / 'backups'
outdir.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
target = outdir / f'dealradar-{stamp}.db'
compressed = outdir / f'dealradar-{stamp}.db.gz'
src=sqlite3.connect(source, timeout=30); dst=sqlite3.connect(target)
try: src.backup(dst)
finally: dst.close(); src.close()
check=sqlite3.connect(target)
assert check.execute('pragma integrity_check').fetchone()[0] == 'ok'
check.close()
with target.open('rb') as inp, gzip.open(compressed,'wb',compresslevel=6) as out: shutil.copyfileobj(inp,out)
target.unlink()
with gzip.open(compressed, 'rb') as inp, open(outdir / f'.verify-{stamp}.db', 'wb') as out:
    shutil.copyfileobj(inp, out)
verified = outdir / f'.verify-{stamp}.db'
try:
    check=sqlite3.connect(verified)
    assert check.execute('pragma integrity_check').fetchone()[0] == 'ok'
    check.close()
finally:
    verified.unlink(missing_ok=True)
all_backups = sorted(list(outdir.glob('dealradar-*.db.gz')) + list(outdir.glob('feedsentinel-*.db.gz')), key=lambda p: p.stat().st_mtime, reverse=True)
for old in all_backups[14:]: old.unlink()
print(compressed)
