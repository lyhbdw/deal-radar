#!/usr/bin/env python3
"""
NodeSeek 关键词管理工具
用法:
  python3 /root/nodeseek_keywords.py list          # 列出所有关键词
  python3 /root/nodeseek_keywords.py add 关键词1 关键词2  # 添加关键词
  python3 /root/nodeseek_keywords.py del 关键词1 关键词2  # 删除关键词
  python3 /root/nodeseek_keywords.py clear          # 清空所有关键词
"""

import sqlite3
import sys
from datetime import datetime, timezone

from nodeseek_core import ensure_keywords_schema, validate_keywords

DB_PATH = "/root/nodeseek_monitor.db"


def get_db():
    db = sqlite3.connect(DB_PATH)
    ensure_keywords_schema(db)
    return db


def list_keywords(db):
    rows = db.execute("SELECT keyword FROM keywords ORDER BY keyword").fetchall()
    if not rows:
        print("(空)")
        return
    print(f"当前关键词 ({len(rows)} 个):")
    for (kw,) in rows:
        print(f"  {kw}")


def add_keywords(db, keywords):
    valid, rejected = validate_keywords(keywords)
    now = datetime.now(timezone.utc).isoformat()
    added, skipped = [], []
    for kw in valid:
        existing = db.execute("SELECT 1 FROM keywords WHERE lower(keyword) = lower(?)", (kw,)).fetchone()
        if existing:
            skipped.append(kw)
        else:
            db.execute("INSERT INTO keywords (keyword, added_at) VALUES (?, ?)", (kw, now))
            added.append(kw)
    db.commit()
    print(f"已添加 {len(added)} 个: {', '.join(added)}" if added else "未添加任何关键词")
    if skipped:
        print(f"已存在 {len(skipped)} 个: {', '.join(skipped)}")
    if rejected:
        print(f"被拒绝: {', '.join(f'{k}({r})' for k, r in rejected)}")


def del_keywords(db, keywords):
    deleted, missing = [], []
    for kw in keywords:
        cur = db.execute("DELETE FROM keywords WHERE lower(keyword) = lower(?)", (kw,))
        (deleted if cur.rowcount else missing).append(kw)
    db.commit()
    print(f"已删除 {len(deleted)} 个: {', '.join(deleted)}" if deleted else "未删除任何关键词")
    if missing:
        print(f"不存在: {', '.join(missing)}")


def clear_keywords(db):
    db.execute("DELETE FROM keywords")
    db.commit()
    print("已清空所有关键词")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    db = get_db()
    cmd = sys.argv[1]

    if cmd == "list":
        list_keywords(db)
    elif cmd == "add":
        if len(sys.argv) < 3:
            print("用法: add 关键词1 关键词2 ...")
            return
        add_keywords(db, sys.argv[2:])
    elif cmd == "del":
        if len(sys.argv) < 3:
            print("用法: del 关键词1 关键词2 ...")
            return
        del_keywords(db, sys.argv[2:])
    elif cmd == "clear":
        clear_keywords(db)
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)

    db.close()


if __name__ == "__main__":
    main()
