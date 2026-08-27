import sqlite3
import unittest

from nodeseek_core import get_enabled_sources, set_enabled_sources


class SourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def test_can_enable_only_one_source(self):
        set_enabled_sources(self.db, {"sbsb"})
        self.assertEqual(get_enabled_sources(self.db), {"sbsb"})

    def test_can_enable_any_two_sources(self):
        set_enabled_sources(self.db, {"nodeseek", "idcflare"})
        self.assertEqual(get_enabled_sources(self.db), {"nodeseek", "idcflare"})

    def test_rejects_unknown_source(self):
        with self.assertRaises(ValueError):
            set_enabled_sources(self.db, {"unknown"})


if __name__ == "__main__":
    unittest.main()
