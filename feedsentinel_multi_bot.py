#!/usr/bin/env python3
"""Public multi-user FeedSentinel Telegram bot — optimized."""
import json,os,queue,signal,sqlite3,sys,threading,time,urllib.request,http.client
from html import escape
from feedsentinel_multiuser import *
DB='/root/nodeseek_monitor.db';OFFSET='/root/feedsentinel_offset.txt';TOKEN='';running=True;states={};_states_ts={}
for line in open('/root/.nodeseek_env') if os.path.exists('/root/.nodeseek_env') else []:
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.strip().split('=',1);os.environ.setdefault(k,v)
TOKEN=os.getenv('NODESEEK_BOT_TOKEN','');API=f'https://api.telegram.org/bot{TOKEN}'
STATE_TTL=300  # 5 min timeout for add-keyword state

# ── DB: persistent connection ──
_conn=None
def db():
 global _conn
 if _conn is not None: return _conn
 _conn=sqlite3.connect(DB,timeout=15)
 _conn.execute('pragma busy_timeout=15000')
 _conn.execute('pragma journal_mode=WAL')
 ensure_multiuser_schema(_conn)
 return _conn

# ── API: persistent keep-alive connections (per-thread) ──
_tls=threading.local()
def _http(timeout):
 c=getattr(_tls,'conn',None)
 if c is None:c=_tls.conn=http.client.HTTPSConnection('api.telegram.org',timeout=timeout)
 return c
def _reset_http():
 c=getattr(_tls,'conn',None)
 if c is not None:
  try:c.close()
  except Exception:pass
 _tls.conn=None
def _post(method,payload,timeout=10):
 req={'method':'POST','path':f'/bot{TOKEN}/{method}','headers':{'Content-Type':'application/json'},'body':json.dumps(payload).encode()}
 for attempt in (1,2):
  try:
   c=_http(timeout);c.sock.settimeout(timeout) if c.sock else None
   c.request(req['method'],req['path'],body=req['body'],headers=req['headers'])
   r=c.getresponse();data=json.loads(r.read())
   if r.status!=200:raise RuntimeError(f'HTTP {r.status}: {data.get("description","")}')
   return data
  except Exception as e:
   _reset_http()
   if attempt==2 or not running:  # don't retry long-polls during shutdown
    print(f'[API] {method} failed after retry: {e}',flush=True)
    raise
def api(method,timeout=30,_tg_timeout=None,**kw):
 t0=time.time()
 try:
  if _tg_timeout is not None: kw['timeout']=_tg_timeout
  return _post(method,kw,timeout)
 except Exception as e:
  dt=time.time()-t0
  if dt>1: print(f'[API] {method} slow: {dt:.1f}s ({e})',flush=True)
  return {'ok':False,'description':str(e)}
def api_fire(method,**kw):
 _q.put((method,kw))
def _worker():
 while running:
  try:method,kw=_q.get(timeout=1)
  except queue.Empty:continue
  api(method,timeout=5,**kw)
_q=queue.Queue()
threading.Thread(target=_worker,daemon=True).start()
def send(chat,text,kb=None):return api('sendMessage',chat_id=chat,text=text,parse_mode='HTML',reply_markup=kb,disable_web_page_preview=True)
def edit(chat,msg,text,kb=None):return api('editMessageText',chat_id=chat,message_id=msg,text=text,parse_mode='HTML',reply_markup=kb,disable_web_page_preview=True)

# ── UI constants ──
_MENU={'inline_keyboard':[[{'text':'➕ 添加关键词','callback_data':'add'},{'text':'📋 我的关键词','callback_data':'keywords'}],[{'text':'🗂 选择网站','callback_data':'sources'},{'text':'📂 分类过滤','callback_data':'categories'}],[{'text':'📜 我的历史','callback_data':'history'},{'text':'📊 我的状态','callback_data':'status'}],[{'text':'❓ 帮助','callback_data':'help'}]]}
_BACK={'inline_keyboard':[[{'text':'🔙 返回','callback_data':'main'}]]}

def main_page(chat,msg=None):
 text='🛡 <b>FeedSentinel · 订阅哨兵</b>\n━━━━━━━━━━━━\n\n配置你的关键词和监控网站。每位用户的数据彼此独立。'
 return edit(chat,msg,text,_MENU) if msg else send(chat,text,_MENU)
def source_page(chat,uid,msg):
 d=db();on=enabled_sources_for_user(d,uid);rows=[]
 for s in SOURCES:rows.append([{'text':('✅' if s in on else '⬜')+' '+s,'callback_data':'src:'+s}])
 rows+=[[{'text':'✅ 全部开启','callback_data':'srcall'},{'text':'⏹ 全部关闭','callback_data':'srcoff'}],_BACK['inline_keyboard'][0]]
 edit(chat,msg,'🗂 <b>选择监控网站</b>\n━━━━━━━━━━━━\n\n点名称开关。你可以只开一个，也可以任意组合多个。',{'inline_keyboard':rows})
def keyword_page(chat,uid,msg,notice=''):
 d=db();rows=d.execute('select id,keyword from user_keywords where user_id=? order by keyword',(uid,)).fetchall()
 text='🏷 <b>我的关键词</b>\n━━━━━━━━━━━━\n'+(notice+'\n' if notice else '')
 text+='\n'.join(f'<code>{i+1:02d}</code>　{escape(k)}' for i,(_,k) in enumerate(rows)) if rows else '\n还没有关键词。'
 kb={'inline_keyboard':[[{'text':'🗑 '+k[:20],'callback_data':'del:'+str(kid)}] for kid,k in rows]+[[{'text':'➕ 添加','callback_data':'add'}],_BACK['inline_keyboard'][0]]}
 edit(chat,msg,text,kb)
