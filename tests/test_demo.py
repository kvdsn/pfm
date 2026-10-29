import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from backend.pfm import core
from backend.pfm.demo import build_demo

TODAY = date(2026, 10, 30)


class DemoTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        os.environ.pop("PFM_HOME", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.folder = Path(self.temp_dir.name) / "demo"
        self.database = build_demo(self.folder, today=TODAY)

    def test_every_entity_gets_accounts_and_mostly_categorized_transactions(self):
        with core.connect(self.database) as db:
            rows = db.execute(
                "SELECT a.account_type, COUNT(*) AS total, MAX(t.booking_date) AS latest, "
                "SUM(c.name = 'Uncategorized' AND t.counterparty_iban = '') AS uncategorized "
                "FROM transactions t JOIN accounts a ON a.id = t.account_id "
                "JOIN categories c ON c.id = t.category_id GROUP BY a.account_type"
            ).fetchall()
        found = {row["account_type"]: row for row in rows}
        self.assertEqual(set(found), {"personal", "business", "kids"})
        for row in found.values():
            self.assertGreater(row["total"], 20)
            self.assertLessEqual(row["latest"], TODAY.isoformat())
            self.assertLess(row["uncategorized"] / row["total"], 0.1)

    def test_the_forecast_and_reports_work(self):
        forecast = core.forecast(self.database, today=TODAY)
        self.assertEqual(forecast["business"]["months"][-1]["month"], "2027-12")
        self.assertTrue(any(line["source"] == "consulting" for line in forecast["business"]["months"][1]["lines"]))
        report = core.report(self.database, "personal", today=TODAY)
        self.assertIn("2026-10", [summary["month"] for summary in report["summaries"]])

    def test_the_same_seed_gives_the_same_data_and_an_existing_database_is_left_alone(self):
        other = build_demo(Path(self.temp_dir.name) / "again", today=TODAY)
        query = "SELECT booking_date, amount_cents FROM transactions ORDER BY id"
        with core.connect(self.database) as first, core.connect(other) as second:
            self.assertEqual(first.execute(query).fetchall(), second.execute(query).fetchall())
        with self.assertRaises(FileExistsError):
            build_demo(self.folder, today=TODAY)


if __name__ == "__main__":
    unittest.main()
