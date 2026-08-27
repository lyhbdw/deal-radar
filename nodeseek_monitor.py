#!/usr/bin/env python3
"""Low-resource multi-source RSS monitor with independent schedules and durable delivery."""
import html, json, logging, os, random, re, signal, sqlite3, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from logging.handlers import RotatingFileHandler
from xml.etree import ElementTree as ET
from nodeseek_core import (SOURCES, bootstrap_required, enqueue_notification, get_category_config,
    mark_bootstrapped, mark_notification_retry, mark_notification_sent, claim_due_notifications,
    migrate_multi_source, notification_counts, source_interval, source_is_enabled, state_set, utcnow)

DB_PATH='/root/nodeseek_monitor.db'; PAUSE_FILE='/root/nodeseek_paused'; MAX_RETRIES=1
MAX_PUSH_PER_TICK=10; TELEGRAM_GAP=.5; HEALTH_WRITE_INTERVAL=15; CLEANUP_INTERVAL=3600
for line in open('/root/.nodeseek_env') if os.path.exists('/root/.nodeseek_env') else []:
    if '=' in line and not line.lstrip().startswith('#'):
        k,v=line.strip().split('=',1);os.environ.setdefault(k,v)
BOT_TOKEN=os.getenv('NODESEEK_BOT_TOKEN',''); CHAT_ID=os.getenv('NODESEEK_CHAT_ID','')
log=logging.getLogger('rss-radar');log.setLevel(logging.INFO)
fmt=logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
for h in (logging.StreamHandler(sys.stdout),RotatingFileHandler('/root/nodeseek_monitor.log',maxBytes=2_000_000,backupCount=3)):
    h.setFormatter(fmt);log.addHandler(h)
running=True
def stop(*_):
 global running;running=False;log.info('收到停止信号')
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
def wait(seconds):
 end=time.monotonic()+seconds
 while running and time.monotonic()<end: time.sleep(min(.2,end-time.monotonic()))
def db_open():
 db=sqlite3.connect(DB_PATH,timeout=15);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA busy_timeout=15000');migrate_multi_source(db);return db
def clean_text(s): return s.replace('\x1b','')
def strip_html(s): return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',clean_text(s)))).strip()
def parse(xml):
 root=ET.fromstring(xml);out=[]
 for i in root.findall('.//item'):
  title=(i.findtext('title') or '').strip();link=(i.findtext('link') or '').strip();guid=(i.findtext('guid') or link).strip()
  creator=i.find('{http://purl.org/dc/elements/1.1/}creator');author=creator.text.strip() if creator is not None and creator.text else ''
  out.append(dict(guid=guid,title=title,link=link,description=strip_html((i.findtext('description') or '').strip()),pub_date=(i.findtext('pubDate') or '').strip(),category=(i.findtext('category') or '').strip(),author=author))
 return out
def fetch(url):
 r=subprocess.run(['curl','-sL','--max-time','10','-H','User-Agent: Mozilla/5.0','-H','Accept: application/rss+xml, application/xml, text/xml, */*','-w','\n%{http_code}',url],capture_output=True,timeout=15)
 body,status=(r.stdout.rsplit(b'\n',1) if b'\n' in r.stdout else (b'',b'0'))
 if status.strip()!=b'200' or not body: raise RuntimeError(f'HTTP {status.decode(errors="replace").strip() or "0"}')
 return body.decode('utf-8',errors='replace')
def esc(x): return html.escape(str(x),quote=True)
def format_message(p,kws,src):
 try: when=parsedate_to_datetime(p['pub_date']).strftime('%m-%d %H:%M')
 except Exception: when=p['pub_date'][:16]
 desc=p['description'][:200]+('...' if len(p['description'])>200 else '')
 lines=[f"{src['emoji']} <b>{esc(src['name'])} 命中</b>",'━━━━━━━━━━━━',f"<b>{esc(p['title'])}</b>"]
 if desc:lines+=['',esc(desc)]
 meta=' · '.join(x for x in (p['category'],p['author'],when) if x)
 if meta:lines+=['',f'<code>{esc(meta)}</code>']
 lines += [f"🏷 {' '.join('#'+x.replace(' ','_') for x in kws)}",f'🔗 <a href="{esc(p["link"])}">打开原帖</a>']
 return '\n'.join(lines)