def callback(c):
 uid=c['from']['id'];chat=c['message']['chat']['id'];msg=c['message']['message_id'];data=c['data']
 api_fire('answerCallbackQuery',callback_query_id=c['id'])
 d=db()
 if data=='main':main_page(chat,msg)
 elif data=='sources':source_page(chat,uid,msg)
 elif data.startswith('src:'):
  on=enabled_sources_for_user(d,uid);s=data[4:];set_user_sources(d,uid,on^{s});source_page(chat,uid,msg)
 elif data=='srcall':set_user_sources(d,uid,set(SOURCES));source_page(chat,uid,msg)
 elif data=='srcoff':set_user_sources(d,uid,set());source_page(chat,uid,msg)
 elif data=='add':states[uid]='add';_states_ts[uid]=time.time();edit(chat,msg,'✏️ <b>添加关键词</b>\n━━━━━━━━━━━━\n\n直接发送关键词；多个用空格隔开。\n正在等待输入…',_BACK)
 elif data=='keywords':keyword_page(chat,uid,msg)
 elif data.startswith('del:'):
  try:kid=int(data[4:])
  except ValueError:
   edit(chat,msg,'❓ <b>帮助</b>\n\n添加关键词后，选择至少一个网站，命中新帖会只推送给你。',_BACK)
  else:
   d.execute('delete from user_keywords where user_id=? and id=?',(uid,kid));d.commit();keyword_page(chat,uid,msg,'🗑 已删除')
 elif data=='status':
  on=enabled_sources_for_user(d,uid);n=d.execute('select count(*) from user_keywords where user_id=?',(uid,)).fetchone()[0]
  edit(chat,msg,f'📊 <b>我的状态</b>\n━━━━━━━━━━━━\n\n关键词：<b>{n}</b> 个\n开启网站：<b>{"、".join(sorted(on)) or "无"}</b>\n\n提醒仅发送给你。',_BACK)
 elif data=='history':
  rows=d.execute('select source,guid,pushed_at from user_notification_history where user_id=? order by id desc limit 10',(uid,)).fetchall()
  text='📜 <b>我的推送历史</b>\n━━━━━━━━━━━━\n\n'+('\n'.join(f'{escape(s)} · {escape(t[:16].replace("T"," "))}' for s,g,t in rows) if rows else '暂无推送。')
  edit(chat,msg,text,_BACK)
 elif data=='categories':edit(chat,msg,'📂 分类过滤\n\n多用户分类界面正在整理中；当前可先使用网站和关键词筛选。',_BACK)
 else:edit(chat,msg,'❓ <b>帮助</b>\n\n添加关键词后，选择至少一个网站，命中新帖会只推送给你。',_BACK)
def message(m):
 uid=m['from']['id'];chat=m['chat']['id'];text=m.get('text','').strip();d=db();ensure_user(d,uid,chat,m['from'].get('username',''),m['from'].get('first_name',''))
 if text.startswith('/start'):states.pop(uid,None);main_page(chat);return
 # Expire stale add-state
 if uid in _states_ts and time.time()-_states_ts[uid]>STATE_TTL:
  states.pop(uid,None);_states_ts.pop(uid,None)
 if states.get(uid)=='add' and text:
  a,e,r=add_user_keywords(d,uid,text.split());states.pop(uid,None);_states_ts.pop(uid,None)
  lines=['✅ 已添加：'+('、'.join(escape(x) for x in a) or '无'),'♻️ 已有：'+('、'.join(escape(x) for x in e) or '无')]
  lines+= [f'❌ {escape(k) or "(空)"}（{escape(why)}）' for k,why in r]  # show rejection reasons, previously dropped
  send(chat,'\n'.join(lines),_MENU);return
 send(chat,'发送 /start 打开 FeedSentinel 控制台。',_MENU)
def stop(*_):
 global running;running=False
 try:_reset_http()  # unblock the in-flight long-poll so shutdown is immediate
 except Exception:pass
def _save_offset(off):
 """Atomic offset write: write to temp then rename."""
 tmp=OFFSET+'.tmp'
 with open(tmp,'w') as f:f.write(str(off))
 os.rename(tmp,OFFSET)
def main():
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop);offset=0
 try:offset=int(open(OFFSET).read())
 except Exception:pass
 api('setMyCommands',commands=[{'command':'start','description':'打开 FeedSentinel 控制台'}])
 api('setChatMenuButton',menu_button={'type':'commands'})
 while running:
  res=api('getUpdates',timeout=30,offset=offset,_tg_timeout=15)
  if not res.get('ok'):time.sleep(3);continue
  for u in res.get('result',[]):
   offset=u['update_id']+1
   try:
    if 'callback_query' in u:callback(u['callback_query'])
    if 'message' in u:message(u['message'])
   except sqlite3.OperationalError as e:
    print(f'[WARN] DB error: {e}',flush=True);time.sleep(1)
   except Exception as e:
    print(f'[ERROR] {e}',flush=True)
  _save_offset(offset)
 if _conn:_conn.close()
if __name__=='__main__':main()
