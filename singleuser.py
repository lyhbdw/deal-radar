"""Single-user FeedSentinel storage layer."""
import sqlite3
from datetime import datetime, timezone
from rss_core import SOURCE_IDS

SOURCES = tuple(SOURCE_IDS)
MAX_KEYWORDS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS keywords(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL COLLATE NOCASE UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_config(
  source TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
  interval REAL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_posts(
  source TEXT NOT NULL, guid TEXT NOT NULL, title TEXT, link TEXT,
  author TEXT, category TEXT, pub_date TEXT, matched_keywords TEXT,
  first_seen TEXT NOT NULL, PRIMARY KEY(source,guid)
);
CREATE INDEX IF NOT EXISTS idx_seen_first_seen ON seen_posts(first_seen);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, guid TEXT NOT NULL,
  message TEXT NOT NULL, markup TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt TEXT NOT NULL,
  claimed_at TEXT, last_error TEXT, sent_at TEXT, created_at TEXT NOT NULL,
  UNIQUE(source,guid)
);
CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(status,next_attempt);
CREATE TABLE IF NOT EXISTS notification_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, guid TEXT NOT NULL,
  title TEXT, category TEXT, matched_keywords TEXT, pushed_at TEXT NOT NULL,
  UNIQUE(source,guid)
);
"""

def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def connect(path):
    db = sqlite3.connect(path, timeout=5)
    db.execute('PRAGMA busy_timeout=5000')
    db.execute('PRAGMA synchronous=NORMAL')
    db.execute('PRAGMA foreign_keys=ON')
    mode = db.execute('PRAGMA journal_mode').fetchone()[0].lower()
    if mode != 'wal':
        db.execute('PRAGMA journal_mode=WAL')
    row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='keywords'").fetchone()
    if not row:
        db.executescript(SCHEMA)
        db.commit()
    return db

def enabled_sources(db):
    configured = {r[0]: bool(r[1]) for r in db.execute('SELECT source,enabled FROM source_config')}
    return {source for source in SOURCES if configured.get(source, True)}

def source_enabled(db, source):
    row = db.execute('SELECT enabled FROM source_config WHERE source=?', (source,)).fetchone()
    return True if row is None else bool(row[0])

def source_interval(db, source, default):
    row = db.execute('SELECT interval FROM source_config WHERE source=?', (source,)).fetchone()
    return default if row is None or row[0] is None else max(1.0, float(row[0]))

def list_keywords(db):
    return db.execute('SELECT id,keyword FROM keywords WHERE enabled=1 ORDER BY keyword').fetchall()

def add_keywords(db, raw_keywords):
    valid, rejected, seen = [], [], set()
    for raw in raw_keywords:
        keyword = str(raw).strip().replace('\n', ' ')
        if not keyword:
            continue
        if len(keyword) > 40:
            rejected.append((keyword, '超过40字符'))
        elif keyword.casefold() not in seen:
            seen.add(keyword.casefold()); valid.append(keyword)
    current = db.execute('SELECT count(*) FROM keywords WHERE enabled=1').fetchone()[0]
    added, existing = [], []
    for keyword in valid:
        if current + len(added) >= MAX_KEYWORDS:
            rejected.append((keyword, f'最多{MAX_KEYWORDS}个关键词')); continue
        cur = db.execute('INSERT OR IGNORE INTO keywords(keyword,created_at) VALUES(?,?)', (keyword, now()))
        (added if cur.rowcount else existing).append(keyword)
    db.commit()
    return added, existing, rejected

def matches(db, post):
    text = (post.get('title', '') + ' ' + post.get('description', '')).casefold()
    return [keyword for _, keyword in list_keywords(db) if keyword.casefold() in text]
