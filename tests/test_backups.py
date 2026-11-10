import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend.pfm import core


class BackupTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp_dir.name)
        self.database = self.folder / "finance.sqlite3"
        core.initialize(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def backup_at(self, moment, label=""):
        with patch("backend.pfm.core.database.datetime") as fake:
            fake.now.return_value = moment
            return core.backup_database(self.database, label)

    def test_a_backup_that_fails_its_check_is_removed(self):
        backup_dir = self.folder / "backups"
        with patch("backend.pfm.core.database._quick_check", return_value="database disk image is malformed"):
            with self.assertRaisesRegex(RuntimeError, "integrity check"):
                core.backup_database(self.database, "x", backup_dir)
        self.assertEqual(list(backup_dir.glob("*.sqlite3")), [])

    def test_backup_without_label_keeps_the_existing_name(self):
        path = self.backup_at(datetime(2026, 10, 17, 18, 24, 20))
        self.assertEqual(path.name, "finance-backup-20261017-182420.sqlite3")

    def test_label_is_slugged_into_the_name(self):
        moment = datetime(2026, 10, 17, 18, 24, 20)
        self.assertEqual(
            self.backup_at(moment, "Before rule edits!").name,
            "finance-backup-20261017-182420-before-rule-edits.sqlite3",
        )
        long_label = self.backup_at(datetime(2026, 10, 17, 18, 24, 21), "x" * 50 + " tail")
        self.assertEqual(long_label.name, f"finance-backup-20261017-182421-{'x' * 40}.sqlite3")
        self.assertEqual(self.backup_at(datetime(2026, 10, 17, 18, 24, 22), "  !!  ").name,
                         "finance-backup-20261017-182422.sqlite3")

    def test_backups_in_the_same_second_do_not_overwrite(self):
        moment = datetime(2026, 10, 17, 18, 24, 20)
        names = [self.backup_at(moment).name for _ in range(3)]
        self.assertEqual(names, [
            "finance-backup-20261017-182420.sqlite3",
            "finance-backup-20261017-182420-2.sqlite3",
            "finance-backup-20261017-182420-3.sqlite3",
        ])
        listed = core.list_backups(self.database)
        self.assertEqual({item["label"] for item in listed}, {""})

    def test_list_backups_newest_first_with_fields(self):
        older = self.backup_at(datetime(2026, 1, 2, 3, 4, 5), "Before removing 2024")
        self.backup_at(datetime(2026, 10, 17, 18, 24, 20))
        (self.folder / "finance-backup-20260101-000000.sqlite3-wal").write_text("x")
        (self.folder / "finance-backup-20260101-000000.txt").write_text("x")
        (self.folder / "other-backup-20260101-000000.sqlite3").write_text("x")
        (self.folder / "finance-backup-20260102-000000.sqlite3").mkdir()
        (self.folder / "finance-backup-notes.sqlite3").write_text("odd")
        backups = core.list_backups(self.database)
        self.assertEqual(len(backups), 3)
        # A name without a timestamp falls back to the file's modification time, so it sorts as newest.
        self.assertEqual(backups[0]["name"], "finance-backup-notes.sqlite3")
        self.assertEqual(backups[0]["label"], "notes")
        self.assertEqual(
            [item["created_at"] for item in backups[1:]], ["2026-10-17T18:24:20", "2026-01-02T03:04:05"]
        )
        self.assertEqual(backups[2]["name"], older.name)
        self.assertEqual(backups[2]["label"], "before removing 2024")
        self.assertEqual(backups[2]["size_bytes"], older.stat().st_size)

    def test_backup_is_a_valid_database_with_the_same_transactions(self):
        path = core.backup_database(self.database, "check")
        with closing(sqlite3.connect(path)) as copy, closing(sqlite3.connect(self.database)) as original:
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            count = "SELECT COUNT(*) FROM transactions"
            self.assertEqual(copy.execute(count).fetchone(), original.execute(count).fetchone())

    def category_names(self, path):
        with closing(sqlite3.connect(path)) as db:
            return {row[0] for row in db.execute("SELECT name FROM categories")}

    def test_restore_replaces_the_database_with_the_backup(self):
        core.add_category(self.database, "personal", "Hobbies")
        backup = core.backup_database(self.database, "keep")
        core.add_category(self.database, "personal", "Later")
        entry = core.restore_backup(self.database, backup.name)
        self.assertEqual(entry["name"], backup.name)
        names = self.category_names(self.database)
        self.assertIn("Hobbies", names)
        self.assertNotIn("Later", names)

    def test_restore_rejects_unknown_and_traversal_names(self):
        core.add_category(self.database, "personal", "Hobbies")
        for name in ("../finance.sqlite3", "finance.sqlite3", "finance-backup-missing.sqlite3", "/etc/passwd"):
            with self.assertRaises(LookupError):
                core.restore_backup(self.database, name)
        self.assertIn("Hobbies", self.category_names(self.database))

    def test_restore_rejects_unusable_backups_and_keeps_the_live_data(self):
        core.add_category(self.database, "personal", "Hobbies")
        (self.folder / "finance-backup-20260101-000000.sqlite3").write_text("not a database")
        with closing(sqlite3.connect(self.folder / "finance-backup-20260102-000000.sqlite3")) as db:
            db.execute("CREATE TABLE unrelated(id INTEGER)")
            db.commit()
        (self.folder / "finance-backup-20260103-000000.sqlite3").write_bytes(b"")
        for name in (
            "finance-backup-20260101-000000.sqlite3",
            "finance-backup-20260102-000000.sqlite3",
            "finance-backup-20260103-000000.sqlite3",
        ):
            with self.assertRaisesRegex(ValueError, "not a usable backup"):
                core.restore_backup(self.database, name)
        self.assertIn("Hobbies", self.category_names(self.database))

    def test_restore_upgrades_an_older_backup(self):
        backup = core.backup_database(self.database, "old")
        with closing(sqlite3.connect(backup)) as db:
            db.execute("DROP INDEX transactions_replaced_idx")
            db.execute("ALTER TABLE transactions DROP COLUMN replaced_by_statement_id")
            db.commit()
        core.restore_backup(self.database, backup.name)
        with closing(sqlite3.connect(self.database)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(transactions)")}
        self.assertIn("replaced_by_statement_id", columns)

    def test_restore_reads_from_the_backup_folder_with_special_characters(self):
        target = self.folder / "me@example.com" / "my backups"
        core.add_category(self.database, "personal", "Hobbies")
        backup = core.backup_database(self.database, backup_dir=target)
        core.add_category(self.database, "personal", "Later")
        core.restore_backup(self.database, backup.name, target)
        self.assertNotIn("Later", self.category_names(self.database))

    def test_default_backup_dir_is_the_database_folder(self):
        self.assertEqual(core.default_backup_dir(self.database), self.folder)
        self.assertEqual(core.backup_database(self.database).parent, self.folder)

    def test_backup_dir_setting_is_used_for_create_and_list(self):
        target = self.folder / "synced" / "backups"
        os.environ["PFM_BACKUP_DIR"] = str(target)
        self.assertEqual(core.default_backup_dir(self.database), target)
        path = core.backup_database(self.database, "check")
        self.assertEqual(path.parent, target)
        self.assertTrue(path.name.startswith("finance-backup-"))
        self.assertEqual([item["name"] for item in core.list_backups(self.database)], [path.name])
        self.assertEqual(list(self.folder.glob("finance-backup-*")), [])

    def test_backups_next_to_the_database_are_not_listed_when_a_folder_is_set(self):
        core.backup_database(self.database)
        os.environ["PFM_BACKUP_DIR"] = str(self.folder / "elsewhere")
        self.assertEqual(core.list_backups(self.database), [])

    def test_explicit_backup_dir_overrides_the_setting(self):
        os.environ["PFM_BACKUP_DIR"] = str(self.folder / "from-env")
        explicit = self.folder / "explicit"
        path = core.backup_database(self.database, backup_dir=explicit)
        self.assertEqual(path.parent, explicit)
        self.assertEqual(len(core.list_backups(self.database, explicit)), 1)
        self.assertEqual(core.list_backups(self.database), [])

    def test_upgrade_backup_honors_the_backup_dir(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP INDEX transactions_replaced_idx")
            db.execute("ALTER TABLE transactions DROP COLUMN replaced_by_statement_id")
            db.commit()
        target = self.folder / "synced"
        backup = core.initialize(self.database, target)
        self.assertIsNotNone(backup)
        self.assertEqual(backup.parent, target)

    def make_files(self, count, label, first_day=1, month=1):
        for index in range(count):
            self.backup_at(datetime(2026, month, first_day + index, 12, 0, 0), label)

    def test_listing_marks_only_automatic_notes(self):
        self.backup_at(datetime(2026, 1, 1, 12, 0, 0), "before import")
        self.backup_at(datetime(2026, 1, 1, 12, 0, 0), "before import")
        self.backup_at(datetime(2026, 1, 2, 12, 0, 0), "before import of 2024")
        self.backup_at(datetime(2026, 1, 3, 12, 0, 0))
        flags = {item["name"]: item["automatic"] for item in core.list_backups(self.database)}
        self.assertEqual(flags, {
            "finance-backup-20260101-120000-before-import.sqlite3": True,
            "finance-backup-20260101-120000-before-import-2.sqlite3": True,
            "finance-backup-20260102-120000-before-import-of-2024.sqlite3": False,
            "finance-backup-20260103-120000.sqlite3": False,
        })

    def test_every_automatic_label_is_recognised(self):
        for index, label in enumerate(sorted(core.AUTOMATIC_BACKUP_LABELS)):
            self.backup_at(datetime(2026, 2, index + 1, 12, 0, 0), label)
        listed = core.list_backups(self.database)
        self.assertEqual(len(listed), 8)
        self.assertTrue(all(item["automatic"] for item in listed))

    def test_prune_keeps_the_newest_automatic_and_all_manual_backups(self):
        self.make_files(25, "before import", month=1)
        manual = [self.backup_at(datetime(2025, 12, day, 12, 0, 0), "mine").name for day in (1, 2, 3)]
        manual.append(self.backup_at(datetime(2025, 12, 4, 12, 0, 0)).name)
        automatic = [item for item in core.list_backups(self.database) if item["automatic"]]
        expected_deleted = {item["name"] for item in automatic[20:]}
        deleted = core.prune_automatic_backups(self.database)
        self.assertEqual(set(deleted), expected_deleted)
        self.assertEqual(len(deleted), 5)
        remaining = core.list_backups(self.database)
        self.assertEqual(sum(item["automatic"] for item in remaining), 20)
        self.assertEqual({item["name"] for item in remaining if not item["automatic"]}, set(manual))
        self.assertEqual(min(item["created_at"] for item in remaining if item["automatic"]), "2026-01-06T12:00:00")
        self.assertEqual(core.prune_automatic_backups(self.database), [])

    def test_prune_honours_keep_and_the_backup_dir(self):
        target = self.folder / "elsewhere"
        for day in range(1, 6):
            with patch("backend.pfm.core.database.datetime") as fake:
                fake.now.return_value = datetime(2026, 3, day, 12, 0, 0)
                core.backup_database(self.database, "before restoring", target)
        self.assertEqual(len(core.prune_automatic_backups(self.database, keep=2, backup_dir=target)), 3)
        self.assertEqual(len(core.list_backups(self.database, target)), 2)

    def test_prune_never_touches_other_files(self):
        self.make_files(22, "before import")
        others = [
            self.folder / "finance-backup-20200101-000000-before-import.sqlite3-wal",
            self.folder / "finance-backup-20200101-000000-before-import.txt",
            self.folder / "other-backup-20200101-000000-before-import.sqlite3",
            self.folder / "notes.md",
        ]
        for path in others:
            path.write_text("x")
        folder = self.folder / "finance-backup-20200101-000000-before-import.sqlite3"
        folder.mkdir()
        self.assertEqual(len(core.prune_automatic_backups(self.database)), 2)
        self.assertTrue(all(path.exists() for path in others))
        self.assertTrue(folder.is_dir())
        self.assertTrue(self.database.exists())

    def test_upgrade_backup_is_labelled_automatic_and_old_ones_are_pruned(self):
        self.make_files(20, "before import", month=2)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP INDEX transactions_replaced_idx")
            db.execute("ALTER TABLE transactions DROP COLUMN replaced_by_statement_id")
            db.commit()
        backup = core.initialize(self.database)
        self.assertIn("before-upgrade", backup.name)
        listed = core.list_backups(self.database)
        upgrade = next(item for item in listed if item["name"] == backup.name)
        self.assertEqual((upgrade["label"], upgrade["automatic"]), ("before upgrade", True))
        self.assertEqual(len(listed), 20)

    def test_reserved_notes(self):
        for note in ("before import", "Before Import!", "before upgrade", "before import 2"):
            self.assertTrue(core.is_reserved_note(note), note)
        for note in ("", "mine", "before import of 2024", "after import"):
            self.assertFalse(core.is_reserved_note(note), note)


if __name__ == "__main__":
    unittest.main()
