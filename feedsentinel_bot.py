#!/usr/bin/env python3
import fcntl, html, json, os, queue, signal, sqlite3, threading, time
import http.client
from pathlib import Path
from singleuser import add_keywords, connect, enabled_sources, list_keywords
from rss_core import SOURCES

BASE = Path(__file__).resolve().parent

def load_env():
    path = Path(os.getenv('FEEDSENTINEL_ENV', BASE / '.env'))
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())
load_env()
DB = os.getenv('FEEDSENTINEL_DB', str(BASE / 'feedsentinel.db'))
TOKEN = os.getenv('NODESEEK_BOT_TOKEN', '')
OWNER = os.getenv('NODESEEK_CHAT_ID', '')
OFFSET = Path(os.getenv('FEEDSENTINEL_OFFSET', str(BASE / 'telegram_offset.txt')))
running = True
states = {}
state_ts = {}
http_local = threading.local()


def lock_instance():
    lock = open(BASE / 'bot.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lock

def api(method, timeout=15, **payload):
    conn = getattr(http_local, 'conn', None)
    try:
        if conn is None:
            conn = http_local.conn = http.client.HTTPSConnection('api.telegram.org', timeout=timeout)
        body = json.dumps(payload).encode()
        conn.request('POST', f'/bot{TOKEN}/{method}', body=body, headers={'Content-Type':'application/json'})
        response = conn.getresponse(); data = json.loads(response.read())
        if response.status != 200: raise RuntimeError(data.get('description', str(response.status)))
        return data
    except Exception as exc:
        try: conn.close()
        except Exception: pass
        http_local.conn = None
        print(f'[api] {method}: {exc}', flush=True)
        return {'ok': False, 'description': str(exc)}

def send(chat, text, markup=None):
    return api('sendMessage', chat_id=chat, text=text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)

def edit(chat, message_id, text, markup=None):
    return api('editMessageText', chat_id=chat, message_id=message_id, text=text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)

MENU={'inline_keyboard':[[{'text':'添加关键词','callback_data':'add'},{'text':'我的关键词','callback_data':'keywords'}],[{'text':'选择网站','callback_data':'sources'}],[{'text':'推送历史','callback_data':'history'},{'text':'系统状态','callback_data':'status'}],[{'text':'测试推送','callback_data':'test'}]]}
BACK={'inline_keyboard':[[{'text':'返回','callback_data':'main'}]]}

def allowed(uid, chat=None):
    return bool(OWNER) and str(uid) == OWNER and (chat is None or str(chat) == OWNER)

def main_page(chat, msg=None):
    text='🛡 <b>FeedSentinel</b>\n\n关键词命中新帖后自动推送。'
    return edit(chat,msg,text,MENU) if msg else send(chat,text,MENU)

def source_page(chat, msg):
    db=connect(DB); enabled=enabled_sources(db); rows=[]
    for source in SOURCES:
        mark='✅' if source['id'] in enabled else '⬜'
        rows.append([{'text':f'{mark} {source["name"]}','callback_data':f'source:{source["id"]}'}])
    rows.append([{'text':'全部开启','callback_data':'all_on'},{'text':'全部关闭','callback_data':'all_off'}]); rows.append(BACK['inline_keyboard'][0])
    edit(chat,msg,'🗂 <b>监控网站</b>',{'inline_keyboard':rows}); db.close()

def keyword_page(chat,msg, notice=''):
    db=connect(DB); rows=list_keywords(db); db.close()
    text='🏷 <b>关键词</b>\n'+(notice+'\n' if notice else '')
    text += '\n'.join(f'<code>{i+1:02d}</code> {html.escape(k)}' for i,(_,k) in enumerate(rows)) or '暂无关键词。'
    buttons=[[{'text':'🗑 '+k[:20],'callback_data':f'del:{kid}'}] for kid,k in rows]
    buttons += [[{'text':'添加','callback_data':'add'}], BACK['inline_keyboard'][0]]
    edit(chat,msg,text,{'inline_keyboard':buttons})

def callback(c):
    uid=str(c['from']['id']); chat=c['message']['chat']['id']; msg=c['message']['message_id']; data=c.get('data','')
    api('answerCallbackQuery', callback_query_id=c['id'])
    if not allowed(uid, chat): return
    db=connect(DB)
    try:
        if data=='main': main_page(chat,msg)
        elif data=='keywords': keyword_page(chat,msg)
        elif data=='add': states[uid]='add'; state_ts[uid]=time.time(); edit(chat,msg,'发送关键词，多个关键词用空格分隔。',BACK)
        elif data.startswith('del:'):
            db.execute('DELETE FROM keywords WHERE id=?', (int(data[4:]),)); db.commit(); keyword_page(chat,msg,'已删除')
        elif data=='sources': source_page(chat,msg)
        elif data.startswith('source:'):
            source=data[7:]; row=db.execute('SELECT enabled FROM source_config WHERE source=?',(source,)).fetchone(); enabled=bool(row and row[0])
            now=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime()); db.execute('INSERT INTO source_config(source,enabled,interval,updated_at) VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',(source,int(not enabled),1,now)); db.commit(); source_page(chat,msg)
        elif data in ('all_on','all_off'):
            now=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime()); value=int(data=='all_on')
            for source in SOURCES: db.execute('INSERT INTO source_config(source,enabled,interval,updated_at) VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',(source,value,1,now))
            db.commit(); source_page(chat,msg)
        elif data=='status':
            state=db.execute("SELECT key,value FROM app_config WHERE key LIKE 'last_%' ORDER BY key").fetchall(); pending=db.execute("SELECT count(*) FROM notifications WHERE status='pending'").fetchone()[0]
            edit(chat,msg,'📊 <b>状态</b>\n\n待发送：%d\n%s' % (pending,'\n'.join(f'{html.escape(k)}: {html.escape(v)}' for k,v in state) or '暂无运行记录。'),BACK)
        elif data=='history':
            rows=db.execute('SELECT source,title,pushed_at FROM notification_history ORDER BY id DESC LIMIT 10').fetchall(); edit(chat,msg,'📜 <b>最近推送</b>\n\n'+('\n'.join(f'{html.escape(s)} · {html.escape(t or "")[:80]}' for s,t,_ in rows) or '暂无记录。'),BACK)
        elif data=='test': send(chat,'✅ FeedSentinel 测试推送正常。',BACK)
    except Exception as exc: print(f'[callback] {exc}',flush=True)
    finally: db.close()

