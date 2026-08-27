"""Shared SQLite schema, source registry, and reliability helpers for RSS radar."""
import json
import sqlite3
from datetime import datetime, timezone
from urllib.error import HTTPError

STATE_BOOTSTRAPPED = "initial_baseline_complete"

SOURCES = [
    {"id":"nodeseek","name":"NodeSeek","emoji":"🛰","rss_url":"https://rss.nodeseek.com/","interval":1,
     "categories":["trade","daily","review","tech","info","dev","carpool","expose","photo-share","promotion"]},
    {"id":"sbsb","name":"烧饼论坛","emoji":"🥞","rss_url":"https://sb.sb/rss.xml","interval":1,
     "categories":["综合","公告","优惠","主机","交易","域名","硬件","分享","推广","AI"]},
    {"id":"idcflare","name":"IDC Flare","emoji":"🔥","rss_url":"https://idcflare.com/latest.rss","interval":1,
     "categories":["交易","求助","茶馆","福利","测评","运营"]},
]
SOURCE_IDS = [s["id"] for s in SOURCES]


def utcnow(): return datetime.now(timezone.utc).isoformat()
def get_source(source_id): return next((s for s in SOURCES if s["id"] == source_id), None)
def _exists(db, name): return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
def _cols(db, name): return {r[1] for r in db.execute(f"PRAGMA table_info({name})")}
def _state_get(db,key):
    row=db.execute("SELECT value FROM monitor_state WHERE key=?",(key,)).fetchone()
    return row[0] if row else None

def ensure_state_table(db):
    db.execute("CREATE TABLE IF NOT EXISTS monitor_state (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
def state_get(db,key,default=None):
    ensure_state_table(db); value=_state_get(db,key); return default if value is None else value
def state_set(db,key,value,commit=True):
    ensure_state_table(db); db.execute("INSERT OR REPLACE INTO monitor_state(key,value) VALUES (?,?)",(key,str(value)))
    if commit: db.commit()

def ensure_keywords_schema(db):
    if not _exists(db,"keywords"):
        db.execute("CREATE TABLE keywords(id INTEGER PRIMARY KEY AUTOINCREMENT,keyword TEXT NOT NULL COLLATE NOCASE UNIQUE,added_at TEXT NOT NULL)")
    elif "id" not in _cols(db,"keywords"):
        db.execute("ALTER TABLE keywords RENAME TO keywords_legacy")
        db.execute("CREATE TABLE keywords(id INTEGER PRIMARY KEY AUTOINCREMENT,keyword TEXT NOT NULL COLLATE NOCASE UNIQUE,added_at TEXT NOT NULL)")
        db.execute("INSERT INTO keywords(keyword,added_at) SELECT keyword,added_at FROM keywords_legacy")
        db.execute("DROP TABLE keywords_legacy")

def ensure_category_table(db):
    if not _exists(db,"category_config"):
        db.execute("CREATE TABLE category_config(source TEXT NOT NULL,category TEXT NOT NULL,enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),PRIMARY KEY(source,category))")
    elif "source" not in _cols(db,"category_config"):
        db.execute("ALTER TABLE category_config RENAME TO category_config_legacy")
        db.execute("CREATE TABLE category_config(source TEXT NOT NULL,category TEXT NOT NULL,enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),PRIMARY KEY(source,category))")
        db.execute("INSERT INTO category_config SELECT 'nodeseek',category,enabled FROM category_config_legacy")
        db.execute("DROP TABLE category_config_legacy")
def get_category_config(db,source):
    ensure_category_table(db)
    return {r[0]:bool(r[1]) for r in db.execute("SELECT category,enabled FROM category_config WHERE source=?",(source,))}
def set_category_enabled(db,source,category,enabled):
    ensure_category_table(db)
    db.execute("INSERT INTO category_config(source,category,enabled) VALUES(?,?,?) ON CONFLICT(source,category) DO UPDATE SET enabled=excluded.enabled",(source,category,int(enabled)))
    db.commit()

def ensure_source_config(db):
    db.execute("CREATE TABLE IF NOT EXISTS source_config(source TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,interval REAL,updated_at TEXT NOT NULL)")
def source_is_enabled(db,source):
    ensure_source_config(db); row=db.execute("SELECT enabled FROM source_config WHERE source=?",(source,)).fetchone(); return True if row is None else bool(row[0])
def set_source_enabled(db,source,enabled):
    ensure_source_config(db); db.execute("INSERT INTO source_config(source,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",(source,int(enabled),utcnow())); db.commit()
def source_interval(db,source,default):
    ensure_source_config(db); row=db.execute("SELECT interval FROM source_config WHERE source=?",(source,)).fetchone(); return default if not row or row[0] is None else max(1.0,float(row[0]))
def set_source_interval(db,source,interval):
    ensure_source_config(db); db.execute("INSERT INTO source_config(source,enabled,interval,updated_at) VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET interval=excluded.interval,updated_at=excluded.updated_at",(source,1,max(1.0,float(interval)),utcnow())); db.commit()

def ensure_notification_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,guid TEXT NOT NULL,
      text TEXT NOT NULL,reply_markup TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
      attempts INTEGER NOT NULL DEFAULT 0,next_attempt TEXT NOT NULL,claimed_at TEXT,
      last_error TEXT,sent_at TEXT,created_at TEXT NOT NULL,UNIQUE(source,guid))""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(status,next_attempt)")
