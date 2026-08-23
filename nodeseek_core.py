"""Shared, dependency-free reliability helpers for NodeSeek services."""

import json
import sqlite3
from urllib.error import HTTPError


STATE_BOOTSTRAPPED = "initial_baseline_complete"


def ensure_keywords_schema(db: sqlite3.Connection) -> None:
    """Migrate legacy keyword TEXT PK table to stable integer identifiers."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(keywords)")}
    if not columns:
        db.execute("""
            CREATE TABLE keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL COLLATE NOCASE UNIQUE,
                added_at TEXT NOT NULL
            )
        """)
    elif "id" not in columns:
        db.execute("ALTER TABLE keywords RENAME TO keywords_legacy")
        db.execute("""
            CREATE TABLE keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL COLLATE NOCASE UNIQUE,
                added_at TEXT NOT NULL
            )
        """)
        db.execute("INSERT INTO keywords (keyword, added_at) SELECT keyword, added_at FROM keywords_legacy")
        db.execute("DROP TABLE keywords_legacy")
    db.commit()


def ensure_category_table(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS category_config (
            category TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL CHECK(enabled IN (0, 1))
        )
    """)
    db.commit()


def get_category_config(db: sqlite3.Connection):
    ensure_category_table(db)
    return {category: bool(enabled) for category, enabled in db.execute(
        "SELECT category, enabled FROM category_config"
    )}


def set_category_enabled(db: sqlite3.Connection, category: str, enabled: bool) -> None:
    ensure_category_table(db)
    db.execute(
        "INSERT INTO category_config (category, enabled) VALUES (?, ?) "
        "ON CONFLICT(category) DO UPDATE SET enabled = excluded.enabled",
        (category, int(enabled)),
    )
    db.commit()


def ensure_state_table(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS monitor_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    db.commit()


def _state_get(db: sqlite3.Connection, key: str):
    row = db.execute("SELECT value FROM monitor_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def bootstrap_required(db: sqlite3.Connection) -> bool:
    """Only true for a completely fresh install, never after a restart/migration."""
    ensure_state_table(db)
    if _state_get(db, STATE_BOOTSTRAPPED) is not None:
        return False
    seen = db.execute("SELECT 1 FROM seen_posts LIMIT 1").fetchone()
    if seen:
        mark_bootstrapped(db)
        return False
    return True


def mark_bootstrapped(db: sqlite3.Connection) -> None:
    ensure_state_table(db)
    db.execute(
        "INSERT OR REPLACE INTO monitor_state (key, value) VALUES (?, ?)",
        (STATE_BOOTSTRAPPED, "1"),
    )
    db.commit()


def state_set(db: sqlite3.Connection, key: str, value: str) -> None:
    ensure_state_table(db)
    db.execute("INSERT OR REPLACE INTO monitor_state (key, value) VALUES (?, ?)", (key, value))
    db.commit()


def state_get(db: sqlite3.Connection, key: str, default=None):
    ensure_state_table(db)
    value = _state_get(db, key)
    return default if value is None else value


def retry_after_seconds(error: HTTPError, default: int) -> int:
    """Get Retry-After from HTTP header or Telegram JSON response; fall back safely."""
    raw_header = error.headers.get("Retry-After") if error.headers else None
    try:
        if raw_header:
            return max(1, int(raw_header))
    except (TypeError, ValueError):
        pass
    try:
        body = error.read().decode("utf-8")
        value = json.loads(body).get("parameters", {}).get("retry_after")
        return max(1, int(value))
    except (AttributeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return default


def validate_keywords(raw_keywords, max_length=40, max_count=10):
    """Normalize case-insensitively, remove duplicates, and return reasons for rejects."""
    accepted, rejected, seen = [], [], set()
    for raw in raw_keywords:
        keyword = str(raw).strip()
        if not keyword:
            rejected.append((keyword, "为空"))
            continue
        if len(keyword) > max_length:
            rejected.append((keyword, f"超过 {max_length} 个字符"))
            continue
        normalized = keyword.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        if len(accepted) >= max_count:
            rejected.append((keyword, f"单次最多 {max_count} 个"))
            continue
        accepted.append(keyword)
    return accepted, rejected
