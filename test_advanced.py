"""Regression tests for durable delivery and per-source scheduling."""
import sqlite3
import unittest

from nodeseek_core import (
    ensure_notification_schema,
    enqueue_notification,
    claim_due_notifications,
    mark_notification_sent,
    mark_notification_retry,
    source_is_enabled,
    set_source_enabled,
)


class NotificationQueueTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        ensure_notification_schema(self.db)

    def test_pending_notification_can_be_claimed_then_marked_sent(self):
        nid = enqueue_notification(self.db, "sbsb", "guid-1", "hello", "{}")
        rows = claim_due_notifications(self.db, limit=5)
        self.assertEqual([(r[0], r[1], r[2]) for r in rows], [(nid, "sbsb", "guid-1")])
        mark_notification_sent(self.db, nid)
        self.assertEqual(claim_due_notifications(self.db, limit=5), [])

    def test_failed_notification_is_retried_not_lost(self):
        nid = enqueue_notification(self.db, "idcflare", "guid-2", "hello", "{}")
        claim_due_notifications(self.db, limit=1)
        mark_notification_retry(self.db, nid, "network", retry_delay=0)
        rows = claim_due_notifications(self.db, limit=1)
        self.assertEqual(rows[0][0], nid)
        self.assertEqual(rows[0][6], 2)  # attempt count


class SourceControlTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def test_source_switch_defaults_enabled_and_persists(self):
        self.assertTrue(source_is_enabled(self.db, "sbsb"))
        set_source_enabled(self.db, "sbsb", False)
        self.assertFalse(source_is_enabled(self.db, "sbsb"))
        set_source_enabled(self.db, "sbsb", True)
        self.assertTrue(source_is_enabled(self.db, "sbsb"))


if __name__ == "__main__":
    unittest.main()