def enqueue_notification(db,source,guid,text,reply_markup):
    ensure_notification_schema(db); now=utcnow()
    cur=db.execute("INSERT OR IGNORE INTO notifications(source,guid,text,reply_markup,status,attempts,next_attempt,created_at) VALUES(?,?,?,?, 'pending',0,?,?)",(source,guid,text,reply_markup,now,now)); db.commit()
    row=db.execute("SELECT id FROM notifications WHERE source=? AND guid=?",(source,guid)).fetchone(); return row[0]
def claim_due_notifications(db,limit=10):
    ensure_notification_schema(db); now=utcnow()
    # reclaim stale inflight sends after 2 minutes
    db.execute("UPDATE notifications SET status='pending',claimed_at=NULL WHERE status='sending' AND claimed_at < datetime('now','-2 minutes')")
    rows=db.execute("SELECT id,source,guid,text,reply_markup,status,attempts FROM notifications WHERE status='pending' AND next_attempt<=? ORDER BY id LIMIT ?",(now,limit)).fetchall()
    for r in rows: db.execute("UPDATE notifications SET status='sending',attempts=attempts+1,claimed_at=? WHERE id=?",(now,r[0]))
    db.commit()
    return [(r[0],r[1],r[2],r[3],r[4],r[5],r[6]+1) for r in rows]
def mark_notification_sent(db,nid): db.execute("UPDATE notifications SET status='sent',sent_at=?,last_error=NULL WHERE id=?",(utcnow(),nid)); db.commit()
def mark_notification_retry(db,nid,error,retry_delay=30):
    db.execute("UPDATE notifications SET status='pending',next_attempt=datetime('now',?),last_error=?,claimed_at=NULL WHERE id=?",(f'+{max(0,int(retry_delay))} seconds',str(error)[:500],nid)); db.commit()
def notification_counts(db):
    ensure_notification_schema(db); return dict(db.execute("SELECT status,COUNT(*) FROM notifications GROUP BY status"))

