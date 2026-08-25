#!/usr/bin/env python3
"""
NodeSeek 关键词监控 - 交互式 Telegram Bot
全程按钮操作。
"""

import json
import os
import sys
import time
import sqlite3
import subprocess
import urllib.request
import socket
import signal
from contextlib import contextmanager
from html import escape as html_escape
from datetime import datetime, timezone
from nodeseek_core import (
    ensure_keywords_schema, ensure_state_table, get_category_config,
    set_category_enabled as set_category_config_enabled,
    state_get, validate_keywords,
)

# ============ 配置 ============

_env_file = "/root/.nodeseek_env"
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for raw_line in f:
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN = os.environ.get("NODESEEK_BOT_TOKEN", "")
_chat_id_raw = os.environ.get("NODESEEK_CHAT_ID", "")
CHAT_ID = int(_chat_id_raw) if _chat_id_raw.isdigit() else 0
DB_PATH = "/root/nodeseek_monitor.db"
PAUSE_FILE = "/root/nodeseek_paused"
OFFSET_FILE = "/root/nodeseek_offset.txt"
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
TELEGRAM_HOST = "api.telegram.org"
MONITOR_SERVICE = "nodeseek-monitor"

ALLOWED_USERS = {CHAT_ID}

ALL_CATEGORIES = ["trade", "daily", "review", "tech", "info", "dev", "carpool", "expose", "photo-share"]

user_states = {}

# ============ Telegram API ============

@contextmanager
def telegram_ipv4_only():
    """Bot 进程内 Telegram 优先 IPv4，绕过偶发的 IPv6 长轮询卡顿。"""
    original = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host == TELEGRAM_HOST:
            return original(host, port, socket.AF_INET, type, proto, flags)
        return original(host, port, family, type, proto, flags)

    socket.getaddrinfo = ipv4_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original


def tg_api(method, request_timeout=60, **kwargs):
    url = f"{API_BASE}/{method}"
    data = json.dumps(kwargs).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with telegram_ipv4_only():
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[API ERROR] {method}: {e}", file=sys.stderr)
        return {"ok": False, "description": str(e)}


def send_message(chat_id, text, reply_markup=None):
    # 交互优先：不要在每次回复前同步等待“正在输入”请求。
    return tg_api("sendMessage", request_timeout=10, chat_id=chat_id, text=text,
                  parse_mode="HTML", reply_markup=reply_markup,
                  disable_web_page_preview=True)


def answer_callback(callback_query_id, text=None):
    kwargs = {"callback_query_id": callback_query_id}
    if text:
        kwargs["text"] = text
    return tg_api("answerCallbackQuery", **kwargs)


def edit_message(chat_id, message_id, text, reply_markup=None):
    result = tg_api("editMessageText", request_timeout=10, chat_id=chat_id, message_id=message_id,
                    text=text, parse_mode="HTML", reply_markup=reply_markup,
                    disable_web_page_preview=True)
    if not result.get("ok") and "not modified" not in result.get("description", "").lower():
        print(f"[WARN] editMessage failed: {result.get('description')}", file=sys.stderr)
    return result

# ============ Offset 持久化 ============

