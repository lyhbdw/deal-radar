#!/usr/bin/env python3
"""
NodeSeek RSS 关键词监控 - 常驻轮询守护进程
- 每 2 秒拉取 RSS（0.5 QPS）
- 关键词匹配 + 分类过滤 + 暂停/恢复
- Telegram 推送（带按钮）+ 429 退避 + 单轮上限
- 推送历史记录 + RSS 持续失败告警
"""

import json
import re
import os
import sys
import time
import signal
import urllib.request
import urllib.error
import sqlite3
import random
import socket
from contextlib import contextmanager
from nodeseek_core import (
    bootstrap_required, ensure_keywords_schema, get_category_config, mark_bootstrapped,
    retry_after_seconds, state_get, state_set,
)
import logging
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

# ============ 配置 ============

RSS_URL = "https://rss.nodeseek.com/"
DB_PATH = "/root/nodeseek_monitor.db"
PAUSE_FILE = "/root/nodeseek_paused"        # 暂停标志文件
CATEGORIES_FILE = "/root/nodeseek_categories.json"  # 分类过滤配置
POLL_INTERVAL = 2
TELEGRAM_INTERVAL = 0.5
MAX_RETRIES = 3
RETRY_DELAY = 3
ERROR_BACKOFF = 30
MAX_PUSH_PER_ROUND = 10       # 单轮最大推送数，防止刷屏
ALL_CATEGORIES = {"trade", "daily", "review", "tech", "info", "dev", "carpool", "expose", "photo-share"}
TELEGRAM_429_WAIT = 30        # 429 退避秒数
RSS_ALERT_THRESHOLD = 10      # 连续失败多少次后推送告警

_env_file = "/root/.nodeseek_env"
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN = os.environ.get("NODESEEK_BOT_TOKEN", "")
CHAT_ID = os.environ.get("NODESEEK_CHAT_ID", "")

# ============ 日志 ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler("/root/nodeseek_monitor.log", maxBytes=2_000_000, backupCount=3),
    ],
)
log = logging.getLogger("nodeseek")

# ============ 运行标志 ============

_running = True

def signal_handler(signum, frame):
    global _running
    _running = False
    log.info("收到停止信号，正在退出...")

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============ 暂停/分类控制 ============

def is_paused():
    return os.path.exists(PAUSE_FILE)

def get_enabled_categories(db):
    """返回启用分类集合；未设置任何开关时表示全部启用。"""
    config = get_category_config(db)
    if not config:
        return None
    enabled = {category for category, is_enabled in config.items() if is_enabled}
    # 对从未配置过的新增分类维持默认启用，已明确关闭的分类才不推送。
    return enabled | (ALL_CATEGORIES - set(config))

# ============ 数据库 ============

def get_db():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS seen_posts (
            guid TEXT PRIMARY KEY,
            title TEXT, link TEXT, author TEXT, category TEXT,
            pub_date TEXT, matched_keywords TEXT, first_seen TEXT
        )
    """)
    ensure_keywords_schema(db)
    db.execute("""
        CREATE TABLE IF NOT EXISTS push_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT, title TEXT, link TEXT, author TEXT,
            category TEXT, pub_date TEXT, matched_keywords TEXT, pushed_at TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_first_seen ON seen_posts(first_seen)")
    db.commit()
    return db


def load_keywords(db):
    rows = db.execute("SELECT keyword FROM keywords").fetchall()
    return [r[0] for r in rows]


def is_seen(db, guid):
    return db.execute("SELECT 1 FROM seen_posts WHERE guid = ?", (guid,)).fetchone() is not None


def mark_seen(db, guid, title, link, author, category, pub_date, matched_kw):
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT OR IGNORE INTO seen_posts (guid, title, link, author, category, pub_date, matched_keywords, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (guid, title, link, author, category, pub_date, ",".join(matched_kw), now),
    )


def add_push_history(db, post, matched_kw):
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO push_history (guid, title, link, author, category, pub_date, matched_keywords, pushed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (post["guid"], post["title"], post["link"], post["author"],
         post["category"], post["pub_date"], ",".join(matched_kw), now),
    )


# ============ RSS 解析 ============

def clean_text(text):
    text = text.replace("\x1b", "")
    text = re.sub(r'\[\d+m', '', text)
    return text


def fetch_rss(url, retries=MAX_RETRIES):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "NodeSeekMonitor/2.1",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = retry_after_seconds(e, default=ERROR_BACKOFF)
                log.warning(f"RSS 429 限流，等待 {wait}s 后重试")
            else:
                wait = RETRY_DELAY * (attempt + 1) + random.uniform(0, 1)
                log.warning(f"RSS HTTP {e.code} (attempt {attempt+1}/{retries})，{wait:.1f}s 后重试")
            if attempt < retries - 1:
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < retries - 1:
                wait = RETRY_DELAY * (attempt + 1) + random.uniform(0, 1)
                log.warning(f"RSS 拉取失败 (attempt {attempt+1}/{retries}): {e}；{wait:.1f}s 后重试")
                time.sleep(wait)
            else:
                raise