def migrate_multi_source(db,default_source="nodeseek"):
    """Idempotently normalize legacy database into source-aware schema."""
    ensure_state_table(db); ensure_keywords_schema(db); ensure_category_table(db); ensure_source_config(db)
    # Rebuild seen_posts to proper source+guid identity if legacy guid-only PK.
    if _exists(db,"seen_posts") and "source" not in _cols(db,"seen_posts"):
        db.execute("ALTER TABLE seen_posts ADD COLUMN source TEXT DEFAULT 'nodeseek'")
    if _exists(db,"seen_posts"):
        sql=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='seen_posts'").fetchone()[0] or ''
        if "PRIMARY KEY (source, guid)" not in sql.replace('\n',' '):
            db.execute("ALTER TABLE seen_posts RENAME TO seen_posts_legacy")
            db.execute("""CREATE TABLE seen_posts(source TEXT NOT NULL,guid TEXT NOT NULL,title TEXT,link TEXT,author TEXT,category TEXT,pub_date TEXT,matched_keywords TEXT,first_seen TEXT,PRIMARY KEY(source,guid))""")
            old=_cols(db,"seen_posts_legacy"); source="source" if "source" in old else f"'{default_source}'"
            select=[source]
            for col in ('guid','title','link','author','category','pub_date','matched_keywords','first_seen'):
                select.append(col if col in old else 'NULL')
            db.execute("INSERT OR IGNORE INTO seen_posts(source,guid,title,link,author,category,pub_date,matched_keywords,first_seen) SELECT " + ','.join(select) + " FROM seen_posts_legacy")
            db.execute("DROP TABLE seen_posts_legacy")
    else:
        db.execute("""CREATE TABLE seen_posts(source TEXT NOT NULL,guid TEXT NOT NULL,title TEXT,link TEXT,author TEXT,category TEXT,pub_date TEXT,matched_keywords TEXT,first_seen TEXT,PRIMARY KEY(source,guid))""")
    if not _exists(db,"push_history"):
        db.execute("CREATE TABLE push_history(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT,guid TEXT,title TEXT,link TEXT,author TEXT,category TEXT,pub_date TEXT,matched_keywords TEXT,pushed_at TEXT)")
    else:
        pcols=_cols(db,"push_history")
        for col in ('source','guid','title','link','author','category','pub_date','matched_keywords','pushed_at'):
            if col not in pcols: db.execute(f"ALTER TABLE push_history ADD COLUMN {col} TEXT")
        db.execute("UPDATE push_history SET source=? WHERE source IS NULL",(default_source,))
    db.execute("CREATE INDEX IF NOT EXISTS idx_seen_first_seen ON seen_posts(first_seen)"); db.execute("CREATE INDEX IF NOT EXISTS idx_push_source_time ON push_history(source,pushed_at)")
    for key in ("initial_baseline_complete","last_rss_success","consecutive_errors","last_rss_error","last_match","last_match_title"):
        v=_state_get(db,key)
        if v is not None: state_set(db,f"{key}:{default_source}",v,False); db.execute("DELETE FROM monitor_state WHERE key=?",(key,))
    ensure_notification_schema(db); db.commit()

def bootstrap_required(db,source):
    ensure_state_table(db)
    key=f"{STATE_BOOTSTRAPPED}:{source}"
    if _state_get(db,key) is not None:return False
    if db.execute("SELECT 1 FROM seen_posts WHERE source=? LIMIT 1",(source,)).fetchone(): mark_bootstrapped(db,source);return False
    return True
def mark_bootstrapped(db,source): state_set(db,f"{STATE_BOOTSTRAPPED}:{source}","1")
def retry_after_seconds(error:HTTPError,default:int):
    try:
        raw=error.headers.get('Retry-After') if error.headers else None
        if raw:return max(1,int(raw))
        return max(1,int(json.loads(error.read().decode()).get('parameters',{}).get('retry_after',default)))
    except Exception:return default
def validate_keywords(raw_keywords,max_length=40,max_count=10):
    accepted=[]; rejected=[]; seen=set()
    for raw in raw_keywords:
        kw=str(raw).strip()
        if not kw: rejected.append((kw,'为空'));continue
        if len(kw)>max_length: rejected.append((kw,f'超过 {max_length} 个字符'));continue
        if kw.casefold() in seen:continue
        seen.add(kw.casefold())
        if len(accepted)>=max_count: rejected.append((kw,f'单次最多 {max_count} 个'));continue
        accepted.append(kw)
    return accepted,rejected
