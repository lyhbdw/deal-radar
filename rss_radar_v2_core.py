"""RSS Radar v2 — normalized schema, independent source workers, atomic delivery."""
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
def get_source(sid): return next((s for s in SOURCES if s["id"] == sid), None)
def clamp_telegram_text(text): return text if len(text) <= MAX_TELEGRAM_TEXT else text[:MAX_TELEGRAM_TEXT-1] + "…"
def connect(path):
    db=sqlite3.connect(path,timeout=15)
    db.execute("PRAGMA journal_mode=WAL");db.execute("PRAGMA busy_timeout=15000")
    initialize(db);return db

def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS monitor_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS keywords(id INTEGER PRIMARY KEY AUTOINCREMENT,keyword TEXT NOT NULL COLLATE NOCASE UNIQUE,added_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_config(source TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,interval REAL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS category_config(source TEXT NOT NULL,category TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,discovered_at TEXT NOT NULL,PRIMARY KEY(source,category));
    CREATE TABLE IF NOT EXISTS seen_posts(source TEXT NOT NULL,guid TEXT NOT NULL,title TEXT,link TEXT,author TEXT,category TEXT,pub_date TEXT,matched_keywords TEXT,first_seen TEXT NOT NULL,PRIMARY KEY(source,guid));
    CREATE TABLE IF NOT EXISTS push_history(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,guid TEXT NOT NULL,title TEXT,link TEXT,author TEXT,category TEXT,pub_date TEXT,matched_keywords TEXT,pushed_at TEXT NOT NULL,UNIQUE(source,guid));
    CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,guid TEXT NOT NULL,text TEXT NOT NULL,reply_markup TEXT NOT NULL,title TEXT,link TEXT,author TEXT,category TEXT,pub_date TEXT,matched_keywords TEXT,status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','sending','sent','failed')),attempts INTEGER NOT NULL DEFAULT 0,next_attempt TEXT NOT NULL,claimed_at TEXT,last_error TEXT,sent_at TEXT,created_at TEXT NOT NULL,UNIQUE(source,guid));
    CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(status,next_attempt);
    CREATE INDEX IF NOT EXISTS idx_seen_first_seen ON seen_posts(first_seen);
    CREATE INDEX IF NOT EXISTS idx_push_source_time ON push_history(source,pushed_at);
    """)
    # Migrate v1 category configuration before seeding categories.
    category_cols={r[1] for r in db.execute("PRAGMA table_info(category_config)")}
    if "discovered_at" not in category_cols:
        db.execute("ALTER TABLE category_config ADD COLUMN discovered_at TEXT")
        db.execute("UPDATE category_config SET discovered_at=? WHERE discovered_at IS NULL",(utcnow(),))
    # v1 DB migration: add optional queue columns, no data loss.
    cols={r[1] for r in db.execute("PRAGMA table_info(notifications)")}
    for col in ("title","link","author","category","pub_date","matched_keywords"):
        if col not in cols: db.execute(f"ALTER TABLE notifications ADD COLUMN {col} TEXT")
    now=utcnow()
    for src in SOURCES:
        for cat in src["categories"]:
            db.execute("INSERT OR IGNORE INTO category_config(source,category,enabled,discovered_at) VALUES(?,?,1,?)",(src["id"],cat,now))
    db.commit()

def state_get(db,key,default=None):
    row=db.execute("SELECT value FROM monitor_state WHERE key=?",(key,)).fetchone();return default if row is None else row[0]
def state_set(db,key,value): db.execute("INSERT OR REPLACE INTO monitor_state(key,value) VALUES(?,?)",(key,str(value)))
def is_source_enabled(db,sid):
    row=db.execute("SELECT enabled FROM source_config WHERE source=?",(sid,)).fetchone();return True if row is None else bool(row[0])
def set_source_enabled(db,sid,enabled):
    if sid not in SOURCE_IDS: raise ValueError(sid)
    db.execute("INSERT INTO source_config(source,enabled,interval,updated_at) VALUES(?,?,NULL,?) ON CONFLICT(source) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",(sid,int(enabled),utcnow()));db.commit()
def enabled_sources(db): return {sid for sid in SOURCE_IDS if is_source_enabled(db,sid)}
def set_enabled_sources(db,sids):
    sids=set(sids);unknown=sids-set(SOURCE_IDS)
    if unknown: raise ValueError(str(unknown))
    for sid in SOURCE_IDS:set_source_enabled(db,sid,sid in sids)
def interval_for(db,sid,default):
    row=db.execute("SELECT interval FROM source_config WHERE source=?",(sid,)).fetchone();return default if row is None or row[0] is None else max(1.0,float(row[0]))
def categories(db,sid): return [(r[0],bool(r[1])) for r in db.execute("SELECT category,enabled FROM category_config WHERE source=? ORDER BY category",(sid,))]
def discover_categories(db,sid,values):
    for cat in set(filter(None,values)): db.execute("INSERT OR IGNORE INTO category_config(source,category,enabled,discovered_at) VALUES(?,?,1,?)",(sid,cat,utcnow()))
    db.commit()
def disabled_categories(db,sid): return {cat for cat,on in categories(db,sid) if not on}

def is_bootstrapped(db,sid): return state_get(db,f"initial_baseline_complete:{sid}") == "1"
def mark_bootstrapped(db,sid): state_set(db,f"initial_baseline_complete:{sid}","1")
def add_seen(db,sid,post,matched): db.execute("INSERT OR IGNORE INTO seen_posts(source,guid,title,link,author,category,pub_date,matched_keywords,first_seen) VALUES(?,?,?,?,?,?,?,?,?)",(sid,post['guid'],post['title'],post['link'],post['author'],post['category'],post['pub_date'],','.join(matched),utcnow()))
def is_seen(db,sid,guid): return db.execute("SELECT 1 FROM seen_posts WHERE source=? AND guid=?",(sid,guid)).fetchone() is not None

def queue(db,sid,post,text,markup,matched):
    now=utcnow()
    db.execute("INSERT OR IGNORE INTO notifications(source,guid,text,reply_markup,title,link,author,category,pub_date,matched_keywords,status,attempts,next_attempt,created_at) VALUES(?,?,?,?,?,?,?,?,?,?, 'pending',0,?,?)",(sid,post['guid'],clamp_telegram_text(text),markup,post['title'],post['link'],post['author'],post['category'],post['pub_date'],','.join(matched),now,now))
    db.commit()
def claim_due(db,limit):
    db.execute("UPDATE notifications SET status='pending',claimed_at=NULL WHERE status='sending' AND claimed_at < datetime('now','-2 minutes')")
    rows=db.execute("SELECT id,source,guid,text,reply_markup,attempts FROM notifications WHERE status='pending' AND next_attempt<=? ORDER BY id LIMIT ?",(utcnow(),limit)).fetchall()
    for row in rows: db.execute("UPDATE notifications SET status='sending',attempts=attempts+1,claimed_at=? WHERE id=?",(utcnow(),row[0]))
    db.commit();return [(r[0],r[1],r[2],r[3],r[4],r[5]+1) for r in rows]
def delivered(db,nid):
    row=db.execute("SELECT source,guid,title,link,author,category,pub_date,matched_keywords FROM notifications WHERE id=?",(nid,)).fetchone()
    if not row:return
    now=utcnow()
    # Atomic status + history transaction.
    db.execute("UPDATE notifications SET status='sent',sent_at=?,last_error=NULL WHERE id=?",(now,nid))
    db.execute("INSERT OR IGNORE INTO push_history(source,guid,title,link,author,category,pub_date,matched_keywords,pushed_at) VALUES(?,?,?,?,?,?,?,?,?)",(*row,now))
    db.commit()
def retry(db,nid,error,delay,failed=False):
    if failed: db.execute("UPDATE notifications SET status='failed',last_error=?,claimed_at=NULL WHERE id=?",(str(error)[:500],nid))
    else: db.execute("UPDATE notifications SET status='pending',next_attempt=datetime('now',?),last_error=?,claimed_at=NULL WHERE id=?",(f'+{max(1,int(delay))} seconds',str(error)[:500],nid))
    db.commit()
def queue_counts(db): return dict(db.execute("SELECT status,COUNT(*) FROM notifications GROUP BY status"))

# Compatibility surface used by the existing Telegram control-plane bot.
def ensure_keywords_schema(db): initialize(db)
def ensure_state_table(db): initialize(db)
def get_category_config(db,sid): return dict(categories(db,sid))
def set_category_enabled(db,sid,category,enabled):
    db.execute("INSERT INTO category_config(source,category,enabled,discovered_at) VALUES(?,?,?,?) ON CONFLICT(source,category) DO UPDATE SET enabled=excluded.enabled",(sid,category,int(enabled),utcnow()));db.commit()
def source_is_enabled(db,sid): return is_source_enabled(db,sid)
def notification_counts(db): return queue_counts(db)
def claim_due_notifications(db,limit=10):
    rows=claim_due(db,limit)
    return [(r[0],r[1],r[2],r[3],r[4],'pending',r[5]-1) for r in rows]
def mark_notification_retry(db,nid,error,retry_delay=30): retry(db,nid,error,retry_delay)
def migrate_multi_source(db,default_source='nodeseek'): initialize(db)

def queue_counts(db): return dict(db.execute("SELECT status,COUNT(*) FROM notifications GROUP BY status"))
