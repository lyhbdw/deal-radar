#!/bin/sh
# FeedSentinel 每日数据库备份:SQLite 在线备份(WAL 安全),保留 14 份
set -e
DB=/root/nodeseek_monitor.db
DIR=/root/backups
mkdir -p "$DIR"
STAMP=$(date -u +%Y%m%d_%H%M%S)
python3 - "$DB" "$DIR/feedsentinel_$STAMP.db" <<'PYEOF'
import sqlite3,sys
src,dst=sys.argv[1],sys.argv[2]
s=sqlite3.connect(f'file:{src}?mode=ro',uri=True)
d=sqlite3.connect(dst)
s.backup(d)
d.close();s.close()
print(f'backup ok: {dst}')
PYEOF
gzip -f "$DIR/feedsentinel_$STAMP.db"
# 保留最近 14 份
ls -1t "$DIR"/feedsentinel_*.db.gz | tail -n +15 | xargs -r rm -f
echo "kept: $(ls -1 "$DIR" | wc -l) backups"
