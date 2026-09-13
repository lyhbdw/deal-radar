import gzip
import sqlite3
import subprocess
import sys
from pathlib import Path

import feedsentinel_monitor
from singleuser import connect


def test_database_connection_has_wal_and_safe_pragmas(tmp_path):
    db = connect(str(tmp_path / "test.db"))
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA synchronous").fetchone()[0] in (1, 2)
    db.close()


def test_delivery_history_preserves_matched_keywords(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    db = connect(str(db_path))
    db.execute("INSERT INTO seen_posts(source,guid,title,matched_keywords,first_seen) VALUES(?,?,?,?,datetime('now'))", ("test", "g1", "title", "alpha,beta"))
    db.execute("INSERT INTO notifications(source,guid,message,markup,next_attempt,created_at) VALUES(?,?,?,?,datetime('now'),datetime('now'))", ("test", "g1", "message", "{}"))
    db.commit()
    monkeypatch.setattr(feedsentinel_monitor, "send", lambda *args: (True, 30, ""))
    feedsentinel_monitor.deliver(db)
    assert db.execute("SELECT matched_keywords FROM notification_history").fetchone()[0] == "alpha,beta"
    db.close()


def test_delivery_handles_missing_seen_post_gracefully(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    db = connect(str(db_path))
    db.execute("INSERT INTO notifications(source,guid,message,markup,next_attempt,created_at) VALUES(?,?,?,?,datetime('now'),datetime('now'))", ("test", "g2", "message", "{}"))
    db.commit()
    monkeypatch.setattr(feedsentinel_monitor, "send", lambda *args: (True, 30, ""))
    feedsentinel_monitor.deliver(db)
    row = db.execute("SELECT status, sent_at FROM notifications WHERE guid='g2'").fetchone()
    assert row[0] == "sent"
    db.close()


def test_backup_archive_is_a_valid_sqlite_database(tmp_path):
    source = tmp_path / "source.db"
    backup_dir = tmp_path / "backups"
    db = sqlite3.connect(source)
    db.execute("CREATE TABLE sample(value TEXT)")
    db.execute("INSERT INTO sample VALUES ('ok')")
    db.commit()
    db.close()
    backup_script = Path(__file__).resolve().parent / "backup.py"
    subprocess.run([sys.executable, str(backup_script), str(source), str(backup_dir)], check=True)
    archive = next(backup_dir.glob("feedsentinel-*.db.gz"))
    restored = tmp_path / "restored.db"
    with gzip.open(archive, "rb") as inp, restored.open("wb") as out:
        out.write(inp.read())
    check = sqlite3.connect(restored)
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert check.execute("SELECT value FROM sample").fetchone()[0] == "ok"
    check.close()


def test_deliver_does_not_leave_uncommitted_transaction_when_idle(tmp_path):
    db_path = tmp_path / "test.db"
    db = connect(str(db_path))
    feedsentinel_monitor.deliver(db)
    assert not db.in_transaction
    db2 = connect(str(db_path))
    from singleuser import add_keywords
    added, _, _ = add_keywords(db2, ["test_keyword"])
    assert added == ["test_keyword"]
    db2.close()
    db.close()
