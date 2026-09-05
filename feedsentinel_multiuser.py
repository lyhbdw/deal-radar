"""Multi-user subscription data layer for FeedSentinel."""
import sqlite3
from datetime import datetime, timezone

SOURCES = ("nodeseek", "sbsb", "idcflare")
MAX_KEYWORDS_PER_USER = 30

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL UNIQUE,
  username TEXT, first_name TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_keywords(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  keyword TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL,
  UNIQUE(user_id, keyword)
);
CREATE TABLE IF NOT EXISTS user_sources(
  user_id INTEGER NOT NULL, source TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, source)
);
CREATE TABLE IF NOT EXISTS user_categories(
  user_id INTEGER NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id, source, category)
);
CREATE TABLE IF NOT EXISTS user_notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  source TEXT NOT NULL, guid TEXT NOT NULL, message TEXT NOT NULL,
  markup TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt TEXT NOT NULL, claimed_at TEXT, last_error TEXT, sent_at TEXT, created_at TEXT NOT NULL,
  UNIQUE(user_id, source, guid)
);
CREATE INDEX IF NOT EXISTS idx_user_notifications_due ON user_notifications(status, next_attempt);
CREATE TABLE IF NOT EXISTS user_notification_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, source TEXT NOT NULL,
  guid TEXT NOT NULL, title TEXT, category TEXT, matched_keywords TEXT, pushed_at TEXT NOT NULL,
  UNIQUE(user_id, source, guid)
);
"""

def now():
    """Must match rss_radar_v2_core.utcnow() format ('YYYY-MM-DD HH:MM:SS')."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

_schema_done = False

def ensure_multiuser_schema(db):
    global _schema_done
    if _schema_done: return
    db.executescript(_SCHEMA_SQL)
    db.commit()
    _schema_done = True

def ensure_user(db, user_id, chat_id=None, username="", first_name=""):
    chat_id = int(chat_id if chat_id is not None else user_id)
    db.execute("INSERT INTO users(user_id,chat_id,username,first_name,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id,username=excluded.username,first_name=excluded.first_name,updated_at=excluded.updated_at", (int(user_id), chat_id, username or "", first_name or "", now(), now()))
    db.commit()

def enabled_sources_for_user(db, user_id):
    return {r[0] for r in db.execute("SELECT source FROM user_sources WHERE user_id=? AND enabled=1", (int(user_id),))}

def set_user_sources(db, user_id, sources):
    sources = set(sources)
    for source in SOURCES:
        db.execute("INSERT INTO user_sources(user_id,source,enabled) VALUES(?,?,?) ON CONFLICT(user_id,source) DO UPDATE SET enabled=excluded.enabled", (int(user_id), source, int(source in sources)))
    db.commit()

def add_user_keywords(db, user_id, raw_keywords):
    valid=[]; rejected=[]; seen=set()
    for raw in raw_keywords:
        keyword=str(raw).strip()
        if not keyword: rejected.append((keyword,"为空")); continue
        if len(keyword)>40: rejected.append((keyword,"超过40字符")); continue
        if keyword.casefold() in seen: continue
        seen.add(keyword.casefold()); valid.append(keyword)
    current=db.execute("SELECT COUNT(*) FROM user_keywords WHERE user_id=?",(int(user_id),)).fetchone()[0]
    added=[]; existing=[]
    for keyword in valid:
        if current+len(added)>=MAX_KEYWORDS_PER_USER:
            rejected.append((keyword,f"每人最多{MAX_KEYWORDS_PER_USER}个关键词")); continue
        cur=db.execute("INSERT OR IGNORE INTO user_keywords(user_id,keyword,created_at) VALUES(?,?,?)",(int(user_id),keyword,now()))
        (added if cur.rowcount else existing).append(keyword)
    db.commit(); return added,existing,rejected

def subscription_matches(db, post):
    """Return {user_id: [matched keyword]} for one normalized post."""
    text=(post.get("title","")+" "+post.get("description","")).casefold()
    result={}
    rows=db.execute("""
      SELECT k.user_id,k.keyword FROM user_keywords k
      JOIN user_sources s ON s.user_id=k.user_id AND s.source=? AND s.enabled=1
      JOIN users u ON u.user_id=k.user_id AND u.active=1
    """,(post["source"],)).fetchall()
    for user_id, keyword in rows:
        if keyword.casefold() in text: result.setdefault(user_id,[]).append(keyword)
    return result