def message(m):
    uid=str(m['from']['id']); chat=m['chat']['id']; text=m.get('text','').strip()
    if not allowed(uid, chat): return
    if text.startswith('/start'): states.pop(uid,None); main_page(chat); return
    if states.get(uid)=='add' and time.time()-state_ts.get(uid,0) <= 300:
        db=connect(DB); added,existing,rejected=add_keywords(db,text.split()); db.close(); states.pop(uid,None); state_ts.pop(uid,None)
        lines=['✅ 已添加：'+('、'.join(map(html.escape,added)) or '无'),'♻️ 已存在：'+('、'.join(map(html.escape,existing)) or '无')]
        lines += [f'❌ {html.escape(k)}（{html.escape(reason)}）' for k,reason in rejected]; send(chat,'\n'.join(lines),MENU); return
    send(chat,'发送 /start 打开控制台。',MENU)

def main():
    global running
    if not TOKEN: raise SystemExit('missing NODESEEK_BOT_TOKEN')
    lock=lock_instance(); offset=0
    try: offset=int(OFFSET.read_text())
    except (OSError,ValueError): pass
    signal.signal(signal.SIGTERM,lambda *_: signal.raise_signal(signal.SIGINT)); signal.signal(signal.SIGINT,lambda *_: globals().__setitem__('running',False))
    api('setMyCommands',commands=[{'command':'start','description':'打开控制台'}])
    while running:
        result=api('getUpdates',timeout=35,offset=offset)
        if not result.get('ok'): time.sleep(3); continue
        for update in result.get('result',[]):
            offset=update['update_id']+1
            try:
                if 'callback_query' in update: callback(update['callback_query'])
                elif 'message' in update: message(update['message'])
            except Exception as exc: print(f'[update] {exc}',flush=True)
        tmp=OFFSET.with_suffix('.tmp'); tmp.write_text(str(offset)); os.replace(tmp,OFFSET)
    _=lock
if __name__=='__main__': main()
