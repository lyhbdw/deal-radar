import io
import json
import sqlite3
import unittest
from urllib.error import HTTPError

from nodeseek_core import (
    bootstrap_required,
    ensure_keywords_schema,
    get_category_config,
    mark_bootstrapped,
    retry_after_seconds,
    set_category_enabled,
    validate_keywords,
)


class BootstrapStateTests(unittest.TestCase):
    def db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE seen_posts (guid TEXT PRIMARY KEY)")
        return db

    def test_empty_database_requires_initial_baseline(self):
        self.assertTrue(bootstrap_required(self.db()))

    def test_existing_seen_posts_are_migrated_without_rebaselining(self):
        db = self.db()
        db.execute("INSERT INTO seen_posts VALUES ('existing')")
        db.commit()
        self.assertFalse(bootstrap_required(db))
        self.assertFalse(bootstrap_required(db))

    def test_explicit_bootstrap_marker_prevents_rebaselining(self):
        db = self.db()
        mark_bootstrapped(db)
        self.assertFalse(bootstrap_required(db))


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
    def test_category_toggle_is_persisted_in_sqlite(self):
        db = sqlite3.connect(":memory:")
        self.assertEqual(get_category_config(db), {})
        set_category_enabled(db, 'trade', False)
        set_category_enabled(db, 'review', True)
        self.assertEqual(get_category_config(db), {'trade': False, 'review': True})