def load_offset():
    try:
        with open(OFFSET_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except Exception:
        pass

# ============ 暂停控制 ============

def is_paused():
    return os.path.exists(PAUSE_FILE)


def pause_monitor():
    with open(PAUSE_FILE, "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())


def resume_monitor():
    if os.path.exists(PAUSE_FILE):
        os.remove(PAUSE_FILE)

# ============ 分类控制 ============

def get_categories_config():
    db = get_db()
    config = get_category_config(db)
    db.close()
    return config or None


def set_category_enabled(cat, enabled):
    db = get_db()
    set_category_config_enabled(db, cat, enabled)
    db.close()


def get_enabled_categories():
    config = get_categories_config()
    if config is None:
        return None
    return {k for k, v in config.items() if v}

# ============ 进程状态 ============

def escape_html(text):
    return html_escape("" if text is None else str(text), quote=True)

def is_monitor_running():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", MONITOR_SERVICE],
            capture_output=True, text=True, timeout=5)
        return result.stdout.strip() == "active"
    except Exception:
        return False

# ============ 数据库 ============

_schema_initialized = False

def init_db():
    """Run schema creation once at startup; subsequent get_db() calls skip DDL."""
    global _schema_initialized
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS seen_posts (
        guid TEXT PRIMARY KEY, title TEXT, link TEXT, author TEXT,
        category TEXT, pub_date TEXT, matched_keywords TEXT, first_seen TEXT)""")
    ensure_keywords_schema(db)
    db.execute("""CREATE TABLE IF NOT EXISTS push_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guid TEXT, title TEXT, link TEXT, author TEXT,
        category TEXT, pub_date TEXT, matched_keywords TEXT, pushed_at TEXT)""")
    ensure_state_table(db)
    db.execute("CREATE INDEX IF NOT EXISTS idx_first_seen ON seen_posts(first_seen)")
    db.commit()
    db.close()
    _schema_initialized = True


def get_db():
    db = sqlite3.connect(DB_PATH, timeout=10)
    if not _schema_initialized:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS seen_posts (
            guid TEXT PRIMARY KEY, title TEXT, link TEXT, author TEXT,
            category TEXT, pub_date TEXT, matched_keywords TEXT, first_seen TEXT)""")
        ensure_keywords_schema(db)
        db.execute("""CREATE TABLE IF NOT EXISTS push_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT, title TEXT, link TEXT, author TEXT,
            category TEXT, pub_date TEXT, matched_keywords TEXT, pushed_at TEXT)""")
        ensure_state_table(db)
        db.execute("CREATE INDEX IF NOT EXISTS idx_first_seen ON seen_posts(first_seen)")
        db.commit()
    return db


def get_keywords():
    db = get_db()
    rows = db.execute("SELECT id, keyword FROM keywords ORDER BY keyword").fetchall()
    db.close()
    return rows


def get_keyword_texts():
    return [keyword for _, keyword in get_keywords()]


def add_keywords(keywords):
    valid, rejected = validate_keywords(keywords)
    db = get_db()
    current_count = db.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    remaining = max(0, 100 - current_count)
    valid, overflow = valid[:remaining], valid[remaining:]
    rejected.extend((kw, "总数最多 100 个") for kw in overflow)
    now = datetime.now(timezone.utc).isoformat()
    added = []
    skipped = []
    for kw in valid:
        existing = db.execute("SELECT 1 FROM keywords WHERE lower(keyword) = lower(?)", (kw,)).fetchone()
        if existing:
            skipped.append(kw)
        else:
            db.execute("INSERT INTO keywords (keyword, added_at) VALUES (?, ?)", (kw, now))
            added.append(kw)
    db.commit()
    db.close()
    return added, skipped, rejected


def del_keywords(keywords):
    db = get_db()
    deleted, missing = [], []
    for kw in keywords:
        cur = db.execute("DELETE FROM keywords WHERE lower(keyword) = lower(?)", (kw,))
        (deleted if cur.rowcount else missing).append(kw)
    db.commit()
    db.close()
    return deleted, missing


def clear_keywords():
    db = get_db()
    db.execute("DELETE FROM keywords")
    db.commit()
    db.close()


def get_push_history(limit=10):
    db = get_db()
    rows = db.execute(
        "SELECT title, link, author, category, matched_keywords, pushed_at FROM push_history ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    db.close()
    return rows


# ============ 键盘布局 ============

def main_menu_keyboard():
    paused = is_paused()
    pause_btn = {"text": "▶️ 恢复监控", "callback_data": "resume"} if paused else {"text": "⏸️ 暂停监控", "callback_data": "pause"}
    return {"inline_keyboard": [
        [
            {"text": "➕ 添加关键词", "callback_data": "add_kw"},
            {"text": "📋 关键词列表", "callback_data": "kw_list"},
        ],
        [
            {"text": "📊 监控状态", "callback_data": "status"},
            {"text": "📂 分类过滤", "callback_data": "cat_filter"},
        ],
        [
            {"text": "📜 推送历史", "callback_data": "history"},
            {"text": "🧪 测试推送", "callback_data": "test_push"},
        ],
        [
            pause_btn,
            {"text": "❓ 帮助", "callback_data": "help"},
        ],
    ]}


def keywords_keyboard():
    kws = get_keywords()
    if not kws:
        return {"inline_keyboard": [
            [{"text": "➕ 添加关键词", "callback_data": "add_kw"}],
            [{"text": "🔙 返回主菜单", "callback_data": "main"}],
        ]}
    rows = []
    row = []
    for keyword_id, kw in kws:
        row.append({"text": f"🗑 {kw[:30]}", "callback_data": f"del:{keyword_id}"})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        {"text": "🗑 清空全部", "callback_data": "clear_all"},
        {"text": "➕ 添加", "callback_data": "add_kw"},
    ])
    rows.append([{"text": "🔙 返回主菜单", "callback_data": "main"}])
    return {"inline_keyboard": rows}


