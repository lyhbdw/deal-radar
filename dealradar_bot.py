#!/usr/bin/env python3
import fcntl, html, json, os, signal, sqlite3, threading, time
import http.client
from pathlib import Path
from singleuser import add_keywords, connect, enabled_sources, list_keywords
from rss_core import SOURCES

BASE = Path(__file__).resolve().parent

def load_env():
    path = Path(os.getenv('DEALRADAR_ENV', os.getenv('FEEDSENTINEL_ENV', BASE / '.env')))
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())
load_env()
DB = os.getenv('DEALRADAR_DB', os.getenv('FEEDSENTINEL_DB', str(BASE / 'dealradar.db')))
TOKEN = os.getenv('NODESEEK_BOT_TOKEN', '')
OWNER = os.getenv('NODESEEK_CHAT_ID', '')
OFFSET = Path(os.getenv('DEALRADAR_OFFSET', os.getenv('FEEDSENTINEL_OFFSET', str(BASE / 'telegram_offset.txt'))))
running = True
states = {}
state_ts = {}
http_local = threading.local()
http_connections = set()
http_connections_lock = threading.Lock()


def lock_instance():
    lock = open(BASE / 'bot.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lock

def close_http_connections():
    with http_connections_lock:
        connections = list(http_connections)
        http_connections.clear()
    for conn in connections:
        try: conn.close()
        except Exception: pass
    http_local.conn = None
    http_local.sock_timeout = None


def api(method, _socket_timeout=None, **payload):
    tg_timeout = payload.get('timeout', 0)
    sock_timeout = _socket_timeout if _socket_timeout is not None else max(15, tg_timeout + 15)
    conn = getattr(http_local, 'conn', None)
    try:
        if conn is None:
            conn = http_local.conn = http.client.HTTPSConnection('api.telegram.org', timeout=sock_timeout)
            http_local.sock_timeout = sock_timeout
            with http_connections_lock:
                http_connections.add(conn)
        elif conn.sock is not None:
            conn.sock.settimeout(sock_timeout)
            http_local.sock_timeout = sock_timeout
        body = json.dumps(payload).encode()
        conn.request('POST', f'/bot{TOKEN}/{method}', body=body, headers={'Content-Type':'application/json'})
        response = conn.getresponse(); data = json.loads(response.read())
        if response.status != 200:
            retry_after = data.get('parameters', {}).get('retry_after')
            err_msg = data.get('description', str(response.status))
            if retry_after:
                err_msg += f' (retry_after={retry_after})'
            raise RuntimeError(err_msg)
        return data
    except Exception as exc:
        try: conn.close()
        except Exception: pass
        with http_connections_lock:
            http_connections.discard(conn)
        http_local.conn = None
        http_local.sock_timeout = None
        is_normal_poll_timeout = method == 'getUpdates' and (
            'timed out' in str(exc).lower() or isinstance(exc, (TimeoutError, http.client.RemoteDisconnected))
        )
        if not is_normal_poll_timeout:
            print(f'[api] {method}: {exc}', flush=True)
        return {'ok': False, 'description': str(exc)}

def send(chat, text, markup=None):
    return api('sendMessage', chat_id=chat, text=text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)

def edit(chat, message_id, text, markup=None):
    return api('editMessageText', chat_id=chat, message_id=message_id, text=text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)

MENU = {
    'inline_keyboard': [
        [{'text': '➕ 添加词', 'callback_data': 'add'}, {'text': '🏷 关键词', 'callback_data': 'keywords'}],
        [{'text': '📡 站点', 'callback_data': 'sources'}, {'text': '📊 状态', 'callback_data': 'status'}],
        [{'text': '📜 历史', 'callback_data': 'history'}, {'text': '🔔 测试', 'callback_data': 'test'}]
    ]
}
BACK = {'inline_keyboard': [[{'text': '‹ 返回', 'callback_data': 'main'}]]}

def allowed(uid, chat=None):
    return bool(OWNER) and str(uid) == OWNER and (chat is None or str(chat) == OWNER)

def main_page(chat, msg=None):
    db = connect(DB)
    try:
        kw_rows = list_keywords(db)
        kw_count = len(kw_rows)
        enabled_src = enabled_sources(db)
        pushed_count = db.execute("SELECT count(*) FROM notification_history").fetchone()[0]
    except Exception:
        kw_count = 0
        enabled_src = set()
        pushed_count = 0
    finally:
        db.close()

    src_names = [s['name'] for s in SOURCES if s['id'] in enabled_src]
    src_str = '、'.join(src_names) if src_names else '无'

    text = (
        '🎯 <b>DealRadar · 捡漏雷达</b>\n\n'
        f'• 监控词：<b>{kw_count}</b> / 30\n'
        f'• 监控站点：{src_str} ({len(src_names)}/{len(SOURCES)})\n'
        f'• 累计推送：{pushed_count} 条'
    )
    dynamic_menu = {
        'inline_keyboard': [
            [{'text': '➕ 添加词', 'callback_data': 'add'}, {'text': f'🏷 关键词 ({kw_count})', 'callback_data': 'keywords'}],
            [{'text': '📡 站点', 'callback_data': 'sources'}, {'text': '📊 状态', 'callback_data': 'status'}],
            [{'text': '📜 历史', 'callback_data': 'history'}, {'text': '🔔 测试', 'callback_data': 'test'}]
        ]
    }
    return edit(chat, msg, text, dynamic_menu) if msg else send(chat, text, dynamic_menu)

def source_page(chat, msg):
    db = connect(DB)
    try:
        enabled = enabled_sources(db)
        rows = []
        for source in SOURCES:
            is_on = source['id'] in enabled
            mark = '🟢' if is_on else '⚪'
            rows.append([{'text': f"{mark} {source['name']}", 'callback_data': f'source:{source["id"]}'}])
        rows.append([{'text': '全部开启', 'callback_data': 'all_on'}, {'text': '全部关闭', 'callback_data': 'all_off'}])
        rows.append([{'text': '‹ 返回', 'callback_data': 'main'}])

        text = '📡 <b>监控站点</b>\n点击切换启用状态：'
        edit(chat, msg, text, {'inline_keyboard': rows})
    finally:
        db.close()

def keyword_page(chat, msg, notice=''):
    db = connect(DB)
    try:
        rows = list_keywords(db)
    finally:
        db.close()

    header = f'🏷 <b>关键词 ({len(rows)}/30)</b>\n'
    if notice:
        header += f'\n{notice}\n'

    if rows:
        kw_list = '\n'.join(f'{i+1}. <code>{html.escape(k)}</code>' for i, (_, k) in enumerate(rows))
        text = f'{header}\n{kw_list}'
    else:
        text = f'{header}\n<i>暂无关键词</i>'

    buttons = []
    del_row = []
    for kid, k in rows:
        label = f'🗑 {k[:10]}' if len(k) <= 10 else f'🗑 {k[:8]}…'
        del_row.append({'text': label, 'callback_data': f'del:{kid}'})
        if len(del_row) == 2:
            buttons.append(del_row)
            del_row = []
    if del_row:
        buttons.append(del_row)

    buttons.append([{'text': '➕ 添加词', 'callback_data': 'add'}, {'text': '‹ 返回', 'callback_data': 'main'}])
    edit(chat, msg, text, {'inline_keyboard': buttons})

def callback(c):
    uid = str(c['from']['id'])
    chat = c['message']['chat']['id']
    msg = c['message']['message_id']
    data = c.get('data', '')
    api('answerCallbackQuery', _socket_timeout=5, callback_query_id=c['id'])
    if not allowed(uid, chat):
        return
    db = connect(DB)
    try:
        if data == 'main':
            main_page(chat, msg)
        elif data == 'keywords':
            keyword_page(chat, msg)
        elif data == 'add':
            states[uid] = 'add'
            state_ts[uid] = time.time()
            prompt = (
                '➕ <b>添加关键词</b>\n\n'
                '发送要添加的词，多个词用空格隔开：\n'
                '<i>示例：搬瓦工 闪购 降价</i>'
            )
            edit(chat, msg, prompt, BACK)
        elif data.startswith('del:'):
            db.execute('DELETE FROM keywords WHERE id=?', (int(data[4:]),))
            db.commit()
            keyword_page(chat, msg, '🗑 <i>已删除</i>')
        elif data == 'sources':
            source_page(chat, msg)
        elif data.startswith('source:'):
            source = data[7:]
            row = db.execute('SELECT enabled FROM source_config WHERE source=?', (source,)).fetchone()
            enabled = bool(row and row[0])
            now = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
            db.execute(
                'INSERT INTO source_config(source,enabled,interval,updated_at) VALUES(?,?,?,?) '
                'ON CONFLICT(source) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',
                (source, int(not enabled), 1, now)
            )
            db.commit()
            source_page(chat, msg)
        elif data in ('all_on', 'all_off'):
            now = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
            value = int(data == 'all_on')
            for source in SOURCES:
                db.execute(
                    'INSERT INTO source_config(source,enabled,interval,updated_at) VALUES(?,?,?,?) '
                    'ON CONFLICT(source) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',
                    (source, value, 1, now)
                )
            db.commit()
            source_page(chat, msg)
        elif data == 'status':
            pending = db.execute("SELECT count(*) FROM notifications WHERE status='pending'").fetchone()[0]
            total_history = db.execute("SELECT count(*) FROM notification_history").fetchone()[0]
            lines = [
                '📊 <b>系统状态</b>',
                f'待发: {pending} · 历史: {total_history}\n'
            ]
            for source in SOURCES:
                sid = source['id']
                prefix = f'metric:{sid}:'
                metrics = {k[len(prefix):]: v for k, v in db.execute("SELECT key,value FROM app_config WHERE key LIKE ?", (prefix + '%',)).fetchall()}
                failures = int(metrics.get("consecutive_failures", "0") or 0)
                state = '🟢' if failures == 0 else f'🔴 失败x{failures}'
                dur = metrics.get("last_fetch_duration_seconds", "—")
                if dur != "—":
                    try:
                        dur = f"{float(dur):.2f}s"
                    except ValueError:
                        pass
                count = metrics.get("last_item_count", "—")
                succ = metrics.get("last_success_at", "—")
                if succ != "—" and len(succ) >= 16:
                    succ = succ[5:16]
                lines.append(f"• {source['emoji']} <b>{html.escape(source['name'])}</b> {state}")
                lines.append(f"  耗时 {dur} · 抓取 {count} 篇 · {succ}")
                if metrics.get('last_error'):
                    lines.append(f"  ⚠️ <i>{html.escape(metrics['last_error'][:50])}</i>")
            cur_time = time.strftime("%H:%M:%S", time.localtime())
            lines.append(f'\n<i>刷新时间：{cur_time}</i>')
            status_markup = {'inline_keyboard': [[{'text': '🔄 刷新', 'callback_data': 'status'}, {'text': '‹ 返回', 'callback_data': 'main'}]]}
            edit(chat, msg, '\n'.join(lines), status_markup)
        elif data == 'history':
            rows = db.execute('SELECT source,title,pushed_at,matched_keywords FROM notification_history ORDER BY id DESC LIMIT 10').fetchall()
            lines = ['📜 <b>最近推送</b>\n']
            if rows:
                src_map = {s['id']: s for s in SOURCES}
                for s_id, title, pushed_at, matched in rows:
                    s_info = src_map.get(s_id, {'name': s_id, 'emoji': '📌'})
                    t_str = pushed_at[5:16] if pushed_at and len(pushed_at) >= 16 else (pushed_at or '未知')
                    safe_title = html.escape(title or '无标题')[:55]
                    tag = f" #{html.escape(matched)}" if matched else ""
                    lines.append(f"{s_info['emoji']} <b>{html.escape(s_info['name'])}</b> · <code>{t_str}</code>{tag}")
                    lines.append(f"  <i>{safe_title}</i>")
            else:
                lines.append('<i>暂无记录</i>')
            edit(chat, msg, '\n'.join(lines), BACK)
        elif data == 'test':
            cur_stamp = time.strftime("%H:%M:%S", time.localtime())
            send(chat, f'🎯 <b>DealRadar · 推送测试正常</b> ({cur_stamp})', BACK)
    except Exception as exc:
        print(f'[callback] {exc}', flush=True)
        api('answerCallbackQuery', _socket_timeout=5, callback_query_id=c['id'], show_alert=True, text='⚠️ 操作失败')
    finally:
        db.close()

def message(m):
    uid = str(m['from']['id'])
    chat = m['chat']['id']
    text = m.get('text', '').strip()
    if not allowed(uid, chat):
        return
    if text.startswith('/start'):
        states.pop(uid, None)
        main_page(chat)
        return
    if states.get(uid) == 'add' and time.time() - state_ts.get(uid, 0) <= 300:
        db = None
        try:
            db = connect(DB)
            added, existing, rejected = add_keywords(db, text.split())
            kw_total = db.execute("SELECT count(*) FROM keywords WHERE enabled=1").fetchone()[0]
            lines = ['<b>关键词更新</b>']
            if added:
                lines.append('✅ 添加：' + '、'.join(f'<code>{html.escape(k)}</code>' for k in added))
            if existing:
                lines.append('♻️ 已存在：' + '、'.join(f'<code>{html.escape(k)}</code>' for k in existing))
            for k, reason in rejected:
                lines.append(f'❌ 忽略：<code>{html.escape(k)}</code> ({html.escape(reason)})')
            lines.append(f'\n当前关键词：<b>{kw_total}</b>/30')
            send(chat, '\n'.join(lines), MENU)
            return
        except Exception as exc:
            print(f'[message:add_keywords] {exc}', flush=True)
            send(chat, f'⚠️ <b>失败</b>: {html.escape(str(exc))}', MENU)
            return
        finally:
            states.pop(uid, None)
            state_ts.pop(uid, None)
            if db:
                try:
                    db.close()
                except Exception:
                    pass
    send(chat, '💡 发送 /start 打开 DealRadar 控制台', MENU)

class ShutdownRequested(BaseException):
    pass

def request_shutdown(*_):
    global running
    running = False
    close_http_connections()
    raise ShutdownRequested()

def main():
    global running
    if not TOKEN: raise SystemExit('missing NODESEEK_BOT_TOKEN')
    lock = lock_instance()
    offset = 0
    try: offset = int(OFFSET.read_text())
    except (OSError, ValueError): pass
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    api('setMyCommands', _socket_timeout=5, commands=[{'command': 'start', 'description': '打开控制台'}])
    import re
    while running:
        result = api('getUpdates', _socket_timeout=20, offset=offset, timeout=10)
        if not result.get('ok'):
            desc = result.get('description', '')
            delay = 3
            m = re.search(r'retry[ _]after[ =:]+(\d+)', desc, re.IGNORECASE)
            if m:
                delay = max(3, int(m.group(1)) + 1)
            time.sleep(delay)
            continue
        updates = result.get('result', [])
        for update in updates:
            offset = update['update_id'] + 1
            try:
                if 'callback_query' in update: callback(update['callback_query'])
                elif 'message' in update: message(update['message'])
            except Exception as exc: print(f'[update] {exc}', flush=True)
        if updates:
            tmp = OFFSET.with_suffix('.tmp')
            tmp.write_text(str(offset))
            os.replace(tmp, OFFSET)
    _ = lock

if __name__ == '__main__':
    try:
        main()
    except ShutdownRequested:
        pass
