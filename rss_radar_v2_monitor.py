#!/usr/bin/env python3
"""RSS Radar v2 monitor. SHADOW=1 disables Telegram delivery."""
import html,json,logging,os,re,signal,subprocess,sys,time,urllib.error,urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from logging.handlers import RotatingFileHandler
from xml.etree import ElementTree as ET
from rss_radar_v2_core import *

DB_PATH=os.getenv('RSS_RADAR_DB','/root/nodeseek_monitor.db')
SHADOW=os.getenv('RSS_RADAR_SHADOW','0')=='1'
MAX_ATTEMPTS=10;MAX_DELIVERIES=10;running=True
for line in open('/root/.nodeseek_env') if os.path.exists('/root/.nodeseek_env') else []:
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.strip().split('=',1);os.environ.setdefault(k,v)
TOKEN=os.getenv('NODESEEK_BOT_TOKEN','');CHAT=os.getenv('NODESEEK_CHAT_ID','')
log=logging.getLogger('rss-radar-v2');log.setLevel(logging.INFO)
for h in (logging.StreamHandler(sys.stdout),RotatingFileHandler('/root/nodeseek_monitor_v2.log',maxBytes=2_000_000,backupCount=3)):
 h.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'));log.addHandler(h)
def stop(*_):
 global running;running=False
def clean(s):return s.replace('\x1b','')
def clean_text(s): return clean(s)
def text(s):return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',clean(s)))).strip()
def strip_html(s): return text(s)
def parse(raw):
 root=ET.fromstring(raw);out=[]
 for x in root.findall('.//item'):
  creator=x.find('{http://purl.org/dc/elements/1.1/}creator')
  out.append({'guid':(x.findtext('guid') or x.findtext('link') or '').strip(),'title':(x.findtext('title') or '').strip(),'link':(x.findtext('link') or '').strip(),'description':text(x.findtext('description') or ''),'pub_date':(x.findtext('pubDate') or '').strip(),'category':(x.findtext('category') or '').strip(),'author':creator.text.strip() if creator is not None and creator.text else ''})
 return [p for p in out if p['guid']]
def fetch(source):
 start=time.monotonic();r=subprocess.run(['curl','-sL','--max-time','10','-H','User-Agent: Mozilla/5.0','-w','\n%{http_code}',source['rss_url']],capture_output=True,timeout=15)
 if b'\n' not in r.stdout:raise RuntimeError('curl no status')
 body,status=r.stdout.rsplit(b'\n',1)
 if status.strip()!=b'200' or not body:raise RuntimeError('HTTP '+status.decode(errors='replace').strip())
 posts=parse(body.decode(errors='replace'))
 if not posts:raise RuntimeError('zero parsed items')
 return posts,int((time.monotonic()-start)*1000)
def message(p,matched,src):
 try:when=parsedate_to_datetime(p['pub_date']).strftime('%m-%d %H:%M')
 except Exception:when=p['pub_date'][:16]
 e=lambda x:html.escape(str(x),quote=True)
 lines=[f"{src['emoji']} <b>{e(src['name'])} 命中</b>",'━━━━━━━━━━━━',f"<b>{e(p['title'][:600])}</b>"]
 if p['description']:lines.extend(['',e(p['description'][:700])])
 meta=' · '.join(x for x in (p['category'],p['author'],when) if x)
 if meta:lines.extend(['',f'<code>{e(meta)}</code>'])
 lines.extend([f"🏷 {' '.join('#'+x.replace(' ','_') for x in matched)}",f'🔗 <a href="{e(p["link"])}">打开原帖</a>'])
 return clamp_telegram_text('\n'.join(lines))
def consume(db,src,posts,elapsed):
 sid=src['id'];discover_categories(db,sid,[p['category'] for p in posts])
 if not is_bootstrapped(db,sid):
  for p in posts:add_seen(db,sid,p,[])
  mark_bootstrapped(db,sid);log.info('[%s] baseline=%d',sid,len(posts))
 else:
  blocked=disabled_categories(db,sid);kws=[r[0] for r in db.execute('select keyword from keywords')];q=0
  for p in reversed(posts):
   if is_seen(db,sid,p['guid']):continue
   matched=[] if p['category'] in blocked else [k for k in kws if k.casefold() in (p['title']+' '+p['description']).casefold()]
   if matched:
    markup=json.dumps({'inline_keyboard':[[{'text':'🔗 查看原帖','url':p['link']},{'text':f"📂 {p['category']}",'callback_data':f'cat:{sid}:{p["category"]}'}]]},ensure_ascii=False)
    queue(db,sid,p,message(p,matched,src),markup,matched);q+=1
   add_seen(db,sid,p,matched)
  if q:log.info('[%s] queued=%d',sid,q)
 state_set(db,f'last_rss_success:{sid}',utcnow());state_set(db,f'last_fetch_duration_ms:{sid}',elapsed);state_set(db,f'last_item_count:{sid}',len(posts));state_set(db,f'consecutive_errors:{sid}',0);state_set(db,f'last_rss_error:{sid}','');db.commit()
def tg(text_,markup):
 req=urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/sendMessage',data=json.dumps({'chat_id':CHAT,'text':text_,'parse_mode':'HTML','reply_markup':json.loads(markup)}).encode(),headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=12) as r:res=json.loads(r.read())
  return bool(res.get('ok')),int(res.get('parameters',{}).get('retry_after',30)),res.get('description','')
 except urllib.error.HTTPError as e:return False,30,'HTTP '+str(e.code)
 except Exception as e:return False,30,str(e)
def deliver(db):
 if SHADOW:return
 for nid,sid,guid,body,markup,attempts in claim_due(db,MAX_DELIVERIES):
  ok,delay,error=tg(body,markup)
  if ok:delivered(db,nid)
  else:retry(db,nid,error,delay,failed=attempts>=MAX_ATTEMPTS)
def main():
 db=connect(DB_PATH);state={s['id']:{'next':time.monotonic(),'future':None,'errors':0} for s in SOURCES}
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
  log.info('v2 start shadow=%s',SHADOW)
  while running:
   now=time.monotonic()
   for src in SOURCES:
    sid=src['id'];st=state[sid]
    if st['future'] and st['future'].done():
     f=st['future'];st['future']=None
     try:posts,elapsed=f.result();consume(db,src,posts,elapsed);st['errors']=0;st['next']=time.monotonic()+interval_for(db,sid,src['interval'])
     except Exception as e:
      st['errors']+=1;delay=min(300,max(3,2**st['errors']));st['next']=time.monotonic()+delay;state_set(db,f'consecutive_errors:{sid}',st['errors']);state_set(db,f'last_rss_error:{sid}',str(e)[:300]);db.commit();log.warning('[%s] %s',sid,e)
    if is_source_enabled(db,sid) and st['future'] is None and now>=st['next']:st['future']=pool.submit(fetch,src)
   deliver(db);time.sleep(.1)
 db.commit();db.close()
if __name__=='__main__':main()