def category_keyboard():
    config = get_categories_config()
    if config is None:
        config = {c: True for c in ALL_CATEGORIES}
    rows = []
    row = []
    for cat in ALL_CATEGORIES:
        enabled = config.get(cat, True)
        icon = "✅" if enabled else "⬜"
        row.append({"text": f"{icon} {cat}", "callback_data": f"toggle_cat:{cat}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "🔄 全部启用", "callback_data": "all_cats"}])
    rows.append([{"text": "🔙 返回主菜单", "callback_data": "main"}])
    return {"inline_keyboard": rows}


def back_keyboard():
    return {"inline_keyboard": [[{"text": "🔙 返回主菜单", "callback_data": "main"}]]}

# ============ 页面渲染 ============

def render_main(chat_id, message_id=None, use_edit=False):
    paused = is_paused()
    monitor_ok = is_monitor_running()
    if not monitor_ok:
        state = "🔴 Monitor 未运行"
    elif paused:
        state = "🟡 监控已暂停"
    else:
        state = "🟢 正在监控"
    text = (
        "🛰 <b>NodeSeek 雷达</b>\n"
        "━━━━━━━━━━━━\n"
        f"{state}\n\n"
        "命中关键词的新帖会自动推送到这里。\n"
        "从下面开始管理关键词、分类和监控状态。"
    )
    if use_edit:
        edit_message(chat_id, message_id, text, reply_markup=main_menu_keyboard())
    else:
        send_message(chat_id, text, reply_markup=main_menu_keyboard())


def render_status(chat_id, message_id=None, use_edit=False):
    db = get_db()
    stats = {
        "total_seen": db.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0],
        "matched": db.execute("SELECT COUNT(*) FROM push_history").fetchone()[0],
        "today_pushed": db.execute(
            "SELECT COUNT(*) FROM push_history WHERE pushed_at >= datetime('now', '-1 day')"
        ).fetchone()[0],
        "keyword_count": db.execute("SELECT COUNT(*) FROM keywords").fetchone()[0],
        "health": {
            "last_rss_success": state_get(db, "last_rss_success", "暂无数据"),
            "consecutive_errors": state_get(db, "consecutive_errors", "0"),
            "last_match": state_get(db, "last_match", "暂无"),
        },
    }
    kw_rows = db.execute("SELECT keyword FROM keywords ORDER BY keyword").fetchall()
    db.close()
    kws = [r[0] for r in kw_rows]
    kw_display = "、".join(kws[:10]) if kws else "无"
    if len(kws) > 10:
        kw_display += f" 等{len(kws)}个"

    monitor_ok = is_monitor_running()
    paused = is_paused()
    monitor_status = "🟢 运行中" if monitor_ok else "🔴 已停止"
    if monitor_ok and paused:
        monitor_status = "🟡 已暂停"

    enabled_cats = get_enabled_categories()
    if enabled_cats is None:
        cat_display = "全部"
    else:
        cat_display = "、".join(sorted(enabled_cats)) if enabled_cats else "无"

    health = stats["health"]
    last_success = health["last_rss_success"]
    last_match = health["last_match"]
    if last_success != "暂无数据":
        last_success = last_success.replace("T", " ").replace("+00:00", " UTC")
    if last_match != "暂无":
        last_match = last_match.replace("T", " ").replace("+00:00", " UTC")

    text = (
        "📡 <b>监控状态</b>\n"
        "━━━━━━━━━━━━\n\n"
        f"{monitor_status}\n"
        f"关键词：<b>{stats['keyword_count']}</b> 个\n"
        f"已处理：<b>{stats['total_seen']}</b> 条\n"
        f"成功推送：<b>{stats['matched']}</b> 条（24小时 <b>{stats['today_pushed']}</b> 条）\n\n"
        f"📂 分类　{escape_html(cat_display)}\n"
        f"🏷 关键词　{escape_html(kw_display)}\n\n"
        "<b>健康检查</b>\n"
        f"• RSS 最近成功：{escape_html(last_success)}\n"
        f"• 连续错误：{escape_html(health['consecutive_errors'])}\n"
        f"• 最近匹配：{escape_html(last_match)}\n\n"
        "每 2 秒拉取一次 RSS（0.5 QPS）"
    )
    kb = {"inline_keyboard": [
        [{"text": "🔄 刷新", "callback_data": "status"}],
        [{"text": "🔙 返回主菜单", "callback_data": "main"}],
    ]}
    if use_edit:
        edit_message(chat_id, message_id, text, reply_markup=kb)
    else:
        send_message(chat_id, text, reply_markup=kb)


def render_keywords(chat_id, message_id=None, use_edit=False, extra_text=""):
    kws = get_keyword_texts()
    if kws:
        text = f"🏷 <b>关键词</b>　共 <b>{len(kws)}</b> 个\n"
        text += "━━━━━━━━━━━━\n\n"
        text += "\n".join(f"<code>{i+1:02d}</code>　{escape_html(kw)}" for i, kw in enumerate(kws))
        text += "\n\n点下面的关键词即可删除。"
    else:
        text = (
            "🏷 <b>关键词</b>\n"
            "━━━━━━━━━━━━\n\n"
            "还没有订阅词。添加后，命中新帖会自动推送。"
        )
    if extra_text:
        text = extra_text + "\n\n" + text
    if use_edit:
        edit_message(chat_id, message_id, text, reply_markup=keywords_keyboard())
    else:
        send_message(chat_id, text, reply_markup=keywords_keyboard())


def begin_keyword_input(chat_id, message_id=None, use_edit=False):
    text = (
        "✏️ <b>添加关键词</b>\n"
        "━━━━━━━━━━━━\n\n"
        "直接发送关键词即可。多个关键词请用空格隔开。\n"
        "例如：<code>甲骨文 VMISS 家宽</code>\n\n"
        "正在等待输入…"
    )
    if use_edit:
        edit_message(chat_id, message_id, text, reply_markup=back_keyboard())
    else:
        send_message(chat_id, text, reply_markup=back_keyboard())


def render_history(chat_id, message_id=None, use_edit=False):
    history = get_push_history(10)
    if not history:
        text = (
            "🗂 <b>推送记录</b>\n"
            "━━━━━━━━━━━━\n\n"
            "暂时没有成功推送的记录。"
        )
    else:
        text = "🗂 <b>最近推送</b>　最多显示 10 条\n━━━━━━━━━━━━\n\n"
        for i, (title, link, author, category, matched_kw, pushed_at) in enumerate(history, 1):
            # 截断标题
            short_title = title[:40] + ("..." if len(title) > 40 else "")
            # 时间格式化
            try:
                dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                time_str = dt.strftime("%m-%d %H:%M")
            except Exception:
                time_str = pushed_at[:16]
            text += f"<b>{i:02d}</b>　<a href=\"{escape_html(link)}\">{escape_html(short_title)}</a>\n"
            text += f"　{escape_html(category)} · {escape_html(author)} · {escape_html(matched_kw)}\n"
            text += f"　{time_str}\n\n"
    kb = {"inline_keyboard": [
        [{"text": "🔄 刷新", "callback_data": "history"}],
        [{"text": "🔙 返回主菜单", "callback_data": "main"}],
    ]}
    if use_edit:
        edit_message(chat_id, message_id, text, reply_markup=kb)
    else:
        send_message(chat_id, text, reply_markup=kb)


def render_cat_filter(chat_id, message_id=None, use_edit=False):
    config = get_categories_config()
    if config is None:
        active = "全部启用"
    else:
        enabled = [k for k, v in config.items() if v]
        active = "、".join(enabled) if enabled else "无"
    text = (
        "📂 <b>分类过滤</b>\n"
        "━━━━━━━━━━━━\n\n"
        f"正在关注：<b>{escape_html(active)}</b>\n\n"
        "点分类名称即可开关；关闭的分类不会触发推送。"
    )
    if use_edit:
        edit_message(chat_id, message_id, text, reply_markup=category_keyboard())
    else:
        send_message(chat_id, text, reply_markup=category_keyboard())


def render_test(chat_id, message_id=None, use_edit=False):
    text = (
        "🛰 <b>NodeSeek 命中</b>\n"
        "━━━━━━━━━━━━\n"
        "<b>这是一条推送预览</b>\n\n"
        "推送模板显示正常，关键词命中后会以这个样式发送。\n\n"
        "<code>test · NodeSeek · " + datetime.now(timezone.utc).strftime("%m-%d %H:%M") + "</code>\n"
        "🏷 #测试 #预览\n"
        "🔗 <a href=\"https://www.nodeseek.com/\">打开原帖</a>"
    )
    keyboard = {"inline_keyboard": [
        [
            {"text": "🔗 查看原帖", "url": "https://www.nodeseek.com/"},
            {"text": "📂 test", "callback_data": "cat:test"},
        ],
        [{"text": "🔙 返回主菜单", "callback_data": "main"}],
    ]}
    if use_edit:
        edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        send_message(chat_id, text, reply_markup=keyboard)


def render_help(chat_id, message_id=None, use_edit=False):
    text = (
        "❔ <b>使用说明</b>\n"
        "━━━━━━━━━━━━\n\n"
        "<b>管理</b>\n"
        "• 添加关键词：点「添加关键词」或发送 <code>/add 词1 词2</code>\n"
        "• 删除关键词：在列表点名称，或发送 <code>/del 词1</code>\n"
        "• 清空全部：<code>/clear confirm</code>\n\n"
        "<b>筛选</b>\n"
        "• 分类过滤可关闭不想看的板块。\n"
        "• 暂停监控后不拉取 RSS，也不会产生推送。\n\n"
        "<b>频率</b>\n"
        "RSS 每 2 秒检查一次。命中关键词的新帖会带原帖链接推送到这里。"
    )
    if use_edit:
        edit_message(chat_id, message_id, text, reply_markup=back_keyboard())
    else:
        send_message(chat_id, text, reply_markup=back_keyboard())

# ============ 回调处理 ============

def handle_callback(callback):
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"]["message_id"]
    user_id = callback["from"]["id"]
    data = callback["data"]

    if user_id not in ALLOWED_USERS:
        answer_callback(callback["id"], "无权限")
        return

    answer_callback(callback["id"])
    user_states[user_id] = "idle"

    if data == "main":
        render_main(chat_id, message_id, use_edit=True)

    elif data == "status":
        render_status(chat_id, message_id, use_edit=True)

    elif data == "kw_list":
        render_keywords(chat_id, message_id, use_edit=True)

    elif data in ("add_kw", "manual_input"):
        user_states[user_id] = "awaiting_keyword"
        begin_keyword_input(chat_id, message_id, use_edit=True)

    elif data == "test_push":
        render_test(chat_id, message_id, use_edit=True)

    elif data == "help":
        render_help(chat_id, message_id, use_edit=True)

    elif data == "history":
        render_history(chat_id, message_id, use_edit=True)

    elif data == "cat_filter":
        render_cat_filter(chat_id, message_id, use_edit=True)

    elif data == "all_cats":
        db = get_db()
        for cat in ALL_CATEGORIES:
            set_category_config_enabled(db, cat, True)
        db.close()
        render_cat_filter(chat_id, message_id, use_edit=True)

    elif data.startswith("toggle_cat:"):
        cat = data[11:]
        config = get_categories_config() or {c: True for c in ALL_CATEGORIES}
        set_category_enabled(cat, not config.get(cat, True))
        render_cat_filter(chat_id, message_id, use_edit=True)

    elif data == "pause":
        pause_monitor()
        edit_message(chat_id, message_id,
            "⏸️ <b>监控已暂停</b>\n\n推送已停止，RSS 不再拉取。\n点击「恢复监控」继续。",
            reply_markup={"inline_keyboard": [
                [{"text": "▶️ 恢复监控", "callback_data": "resume"}],
                [{"text": "🔙 返回主菜单", "callback_data": "main"}],
            ]})

    elif data == "resume":
        resume_monitor()
        edit_message(chat_id, message_id,
            "▶️ <b>监控已恢复</b>\n\n推送已恢复，继续拉取 RSS。",
            reply_markup={"inline_keyboard": [
                [{"text": "⏸️ 暂停监控", "callback_data": "pause"}],
                [{"text": "🔙 返回主菜单", "callback_data": "main"}],
            ]})

    elif data == "clear_all":
        edit_message(chat_id, message_id,
            "⚠️ <b>确认清空所有关键词？</b>\n\n"
            "这将删除所有关键词，不可撤销。\n\n"
            "点击「确认清空」继续，或「取消」返回。",
            reply_markup={"inline_keyboard": [
                [{"text": "✅ 确认清空", "callback_data": "clear_confirm"},
                 {"text": "❌ 取消", "callback_data": "kw_list"}],
            ]})

    elif data == "clear_confirm":
        clear_keywords()
        render_keywords(chat_id, message_id, use_edit=True,
                       extra_text="🗑 已清空所有关键词")

    elif data.startswith("del:"):
        try:
            keyword_id = int(data[4:])
        except ValueError:
            keyword_id = -1
        db = get_db()
        row = db.execute("SELECT keyword FROM keywords WHERE id = ?", (keyword_id,)).fetchone()
        if row:
            db.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
            db.commit()
            db.close()
            render_keywords(chat_id, message_id, use_edit=True,
                           extra_text=f"🗑 已删除: {escape_html(row[0])}")
        else:
            db.close()
            render_keywords(chat_id, message_id, use_edit=True,
                           extra_text="⚠️ 该关键词已不存在，请刷新列表")

    elif data.startswith("cat:"):
        cat = data[4:]
        send_message(chat_id, f"📂 分类: {escape_html(cat)}")


def handle_message(message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()

    if user_id not in ALLOWED_USERS:
        send_message(chat_id, "⛔ 无权限")
        return

    if not text:
        return

    if text.startswith("/"):
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]
        user_states[user_id] = "idle"

        if cmd == "/start":
            render_main(chat_id)
        elif cmd == "/help":
            render_help(chat_id)
        elif cmd == "/status":
            render_status(chat_id)
        elif cmd == "/keywords":
            render_keywords(chat_id)
        elif cmd == "/add":
            if args:
                added, skipped, rejected = add_keywords(args)
                extra = ""
                if added:
                    extra += f"✅ 已添加 {len(added)} 个: {', '.join(escape_html(a) for a in added)}"
                if skipped:
                    extra += f"\n⚠️ 已存在 {len(skipped)} 个: {', '.join(escape_html(s) for s in skipped)}"
                if rejected:
                    extra += f"\n⚠️ 未添加: {', '.join(f'{escape_html(k)}（{escape_html(reason)}）' for k, reason in rejected)}"
                render_keywords(chat_id, extra_text=extra)
            else:
                user_states[user_id] = "awaiting_keyword"
                begin_keyword_input(chat_id)
        elif cmd == "/del":
            if args:
                deleted, missing = del_keywords(args)
                extra = ""
                if deleted:
                    extra += f"🗑 已删除 {len(deleted)} 个: {', '.join(escape_html(a) for a in deleted)}"
                if missing:
                    extra += f"\n⚠️ 不存在: {', '.join(escape_html(a) for a in missing)}"
                render_keywords(chat_id, extra_text=extra)
            else:
                render_keywords(chat_id)
        elif cmd == "/clear":
            if args == ["confirm"]:
                clear_keywords()
                render_keywords(chat_id, extra_text="🗑 已清空所有关键词")
            else:
                send_message(chat_id, "⚠️ 此操作会删除全部关键词。确认请发送：<code>/clear confirm</code>")
        else:
            send_message(chat_id, "未知命令，请使用按钮操作或发送 /start")
        return

    state = user_states.get(user_id, "idle")
    if state == "awaiting_keyword":
        keywords = text.split()
        added, skipped, rejected = add_keywords(keywords)
        user_states[user_id] = "idle"
        extra = ""
        if added:
            extra += f"✅ 已添加 {len(added)} 个: {', '.join(escape_html(a) for a in added)}"
        if skipped:
            extra += f"\n⚠️ 已存在 {len(skipped)} 个: {', '.join(escape_html(s) for s in skipped)}"
        if rejected:
            extra += f"\n⚠️ 未添加: {', '.join(f'{escape_html(k)}（{escape_html(reason)}）' for k, reason in rejected)}"
        if extra:
            render_keywords(chat_id, extra_text=extra)
        else:
            send_message(chat_id, "未添加任何关键词")
    else:
        send_message(chat_id,
            "💡 请点击下方按钮操作，或发送 /start\n\n"
            "如需添加关键词，请先点「➕ 添加关键词」",
            reply_markup=main_menu_keyboard())


# ============ 主循环 ============

_running = True

def _signal_handler(signum, frame):
    global _running
    _running = False

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def main():
    global _running
    if not BOT_TOKEN or not CHAT_ID:
        print("[ERROR] NODESEEK_BOT_TOKEN or NODESEEK_CHAT_ID not set", file=sys.stderr)
        sys.exit(1)

    init_db()

    result = tg_api("getMe")
    if not result.get("ok"):
        print(f"[ERROR] Invalid bot token: {result}", file=sys.stderr)
        sys.exit(1)

    bot_info = result["result"]
    print(f"[INFO] Bot started: @{bot_info['username']} ({bot_info['first_name']})")
    print(f"[INFO] Chat ID: {CHAT_ID}")

    # 注册命令列表，让用户输入 / 时自动弹出
    commands = [
        {"command": "start", "description": "打开主菜单"},
        {"command": "status", "description": "查看监控状态"},
        {"command": "keywords", "description": "查看关键词列表"},
        {"command": "add", "description": "添加关键词 (空格分隔多个)"},
        {"command": "del", "description": "删除关键词 (空格分隔多个)"},
        {"command": "clear", "description": "清空关键词（需 confirm 确认）"},
        {"command": "help", "description": "查看帮助"},
    ]
    cmd_result = tg_api("setMyCommands", commands=commands)
    if cmd_result.get("ok"):
        print(f"[INFO] Registered {len(commands)} commands")
    else:
        print(f"[WARN] setMyCommands failed: {cmd_result.get('description')}")

    # 设置菜单按钮为命令列表，聊天界面左下角汉堡按钮点开即显示所有命令
    menu_result = tg_api("setChatMenuButton", menu_button={"type": "commands"})
    if menu_result.get("ok"):
        print(f"[INFO] Menu button set to commands")
    else:
        print(f"[WARN] setChatMenuButton failed: {menu_result.get('description')}")

    offset = load_offset()
    print(f"[INFO] Resumed from offset: {offset}")

    while _running:
        try:
            result = tg_api("getUpdates", request_timeout=65, offset=offset, timeout=50)
            if not result.get("ok"):
                print(f"[WARN] getUpdates failed: {result.get('description')}")
                time.sleep(5)
                continue

            for update in result.get("result", []):
                started = time.monotonic()
                offset = update["update_id"] + 1
                save_offset(offset)

                if "callback_query" in update:
                    handle_callback(update["callback_query"])
                    print(f"[PERF] callback processed in {time.monotonic() - started:.3f}s")

                if "message" in update:
                    handle_message(update["message"])
                    print(f"[PERF] message processed in {time.monotonic() - started:.3f}s")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            time.sleep(5)

    print("[INFO] Bot stopped.")


if __name__ == "__main__":
    main()
