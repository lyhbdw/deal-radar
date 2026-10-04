"""DealRadar — 共享数据结构、源配置与辅助工具。"""
from __future__ import annotations
import re

MAX_TELEGRAM_TEXT = 3900

SOURCES = [
    {"id": "nodeseek", "name": "NodeSeek", "emoji": "🛰", "rss_url": "https://rss.nodeseek.com/", "interval": 1.0},
    {"id": "sbsb", "name": "烧饼论坛", "emoji": "🥞", "rss_url": "https://sb.sb/rss.xml", "interval": 1.0},
    {"id": "idcflare", "name": "IDC Flare", "emoji": "🔥", "rss_url": "https://idcflare.com/latest.rss", "interval": 1.0},
]
SOURCE_IDS = [s["id"] for s in SOURCES]

def clamp_telegram_text(text: str) -> str:
    """截断超长 Telegram 消息文本，避免破坏 HTML 实体。"""
    if len(text) <= MAX_TELEGRAM_TEXT:
        return text
    cut = text[:MAX_TELEGRAM_TEXT - 1]
    return re.sub(r'&[a-zA-Z0-9#]{1,10}$', '', cut) + "…"
