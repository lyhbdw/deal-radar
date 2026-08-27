import io
import json
import sqlite3
import unittest
from urllib.error import HTTPError

from nodeseek_core import (
    SOURCES,
    SOURCE_IDS,
    bootstrap_required,
    ensure_keywords_schema,
    get_category_config,
    mark_bootstrapped,
    migrate_multi_source,
    retry_after_seconds,
    set_category_enabled,
    validate_keywords,
)


class BootstrapStateTests(unittest.TestCase):
    def db(self, source_id="nodeseek"):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE seen_posts (guid TEXT PRIMARY KEY, source TEXT)")
        return db

    def test_empty_database_requires_initial_baseline(self):
        for sid in SOURCE_IDS:
            self.assertTrue(bootstrap_required(self.db(), sid), f"{sid} should require bootstrap")

    def test_existing_seen_posts_are_migrated_without_rebaselining(self):
        for sid in SOURCE_IDS:
            db = self.db()
            db.execute("INSERT INTO seen_posts VALUES ('existing', ?)", (sid,))
            db.commit()
            self.assertFalse(bootstrap_required(db, sid))
            self.assertFalse(bootstrap_required(db, sid))

    def test_explicit_bootstrap_marker_prevents_rebaselining(self):
        for sid in SOURCE_IDS:
            db = self.db()
            mark_bootstrapped(db, sid)
            self.assertFalse(bootstrap_required(db, sid))

    def test_bootstrap_is_per_source(self):
        """Bootstrapping one source does not bootstrap another."""
        db = self.db()
        mark_bootstrapped(db, "nodeseek")
        self.assertFalse(bootstrap_required(db, "nodeseek"))
        self.assertTrue(bootstrap_required(db, "sbsb"))


class RetryAfterTests(unittest.TestCase):
    def test_telegram_http_429_json_body_uses_retry_after(self):
        body = json.dumps({"parameters": {"retry_after": 17}}).encode()
        error = HTTPError("https://api.telegram.org", 429, "Too Many Requests", {}, io.BytesIO(body))
        self.assertEqual(retry_after_seconds(error, default=30), 17)

    def test_invalid_or_missing_retry_after_uses_default(self):
        error = HTTPError("https://example.test", 429, "Too Many Requests", {}, io.BytesIO(b"not json"))
        self.assertEqual(retry_after_seconds(error, default=30), 30)


class KeywordValidationTests(unittest.TestCase):
    def test_normalizes_deduplicates_and_rejects_overlong_keywords(self):
        accepted, rejected = validate_keywords([" VPS ", "vps", "", "x" * 41], max_length=40)
        self.assertEqual(accepted, ["VPS"])
        self.assertEqual(rejected, [("", "为空"), ("x" * 41, "超过 40 个字符")])

    def test_limits_batch_size(self):
        accepted, rejected = validate_keywords([str(i) for i in range(12)], max_count=10)
        self.assertEqual(accepted, [str(i) for i in range(10)])
        self.assertEqual(rejected, [("10", "单次最多 10 个"), ("11", "单次最多 10 个")])


