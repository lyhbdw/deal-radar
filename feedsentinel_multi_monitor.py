#!/usr/bin/env python3
"""FeedSentinel multi-user RSS monitor — optimized."""
import json, os, signal, sqlite3, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from feedsentinel_multiuser import ensure_multiuser_schema
from rss_radar_v2_core import SOURCES, connect, is_source_enabled, interval_for, state_get, state_set, utcnow
from rss_radar_v2_monitor import fetch, message

DB_PATH='/root/nodeseek_monitor.db'; running=True; MAX_DELIVERIES=20; MAX_ATTEMPTS=10
for line in open('/root/.nodeseek_env') if os.path.exists('/root/.nodeseek_env') else []:
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.strip().split('=',1);os.environ.setdefault(k,v)
TOKEN=os.getenv('NODESEEK_BOT_TOKEN','')
def stop(*_):
 global running;running=False

def consume(db,src,posts):
 sid=src['id']
 key=f'initial_baseline_complete:{sid}'
 if state_get(db,key) != '1':
  for p in posts: db.execute("INSERT OR IGNORE INTO seen_posts(source,guid,title,link,author,category,pub_date,matched_keywords,first_seen) VALUES(?,?,?,?,?,?,?,?,?)",(sid,p['guid'],p['title'],p['link'],p['author'],p['category'],p['pub_date'],'',utcnow()))
  state_set(db,key,'1');db.commit();return
 if not posts:
  state_set(db,f'last_rss_success:{sid}',utcnow());state_set(db,f'consecutive_errors:{sid}',0);db.commit();return
 # Batch: filter seen posts in one query (cap at 500 to avoid SQLite variable limit)
 check_posts=posts[:500]
 placeholders=','.join('?'*len(check_posts))
 seen_ids={r[0] for r in db.execute(f"SELECT guid FROM seen_posts WHERE source=? AND guid IN ({placeholders})",[sid]+[p['guid'] for p in check_posts]).fetchall()}
 new_posts=[p for p in reversed(check_posts) if p['guid'] not in seen_ids]
 if not new_posts:
  state_set(db,f'last_rss_success:{sid}',utcnow());state_set(db,f'consecutive_errors:{sid}',0);db.commit();return
 # Batch: load all active user keywords+sources once
 all_kws=db.execute("""
   SELECT k.user_id,k.keyword FROM user_keywords k
   JOIN user_sources s ON s.user_id=k.user_id AND s.source=? AND s.enabled=1
   JOIN users u ON u.user_id=k.user_id AND u.active=1
 """,(sid,)).fetchall()
 now=utcnow()
 for post in new_posts:
  post['source']=sid
  text_cf=(post['title']+' '+post['description']).casefold()
  matched_users={}
  for user_id,keyword in all_kws:
   if keyword.casefold() in text_cf: matched_users.setdefault(user_id,[]).append(keyword)
  for user_id,keywords in matched_users.items():
   markup=json.dumps({'inline_keyboard':[[{'text':'🔗 查看原帖','url':post['link']}]]},ensure_ascii=False)
   db.execute("INSERT OR IGNORE INTO user_notifications(user_id,source,guid,message,markup,status,attempts,next_attempt,created_at) VALUES(?,?,?,?,?,'pending',0,?,?)",(user_id,sid,post['guid'],message(post,keywords,src),markup,now,now))
  db.execute("INSERT OR IGNORE INTO seen_posts(source,guid,title,link,author,category,pub_date,matched_keywords,first_seen) VALUES(?,?,?,?,?,?,?,?,?)",(sid,post['guid'],post['title'],post['link'],post['author'],post['category'],post['pub_date'],'',now))
 state_set(db,f'last_rss_success:{sid}',now);state_set(db,f'consecutive_errors:{sid}',0)
 db.commit()

def send(chat_id,text,markup):
 req=urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/sendMessage',data=json.dumps({'chat_id':chat_id,'text':text,'parse_mode':'HTML','reply_markup':json.loads(markup)}).encode(),headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=12) as r:res=json.loads(r.read())
  return bool(res.get('ok')),int(res.get('parameters',{}).get('retry_after',30)),res.get('description','')
 except urllib.error.HTTPError as e:
  # 429 body carries retry_after — must read it, not just the code
  try:body=json.loads(e.read())
  except Exception:body={}
  return False,int(body.get('parameters',{}).get('retry_after',30)),f'HTTP {e.code}: {body.get("description",e)}'
 except Exception as e:return False,30,str(e)