def parse_rss(xml_text):
    items = []
    root = ET.fromstring(xml_text)

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        desc = clean_text((item.findtext("description") or "").strip())
        pub_date = (item.findtext("pubDate") or "").strip()
        category = (item.findtext("category") or "").strip()
        creator_el = item.find("{http://purl.org/dc/elements/1.1/}creator")
        author = creator_el.text.strip() if creator_el is not None and creator_el.text else ""

        if not guid:
            guid = link

        items.append({
            "guid": guid, "title": title, "link": link,
            "description": desc, "author": author,
            "category": category, "pub_date": pub_date,
        })
    return items


# ============ 关键词匹配 ============


def match_keywords(text, keywords):
    matched = []
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            matched.append(kw)
    return matched


# ============ Telegram 推送 ============

def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@contextmanager
def telegram_ipv4_only():
    """Match Bot's stable IPv4 route for actual monitoring notifications."""
    original = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host == "api.telegram.org":
            return original(host, port, socket.AF_INET, type, proto, flags)
        return original(host, port, family, type, proto, flags)

    socket.getaddrinfo = ipv4_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original


def send_telegram(token, chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    for attempt in range(2):
        try:
            with telegram_ipv4_only():
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                if result.get("ok"):
                    return True
                log.error(f"Telegram error: {result.get('description', '')}")
                return False
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                wait = retry_after_seconds(e, default=TELEGRAM_429_WAIT)
                log.warning(f"Telegram 429，等待 {wait}s 后重试该消息")
                time.sleep(wait)
                continue
            log.error(f"Telegram HTTP {e.code}: {e.reason}")
            return False
        except Exception as e:
            log.error(f"Telegram send failed: {e}")
            return False
    return False


def format_message(post, matched_kw):
    clean_desc = clean_text(post["description"])
    title = escape_html(clean_text(post["title"]))
    author = escape_html(post["author"])
    category = escape_html(post["category"])
    kw_str = " ".join(f"#{k.replace(' ', '_')}" for k in matched_kw)

    # 发布时间格式化：Sun, 23 Aug 2026 18:10:23 GMT -> 08-23 18:10
    pub_date = post.get("pub_date", "")
    time_str = ""
    if pub_date:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pub_date)
            time_str = dt.strftime("%m-%d %H:%M")
        except Exception:
            time_str = pub_date[:16]

    lines = [
        "🛰 <b>NodeSeek 命中</b>",
        "━━━━━━━━━━━━",
        f"<b>{title}</b>",
    ]

    # 空描述时不显示摘要
    if clean_desc:
        desc = clean_desc[:200]
        if len(clean_desc) > 200:
            desc += "..."
        lines.extend(["", escape_html(desc)])

    meta = " · ".join(part for part in (category, author, time_str) if part)
    if meta:
        lines.extend(["", f"<code>{meta}</code>"])
    lines.append(f"🏷 {kw_str}")
    lines.append(f"🔗 <a href=\"{post['link']}\">打开原帖</a>")

    return "\n".join(lines)


def build_post_keyboard(post, matched_kw):
    return {"inline_keyboard": [[
        {"text": "🔗 查看原帖", "url": post["link"]},
        {"text": f"📂 {post['category']}", "callback_data": f"cat:{post['category']}"},
    ]]}


# ============ 主循环 ============

