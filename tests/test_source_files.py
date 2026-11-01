import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.pfm import core
from backend.pfm.core.card_exports import Statement
from backend.pfm.core.parsing import ParsedExport
from backend.pfm.core.source_files import account_folder_name, source_file_name
from tests.test_core import make_csv, transaction

ACCOUNT = "BE00000000000000000001"


class SourceFileTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        os.environ.pop("PFM_HOME", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "finance.sqlite3"
        core.initialize(self.database)
        self.staging = {}

    def import_file(self, contents, account_type="personal", filename="export.csv"):
        preview = core.stage_import(self.database, filename, contents, self.staging)
        return core.commit_import(self.database, preview["token"], account_type, self.staging)

    def export(self, *dates):
        return make_csv(
            [transaction(ACCOUNT, day, "-1,00", "10,00", f"Shop {index}") for index, day in enumerate(dates)]
        ).encode("utf-8")

    def source_files(self):
        folder = self.root / "source-data"
        return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())

    def test_commit_saves_a_copy_named_by_account_and_date_range(self):
        contents = self.export("03/05/2026", "01/05/2026")
        result = self.import_file(contents)
        bank = core.accounts_list(self.database)[0]["label"]
        expected = f"personal/{bank} account 0001/2026-05-01 to 2026-05-03.csv"
        self.assertEqual(result["source_file"], expected)
        self.assertEqual((self.root / "source-data" / expected).read_bytes(), contents)

    def test_copies_are_kept_in_the_backup_folder_when_one_is_set(self):
        backups = self.root / "backups"
        os.environ["PFM_BACKUP_DIR"] = str(backups)
        result = self.import_file(self.export("01/05/2026"))
        self.assertTrue((backups / "source-data" / result["source_file"]).is_file())
        self.assertFalse((self.root / "source-data").exists())

    def test_a_single_card_statement_is_named_by_its_debit_date(self):
        parsed = ParsedExport(
            account="1234565000", bank="Belfius", kind="card", rows=[{"_booking_date": "2024-06-13"}],
            unknown_columns=[], statements=[Statement(key="2025-04-07", debit_date="2025-04-16")],
        )
        self.assertEqual(source_file_name(parsed, b"%PDF-1.7"), "statement 2025-04-16.pdf")
        self.assertEqual(account_folder_name("Belfius", "card", "1234565000"), "Belfius card 5000")

    def test_preview_alone_saves_nothing(self):
        core.stage_import(self.database, "export.csv", self.export("01/05/2026"), self.staging)
        self.assertFalse((self.root / "source-data").exists())

    def test_same_file_is_kept_once_and_a_different_one_never_overwrites(self):
        first = self.export("01/05/2026", "03/05/2026")
        self.import_file(first)
        again = self.import_file(first, filename="export (1).csv")
        other = self.import_file(self.export("03/05/2026", "01/05/2026", "02/05/2026"))
        self.assertTrue(again["source_file"].endswith("2026-05-01 to 2026-05-03.csv"))
        self.assertTrue(other["source_file"].endswith("2026-05-01 to 2026-05-03 (2).csv"))
        self.assertEqual(len(self.source_files()), 2)

    def test_folder_follows_the_account_to_another_account_type(self):
        self.import_file(self.export("01/05/2026"))
        account = core.accounts_list(self.database)[0]
        core.remap_account(self.database, account["id"], "business")
        self.assertEqual(self.source_files(), [f"business/{account['label']} account 0001/2026-05-01.csv"])
        self.assertFalse((self.root / "source-data" / "personal" / f"{account['label']} account 0001").exists())

    def test_import_still_succeeds_when_the_copy_cannot_be_saved(self):
        (self.root / "source-data").write_text("not a folder")
        result = self.import_file(self.export("01/05/2026"))
        self.assertEqual(result["imported"], 1)
        self.assertNotIn("source_file", result)
        self.assertTrue(result["source_file_error"])


if __name__ == "__main__":
    unittest.main()
