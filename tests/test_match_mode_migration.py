import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.pfm import core
from backend.pfm.core import database
from tests.test_kids import OLD_MODE_CHECK, build_old_database, dump


class MatchModeMigrationTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        os.environ.pop("PFM_HOME", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.folder = Path(self.temp_dir.name)
        self.path = self.folder / "finance.sqlite3"

    def backups(self):
        return list(self.folder.glob("finance-backup-*-before-upgrade*.sqlite3"))

    def table_sql(self, name, path=None):
        with closing(sqlite3.connect(path or self.path)) as db:
            return db.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]

    def insert_word_rule(self):
        with core.connect(self.path) as db:
            db.execute(
                "INSERT INTO category_rules(account_type, category_id, match_text, match_fields, flow_type, priority, match_mode)"
                " VALUES ('personal', 9, 'zara', '[\"merchant\"]', 'expense', 9, 'word')"
            )

    def test_the_schema_lists_every_match_mode(self):
        self.assertEqual(database.MATCH_MODES, ("contains", "word", "exact"))
        core.initialize(self.path)
        self.assertIn(database.MATCH_MODE_CHECK, self.table_sql("category_rules"))
        self.assertEqual(self.backups(), [])

    def test_old_match_mode_check_is_detected_and_rebuilt_with_a_backup(self):
        build_old_database(self.path, old_account_types=False)
        before = dump(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(database._stale_account_type_tables(db), [])
            self.assertEqual(database._stale_check_tables(db), ["category_rules"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_word_rule()

        backup = core.initialize(self.path)

        self.assertIsNotNone(backup)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(dump(backup), before)
        self.assertIn(OLD_MODE_CHECK, self.table_sql("category_rules", backup))
        after = dump(self.path)
        after["categories"] = [row for row in after["categories"] if row["account_type"] != "kids"]
        self.assertEqual(after, before)
        self.assertIn(database.MATCH_MODE_CHECK, self.table_sql("category_rules"))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            self.assertIn("category_rules_account_priority_idx", indexes)

        self.insert_word_rule()
        with core.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE category_rules SET match_mode = 'regex' WHERE id = 3")

        settled = dump(self.path)
        self.assertIsNone(core.initialize(self.path))
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(dump(self.path), settled)

    def test_both_old_checks_are_upgraded_in_one_go(self):
        build_old_database(self.path)
        before = dump(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(database._stale_check_tables(db), ["accounts", "categories", "category_rules"])
            self.assertIn(OLD_MODE_CHECK, self.table_sql("category_rules"))

        backup = core.initialize(self.path)

        self.assertIsNotNone(backup)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(dump(backup), before)
        after = dump(self.path)
        after["categories"] = [row for row in after["categories"] if row["account_type"] != "kids"]
        self.assertEqual(after, before)
        for table in ("accounts", "categories", "category_rules"):
            self.assertIn("'kids'", self.table_sql(table))
        self.assertIn(database.MATCH_MODE_CHECK, self.table_sql("category_rules"))
        self.insert_word_rule()
        self.assertIsNone(core.initialize(self.path))
        self.assertEqual(len(self.backups()), 1)


if __name__ == "__main__":
    unittest.main()