def main():
    if not BOT_TOKEN or not CHAT_ID:
        log.error("NODESEEK_BOT_TOKEN or NODESEEK_CHAT_ID not set")
        sys.exit(1)

    db = get_db()
    log.info(f"=== NodeSeek 关键词监控启动 ===")
    log.info(f"RSS: {RSS_URL}")
    log.info(f"轮询间隔: {POLL_INTERVAL}s (0.5 QPS)")
    log.info(f"单轮推送上限: {MAX_PUSH_PER_ROUND}")

    # 仅全新安装时静默建立基线；已有数据库/重启期间的新帖必须继续匹配。
    try:
        if bootstrap_required(db):
            xml_text = fetch_rss(RSS_URL)
            posts = parse_rss(xml_text)
            for p in posts:
                mark_seen(db, p["guid"], p["title"], p["link"],
                          p["author"], p["category"], p["pub_date"], [])
            db.commit()
            mark_bootstrapped(db)
            log.info(f"首次启动: 标记 {len(posts)} 条现有帖子为已见")
        else:
            log.info("已存在监控状态：恢复轮询，不跳过重启期间的新帖")
    except Exception as e:
        log.warning(f"启动拉取失败，继续: {e}")

    consecutive_errors = 0
    poll_count = 0
    rss_alert_sent = False
    outage_started = None

    while _running:
        try:
            # 检查暂停状态
            if is_paused():
                time.sleep(POLL_INTERVAL)
                continue

            # 加载关键词和分类过滤
            keywords = load_keywords(db)
            enabled_cats = get_enabled_categories(db)

            # 拉取 RSS
            xml_text = fetch_rss(RSS_URL)
            posts = parse_rss(xml_text)
            if rss_alert_sent and outage_started is not None:
                elapsed = int(time.monotonic() - outage_started)
                send_telegram(
                    BOT_TOKEN, CHAT_ID,
                    f"✅ <b>监控已恢复</b>\n\nRSS 已恢复正常，刚才故障持续约 {elapsed // 60} 分 {elapsed % 60} 秒。",
                )
                log.info(f"RSS 已恢复，故障持续 {elapsed}s")
            consecutive_errors = 0
            rss_alert_sent = False
            outage_started = None
            poll_count += 1
            # 每约 30 秒持久化一次健康指标，避免 2 秒轮询造成无谓 SQLite 写入。
            if poll_count % 15 == 0:
                state_set(db, "last_rss_success", datetime.now(timezone.utc).isoformat())
                state_set(db, "consecutive_errors", "0")

            # 逆序处理
            new_matches = 0
            pushed_this_round = 0
            for post in reversed(posts):
                if not _running:
                    break
                if is_seen(db, post["guid"]):
                    continue

                # 分类过滤
                if enabled_cats is not None and post["category"] not in enabled_cats:
                    mark_seen(db, post["guid"], post["title"], post["link"],
                              post["author"], post["category"], post["pub_date"], [])
                    continue

                combined_text = f"{post['title']} {post['description']}"
                matched = match_keywords(combined_text, keywords)

                if matched:
                    # 单轮推送上限
                    if pushed_this_round >= MAX_PUSH_PER_ROUND:
                        log.warning(f"单轮推送已达上限 {MAX_PUSH_PER_ROUND}，剩余帖子下轮处理")
                        # 不标记已见，下轮继续
                        break

                    msg = format_message(post, matched)
                    keyboard = build_post_keyboard(post, matched)
                    if send_telegram(BOT_TOKEN, CHAT_ID, msg, reply_markup=keyboard):
                        new_matches += 1
                        pushed_this_round += 1
                        add_push_history(db, post, matched)
                        state_set(db, "last_match", datetime.now(timezone.utc).isoformat(), commit=False)
                        state_set(db, "last_match_title", post["title"][:120], commit=False)
                        log.info(f"[NEW] {post['title']} -> {matched}")
                        time.sleep(TELEGRAM_INTERVAL)
                        mark_seen(db, post["guid"], post["title"], post["link"],
                                  post["author"], post["category"], post["pub_date"], matched)
                    else:
                        log.warning(f"推送失败，跳过标记: {post['title']}")
                        continue

                # 不匹配的帖子标记已见
                mark_seen(db, post["guid"], post["title"], post["link"],
                          post["author"], post["category"], post["pub_date"], [])

            if new_matches > 0:
                log.info(f"本轮 {new_matches} 条新匹配 (poll #{poll_count})")

            db.commit()

            # 清理旧数据 + 历史 + WAL checkpoint（约每小时一次）
            if poll_count % 1800 == 0:
                deleted_seen = db.execute("DELETE FROM seen_posts WHERE first_seen < datetime('now', '-7 days')").rowcount
                db.execute("DELETE FROM push_history WHERE id NOT IN (SELECT id FROM push_history ORDER BY id DESC LIMIT 100)")
                db.execute("PRAGMA wal_checkpoint(PASSIVE)")
                db.commit()
                if deleted_seen > 0:
                    log.info(f"清理旧数据: 删除 {deleted_seen} 条过期记录，WAL checkpoint 完成 (poll #{poll_count})")

        except Exception as e:
            consecutive_errors += 1
            if outage_started is None:
                outage_started = time.monotonic()
            # 记录故障指标；状态页可显示而不必读取日志。
            try:
                state_set(db, "consecutive_errors", str(consecutive_errors))
                state_set(db, "last_rss_error", str(e)[:200])
            except sqlite3.Error:
                pass
            log.error(f"主循环错误 (连续 {consecutive_errors} 次): {e}")

            # RSS 持续失败告警
            if consecutive_errors >= RSS_ALERT_THRESHOLD and not rss_alert_sent:
                alert_msg = f"⚠️ <b>监控告警</b>\n\nRSS 拉取连续失败 {consecutive_errors} 次\n错误: {escape_html(str(e)[:200])}\n\n监控仍在重试中..."
                send_telegram(BOT_TOKEN, CHAT_ID, alert_msg)
                rss_alert_sent = True

            if consecutive_errors >= 5:
                log.warning(f"连续失败 {consecutive_errors} 次，退避 {ERROR_BACKOFF}s")
                time.sleep(ERROR_BACKOFF)
            else:
                time.sleep(RETRY_DELAY)
            continue

        time.sleep(POLL_INTERVAL)

    db.close()
    log.info("监控已停止")


if __name__ == "__main__":
    main()
