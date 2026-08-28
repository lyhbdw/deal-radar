"""RSS Radar v2 — shared schema, state, and source config for FeedSentinel."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone

MAX_TELEGRAM_TEXT = 3900
SOURCES = [
    {"id":"nodeseek","name":"NodeSeek","emoji":"🛰","rss_url":"https://rss.nodeseek.com/","interval":1.0,"categories":["trade","daily","review","tech","info","dev","carpool","expose","photo-share","promotion"]},
    {"id":"sbsb","name":"烧饼论坛","emoji":"🥞","rss_url":"https://sb.sb/rss.xml","interval":1.0,"categories":["综合","公告","优惠","主机","交易","域名","硬件","分享","推广","AI"]},
    {"id":"idcflare","name":"IDC Flare","emoji":"🔥","rss_url":"https://idcflare.com/latest.rss","interval":1.0,"categories":["交易","求助","茶馆","福利","测评","运营"]},
]
SOURCE_IDS = [s["id"] for s in SOURCES]

def utcnow(): return datetime.now(timezone.utc).isoformat()
def clamp_telegram_text(text): return text if len(text) <= MAX_TELEGRAM_TEXT else text[:MAX_TELEGRAM_TEXT-1] + "…"

_init_done = False

def connect(path):
    global _init_done
    db=sqlite3.connect(path,timeout=15)
    db.execute("PRAGMA busy_timeout=15000")
    if not _init_done:
        db.execute("PRAGMA journal_mode=WAL")
        initialize(db)
    return db

def initialize(db):
    global _init_done
    if _init_done: return
    db.executescript("""
    CREATE TABLE IF NOT EXISTS monitor_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_config(source TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,interval REAL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS seen_posts(source TEXT NOT NULL,guid TEXT NOT NULL,title TEXT,link TEXT,author TEXT,category TEXT,pub_date TEXT,matched_keywords TEXT,first_seen TEXT NOT NULL,PRIMARY KEY(source,guid));
    CREATE INDEX IF NOT EXISTS idx_seen_first_seen ON seen_posts(first_seen);
    """)
    db.commit()
    _init_done = True

def state_get(db,key,default=None):
    row=db.execute("SELECT value FROM monitor_state WHERE key=?",(key,)).fetchone();return default if row is None else row[0]
def state_set(db,key,value): db.execute("INSERT OR REPLACE INTO monitor_state(key,value) VALUES(?,?)",(key,str(value)))
def is_source_enabled(db,sid):
    row=db.execute("SELECT enabled FROM source_config WHERE source=?",(sid,)).fetchone();return True if row is None else bool(row[0])
def interval_for(db,sid,default):
    row=db.execute("SELECT interval FROM source_config WHERE source=?",(sid,)).fetchone();return default if row is None or row[0] is None else max(1.0,float(row[0]))