class KeywordSchemaTests(unittest.TestCase):
    def test_legacy_keywords_are_migrated_to_stable_integer_ids(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE keywords (keyword TEXT PRIMARY KEY, added_at TEXT)")
        db.execute("INSERT INTO keywords VALUES ('甲骨文', '2026-01-01T00:00:00+00:00')")
        db.commit()
        ensure_keywords_schema(db)
        row = db.execute("SELECT id, keyword, added_at FROM keywords").fetchone()
        self.assertIsInstance(row[0], int)
        self.assertEqual(row[1:], ('甲骨文', '2026-01-01T00:00:00+00:00'))


class CategoryConfigTests(unittest.TestCase):
    def test_category_toggle_is_persisted_per_source(self):
        db = sqlite3.connect(":memory:")
        for sid in SOURCE_IDS:
            self.assertEqual(get_category_config(db, sid), {}, f"{sid} should start empty")
            set_category_enabled(db, sid, "trade", False)
            set_category_enabled(db, sid, "review", True)
            self.assertEqual(
                get_category_config(db, sid),
                {"trade": False, "review": True},
                f"{sid} config mismatch",
            )

    def test_category_config_is_isolated_per_source(self):
        """Toggling a category for one source does not affect another."""
        db = sqlite3.connect(":memory:")
        set_category_enabled(db, "nodeseek", "trade", False)
        self.assertEqual(get_category_config(db, "sbsb"), {})
        self.assertEqual(get_category_config(db, "nodeseek"), {"trade": False})


class MultiSourceMigrationTests(unittest.TestCase):
    def test_migrate_adds_source_columns(self):
        db = sqlite3.connect(":memory:")
        # Simulate legacy schema (no source column)
        db.execute("CREATE TABLE seen_posts (guid TEXT PRIMARY KEY, title TEXT)")
        db.execute("CREATE TABLE push_history (id INTEGER PRIMARY KEY, guid TEXT)")
        db.execute("CREATE TABLE monitor_state (key TEXT PRIMARY KEY, value TEXT)")
        # Legacy state keys
        db.execute("INSERT INTO monitor_state VALUES ('initial_baseline_complete', '1')")
        db.execute("INSERT INTO monitor_state VALUES ('last_rss_success', '2026-01-01')")
        db.commit()
        migrate_multi_source(db)
        # source columns exist
        cols = {r[1] for r in db.execute("PRAGMA table_info(seen_posts)")}
        self.assertIn("source", cols)
        # legacy state keys migrated to source-scoped
        row = db.execute("SELECT value FROM monitor_state WHERE key = 'initial_baseline_complete:nodeseek'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "1")
        # legacy key removed
        row = db.execute("SELECT 1 FROM monitor_state WHERE key = 'initial_baseline_complete'").fetchone()
        self.assertIsNone(row)

    def test_migrate_is_idempotent(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE seen_posts (guid TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE push_history (id INTEGER PRIMARY KEY, guid TEXT)")
        migrate_multi_source(db)
        # Second call should not raise
        migrate_multi_source(db)
        cols = {r[1] for r in db.execute("PRAGMA table_info(seen_posts)")}
        self.assertIn("source", cols)

    def test_legacy_category_config_migrated_to_nodeseek(self):
        db = sqlite3.connect(":memory:")
        # Need all tables migrate_multi_source touches
        db.execute("CREATE TABLE seen_posts (guid TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE push_history (id INTEGER PRIMARY KEY, guid TEXT)")
        db.execute("CREATE TABLE monitor_state (key TEXT PRIMARY KEY, value TEXT)")
        # Simulate legacy category_config without source column
        db.execute("CREATE TABLE category_config (category TEXT PRIMARY KEY, enabled INTEGER)")
        db.execute("INSERT INTO category_config VALUES ('trade', 0)")
        db.execute("INSERT INTO category_config VALUES ('tech', 1)")
        db.commit()
        migrate_multi_source(db)
        # Legacy entries should now be under nodeseek
        config = get_category_config(db, "nodeseek")
        self.assertEqual(config, {"trade": False, "tech": True})
        # sbsb should be empty
        self.assertEqual(get_category_config(db, "sbsb"), {})


class SourceRegistryTests(unittest.TestCase):
    def test_sources_have_required_fields(self):
        for s in SOURCES:
            self.assertIn("id", s)
            self.assertIn("name", s)
            self.assertIn("emoji", s)
            self.assertIn("rss_url", s)
            self.assertIn("categories", s)
            self.assertIsInstance(s["categories"], list)
            self.assertTrue(len(s["categories"]) > 0)

    def test_source_ids_are_unique(self):
        self.assertEqual(len(SOURCE_IDS), len(set(SOURCE_IDS)))


if __name__ == "__main__":
    unittest.main()