def send(text,markup):
 payload={'chat_id':CHAT_ID,'text':text,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':json.loads(markup)}
 data=json.dumps(payload).encode();req=urllib.request.Request(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',data=data,headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=12) as r: result=json.loads(r.read())
  if result.get('ok'):return True,0,''
  return False,int(result.get('parameters',{}).get('retry_after',30)),result.get('description','Telegram rejected')
 except urllib.error.HTTPError as e:
  try:return False,int(json.loads(e.read()).get('parameters',{}).get('retry_after',30)),f'HTTP {e.code}'
  except Exception:return False,30,f'HTTP {e.code}'
 except Exception as e:return False,30,str(e)
def deliver(db):
 for nid,source,guid,text,markup,_,attempts in claim_due_notifications(db,MAX_PUSH_PER_TICK):
  ok,delay,error=send(text,markup)
  if ok:
   mark_notification_sent(db,nid)
   db.execute('INSERT INTO push_history(source,guid,title,link,author,category,pub_date,matched_keywords,pushed_at) SELECT source,guid,title,link,author,category,pub_date,matched_keywords,? FROM seen_posts WHERE source=? AND guid=?',(utcnow(),source,guid))
   log.info('[%s] delivered %s',source,guid)
  else: mark_notification_retry(db,nid,error,delay if attempts<5 else 300);log.warning('[%s] delivery retry %s: %s',source,guid,error)
  wait(TELEGRAM_GAP)
def mark_seen(db,s,p,kws):
 db.execute('INSERT OR IGNORE INTO seen_posts(source,guid,title,link,author,category,pub_date,matched_keywords,first_seen) VALUES(?,?,?,?,?,?,?,?,?)',(s,p['guid'],p['title'],p['link'],p['author'],p['category'],p['pub_date'],','.join(kws),utcnow()))
def process(db,src,state):
 sid=src['id'];now=time.monotonic()
 if not source_is_enabled(db,sid):state[sid]['next']=now+5;return
 try:
  posts=parse(fetch(src['rss_url']))
  if bootstrap_required(db,sid):
   for p in posts:mark_seen(db,sid,p,[])
   mark_bootstrapped(db,sid);log.info('[%s] baseline %d',sid,len(posts));state[sid]['next']=now+source_interval(db,sid,src['interval']);db.commit();return
  disabled={c for c,on in get_category_config(db,sid).items() if not on};kws=[r[0] for r in db.execute('SELECT keyword FROM keywords')]
  new=0
  for p in reversed(posts):
   exists=db.execute('SELECT 1 FROM seen_posts WHERE source=? AND guid=?',(sid,p['guid'])).fetchone()
   if exists:continue
   hit=[] if p['category'] in disabled else [k for k in kws if k.casefold() in (p['title']+' '+p['description']).casefold()]
   if hit:
    text=format_message(p,hit,src);kb=json.dumps({'inline_keyboard':[[{'text':'🔗 查看原帖','url':p['link']},{'text':f"📂 {p['category']}",'callback_data':f'cat:{sid}:{p["category"]}'}]]},ensure_ascii=False)
    enqueue_notification(db,sid,p['guid'],text,kb);new+=1;state_set(db,f'last_match:{sid}',utcnow(),False);state_set(db,f'last_match_title:{sid}',p['title'][:120],False)
   mark_seen(db,sid,p,hit)
  state[sid].update(errors=0,next=now+source_interval(db,sid,src['interval']),last_ok=now)
  state_set(db,f'last_rss_success:{sid}',utcnow(),False);state_set(db,f'consecutive_errors:{sid}','0',False);state_set(db,f'last_rss_error:{sid}','',False);state_set(db,f'last_fetch_duration_ms:{sid}',str(int((time.monotonic()-now)*1000)),False);state_set(db,f'last_item_count:{sid}',str(len(posts)),False)
  if new:log.info('[%s] queued %d',sid,new)
 except Exception as e:
  st=state[sid];st['errors']+=1;delay=min(300,max(3,2**min(st['errors'],8)));st['next']=now+delay
  state_set(db,f'consecutive_errors:{sid}',str(st['errors']),False);state_set(db,f'last_rss_error:{sid}',str(e)[:300],False);log.warning('[%s] fetch failed (%d): %s; retry %ss',sid,st['errors'],e,delay)
  if st['errors']==10: enqueue_notification(db,sid,f'health-{int(time.time())}',f'⚠️ <b>{esc(src["name"])} 监控告警</b>\n连续抓取失败 10 次：{esc(str(e)[:200])}', '{}')
 db.commit()
def main():
 if not BOT_TOKEN or not CHAT_ID:sys.exit('NODESEEK_BOT_TOKEN / NODESEEK_CHAT_ID missing')
 db=db_open();now=time.monotonic();state={s['id']:{'next':now,'errors':0,'last_ok':now} for s in SOURCES};last_cleanup=now
 log.info('RSS radar started: %s',', '.join(s['id'] for s in SOURCES))
 while running:
  now=time.monotonic()
  if not os.path.exists(PAUSE_FILE):
   for s in SOURCES:
    force = db.execute('SELECT 1 FROM monitor_state WHERE key=?',(f'force_refresh:{s["id"]}',)).fetchone()
    if force:
     state[s['id']]['next']=now
     db.execute('DELETE FROM monitor_state WHERE key=?',(f'force_refresh:{s["id"]}',))
    if now>=state[s['id']]['next']:process(db,s,state)
   deliver(db)
  if now-last_cleanup>=CLEANUP_INTERVAL:
   db.execute("DELETE FROM seen_posts WHERE first_seen < datetime('now','-7 days')");db.execute("DELETE FROM push_history WHERE id NOT IN(SELECT id FROM push_history ORDER BY id DESC LIMIT 500)");db.execute("DELETE FROM notifications WHERE status='sent' AND sent_at < datetime('now','-30 days')");db.execute('PRAGMA wal_checkpoint(PASSIVE)');db.commit();last_cleanup=now
  due=min(v['next'] for v in state.values());wait(max(.1,min(1,due-time.monotonic())))
 db.commit();db.close();log.info('monitor stopped')
if __name__=='__main__':main()