def deliver(db):
 # Always check stale sending (independent of pending count)
 stale=db.execute("SELECT 1 FROM user_notifications WHERE status='sending' AND claimed_at<datetime('now','-2 minutes') LIMIT 1").fetchone()
 if stale:
  db.execute("UPDATE user_notifications SET status='pending',claimed_at=NULL WHERE status='sending' AND claimed_at<datetime('now','-2 minutes')")
  db.commit()
 rows=db.execute("SELECT n.id,n.user_id,u.chat_id,n.message,n.markup,n.attempts FROM user_notifications n JOIN users u ON u.user_id=n.user_id WHERE n.status='pending' AND n.next_attempt<=? AND u.active=1 ORDER BY n.id LIMIT ?",(utcnow(),MAX_DELIVERIES)).fetchall()
 if not rows: return
 # Claim all at once using parameterized query
 placeholders=','.join('?'*len(rows))
 db.execute(f"UPDATE user_notifications SET status='sending',attempts=attempts+1,claimed_at=? WHERE id IN ({placeholders})",[utcnow()]+[r[0] for r in rows])
 db.commit()
 for nid,uid,chat,msg,markup,attempts in rows:
  ok,delay,error=send(chat,msg,markup)
  now=utcnow()
  if ok:
   row=db.execute("SELECT source,guid FROM user_notifications WHERE id=?",(nid,)).fetchone()
   db.execute("UPDATE user_notifications SET status='sent',sent_at=?,last_error=NULL WHERE id=?",(now,nid))
   db.execute("INSERT OR IGNORE INTO user_notification_history(user_id,source,guid,pushed_at) VALUES(?,?,?,?)",(uid,row[0],row[1],now))
  elif attempts+1>=MAX_ATTEMPTS:
   db.execute("UPDATE user_notifications SET status='failed',last_error=? WHERE id=?",(error[:500],nid))
  else:
   db.execute("UPDATE user_notifications SET status='pending',next_attempt=datetime('now',?),last_error=?,claimed_at=NULL WHERE id=?",(f'+{delay} seconds',error[:500],nid))
 db.commit()  # one commit per batch, not per row

def _log_status(db,state):
 """One status line per minute for observability."""
 try:
  pending=db.execute("select count(*) from user_notifications where status='pending'").fetchone()[0]
  stuck=db.execute("select count(*) from user_notifications where status='sending' and claimed_at<datetime('now','-2 minutes')").fetchone()[0]
  errs={sid:st['errors'] for sid,st in state.items() if st['errors']}
  print(f'[status] pending={pending} stuck={stuck} errors={errs or "-"}',flush=True)
 except Exception as e:print(f'[status] failed: {e}',flush=True)

def _prune(db):
 """Hourly: keep seen_posts at 30d, finished notifications at 7d, history at 90d."""
 try:
  a=db.execute("DELETE FROM seen_posts WHERE first_seen<datetime('now','-30 days')").rowcount
  b=db.execute("DELETE FROM user_notifications WHERE status IN ('sent','failed') AND created_at<datetime('now','-7 days')").rowcount
  c=db.execute("DELETE FROM user_notification_history WHERE pushed_at<datetime('now','-90 days')").rowcount
  db.commit()
  if a or b or c:print(f'[prune] seen_posts -{a}, notifications -{b}, history -{c}',flush=True)
 except Exception as e:print(f'[prune] failed: {e}',flush=True)

def main():
 if not TOKEN:raise SystemExit('missing bot token')
 db=connect(DB_PATH);ensure_multiuser_schema(db)
 state={s['id']:{'next':time.monotonic(),'future':None,'errors':0} for s in SOURCES}
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
  while running:
   now=time.monotonic()
   for src in SOURCES:
    sid=src['id'];st=state[sid]
    if st['future'] and st['future'].done():
     try:
      posts=st['future'].result()
      consume(db,src,posts)
      st['errors']=0;st['next']=time.monotonic()+interval_for(db,sid,src['interval'])
     except Exception as e:
      st['errors']+=1;st['next']=time.monotonic()+min(300,max(3,2**st['errors']))
      state_set(db,f'consecutive_errors:{sid}',st['errors']);state_set(db,f'last_rss_error:{sid}',str(e)[:300]);db.commit()
     st['future']=None
    if is_source_enabled(db,sid) and st['future'] is None and now>=st['next']:
     st['future']=pool.submit(fetch,src)
   deliver(db)
   if int(now)%60==0:
    db.execute("PRAGMA wal_checkpoint(PASSIVE)")
    _log_status(db,state)
   if int(now)%3600==0:
    _prune(db)
   time.sleep(.3)
 db.close()
if __name__=='__main__':main()
