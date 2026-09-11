#!/usr/bin/env python3
import fcntl, json, os, signal, sqlite3, subprocess, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from singleuser import connect, matches, source_enabled, source_interval, now
from rss_core import SOURCES
from rss_monitor import fetch, message

BASE=Path(__file__).resolve().parent

def load_env():
    path=Path(os.getenv('FEEDSENTINEL_ENV',BASE/'.env'))
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1); os.environ.setdefault(key.strip(),value.strip())
load_env()
DB_PATH=os.getenv('FEEDSENTINEL_DB',str(BASE/'feedsentinel.db'))
TOKEN=os.getenv('NODESEEK_BOT_TOKEN','')
MAX_ATTEMPTS=10; MAX_DELIVERIES=20; running=True

def lock_instance():
    lock=open(BASE/'monitor.lock','w'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); return lock

def send(chat,text,markup):
    payload=json.dumps({'chat_id':chat,'text':text,'parse_mode':'HTML','reply_markup':json.loads(markup)}).encode()
    req=urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/sendMessage',data=payload,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=12) as response: result=json.loads(response.read())
        return bool(result.get('ok')),int(result.get('parameters',{}).get('retry_after',30)),result.get('description','')
    except urllib.error.HTTPError as exc:
        try: body=json.loads(exc.read())
        except Exception: body={}
        return False,int(body.get('parameters',{}).get('retry_after',30)),f'HTTP {exc.code}: {body.get("description",exc)}'
    except Exception as exc: return False,30,str(exc)

def consume(db,source,posts):
    sid=source['id']; key=f'initial_baseline_complete:{sid}'
    if db.execute('SELECT value FROM app_config WHERE key=?',(key,)).fetchone() is None:
        for post in posts[:500]: db.execute('INSERT OR IGNORE INTO seen_posts(source,guid,title,link,author,category,pub_date,matched_keywords,first_seen) VALUES(?,?,?,?,?,?,?,?,?)',(sid,post['guid'],post['title'],post['link'],post['author'],post['category'],post['pub_date'],'',now()))
        db.execute('INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)',(key,'1')); db.commit(); return
    posts=posts[:500]
    timestamp=now()
    if not posts:
        db.execute('INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)',(f'last_success:{sid}',timestamp)); db.execute('DELETE FROM app_config WHERE key=?',(f'last_error:{sid}',)); db.commit(); return
    placeholders=','.join('?'*len(posts)); seen={r[0] for r in db.execute(f'SELECT guid FROM seen_posts WHERE source=? AND guid IN ({placeholders})',[sid]+[p['guid'] for p in posts])}
    for post in reversed([p for p in posts if p['guid'] not in seen]):
        post['source']=sid; matched=matches(db,post)
        if matched:
            markup=json.dumps({'inline_keyboard':[[{'text':'🔗 查看原帖','url':post['link']}]]},ensure_ascii=False)
            db.execute('INSERT OR IGNORE INTO notifications(source,guid,message,markup,next_attempt,created_at) VALUES(?,?,?,?,?,?)',(sid,post['guid'],message(post,matched,source),markup,timestamp,timestamp))
        db.execute('INSERT OR IGNORE INTO seen_posts(source,guid,title,link,author,category,pub_date,matched_keywords,first_seen) VALUES(?,?,?,?,?,?,?,?,?)',(sid,post['guid'],post['title'],post['link'],post['author'],post['category'],post['pub_date'],','.join(matched),timestamp))
    db.execute('INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)',(f'last_success:{sid}',timestamp)); db.execute('DELETE FROM app_config WHERE key=?',(f'last_error:{sid}',)); db.commit()

def deliver(db):
    db.execute("UPDATE notifications SET status='pending',claimed_at=NULL WHERE status='sending' AND claimed_at<datetime('now','-2 minutes')")
    rows=db.execute("SELECT id,message,markup,attempts FROM notifications WHERE status='pending' AND next_attempt<=? ORDER BY id LIMIT ?",(now(),MAX_DELIVERIES)).fetchall()
    if not rows:return
    ids=[r[0] for r in rows]; placeholders=','.join('?'*len(ids)); claimed=now(); db.execute(f"UPDATE notifications SET status='sending',attempts=attempts+1,claimed_at=? WHERE status='pending' AND id IN ({placeholders})",[claimed]+ids); db.commit()
    chat=os.getenv('NODESEEK_CHAT_ID','')
    for nid,text,markup,attempts in rows:
        ok,delay,error=send(chat,text,markup); timestamp=now()
        if ok:
            source,guid=db.execute('SELECT source,guid FROM notifications WHERE id=?',(nid,)).fetchone()
            title=db.execute('SELECT title FROM seen_posts WHERE source=? AND guid=?',(source,guid)).fetchone()[0]
            db.execute("UPDATE notifications SET status='sent',sent_at=?,last_error=NULL WHERE id=?",(timestamp,nid))
            db.execute('INSERT OR IGNORE INTO notification_history(source,guid,title,matched_keywords,pushed_at) VALUES(?,?,?,?,?)',(source,guid,title,'',timestamp))
        elif attempts+1>=MAX_ATTEMPTS: db.execute('UPDATE notifications SET status="failed",last_error=? WHERE id=?',(error[:500],nid))
        else: db.execute("UPDATE notifications SET status='pending',next_attempt=datetime('now',?),last_error=?,claimed_at=NULL WHERE id=?",(f'+{delay} seconds',error[:500],nid))
    db.commit()

def main():
    global running
    if not TOKEN or not os.getenv('NODESEEK_CHAT_ID'): raise SystemExit('missing NODESEEK_BOT_TOKEN or NODESEEK_CHAT_ID')
    lock=lock_instance(); db=connect(DB_PATH)
    signal.signal(signal.SIGTERM,lambda *_: globals().__setitem__('running',False)); signal.signal(signal.SIGINT,lambda *_: globals().__setitem__('running',False))
    state={s['id']:{'next':0,'future':None,'errors':0} for s in SOURCES}; last_maintenance=time.monotonic()
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        while running:
            monotonic=time.monotonic()
            for source in SOURCES:
                item=state[source['id']]
                if item['future'] and item['future'].done():
                    try: consume(db,source,item['future'].result()); item['errors']=0; item['next']=monotonic+source_interval(db,source['id'],source['interval'])
                    except Exception as exc: item['errors']+=1; item['next']=monotonic+min(300,max(3,2**item['errors'])); db.execute('INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)',(f'last_error:{source["id"]}',str(exc)[:300])); db.commit(); print(f'[rss] {source["id"]}: {exc}',flush=True)
                    item['future']=None
                if source_enabled(db,source['id']) and item['future'] is None and monotonic>=item['next']: item['future']=pool.submit(fetch,source)
            deliver(db)
            if monotonic-last_maintenance >= 3600:
                try:
                    db.execute("DELETE FROM seen_posts WHERE first_seen < datetime('now','-90 days')")
                    db.execute("DELETE FROM notification_history WHERE pushed_at < datetime('now','-365 days')")
                    db.execute("DELETE FROM notifications WHERE status IN ('sent','failed') AND created_at < datetime('now','-30 days')")
                    db.execute('PRAGMA wal_checkpoint(PASSIVE)'); db.commit()
                except sqlite3.OperationalError as exc:
                    print(f'[maintenance] skipped: {exc}',flush=True)
                last_maintenance=monotonic
            time.sleep(.3)
    db.close(); _=lock
if __name__=='__main__': main()
