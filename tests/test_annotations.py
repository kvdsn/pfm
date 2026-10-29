import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.pfm import core
from backend.pfm.server import create_app
from tests.test_core import make_csv, transaction

ACCOUNT = "BE00000000000000000001"
BUSINESS = "BE00000000000000000002"
ANNOTATION_COLUMNS = ("note", "follow_up", "follow_up_at")


class AnnotationFixture(unittest.TestCase):
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
        self.client = TestClient(create_app(self.database, self.root / "dist"))
        self.addCleanup(self.client.close)
        self.import_rows("personal", [
            transaction(ACCOUNT, "10/03/2024", "-30,00", "970,00", "Hema", "ONE"),
            transaction(ACCOUNT, "05/01/2025", "-20,00", "950,00", "Bol", "TWO"),
            transaction(ACCOUNT, "02/05/2026", "-10,00", "940,00", "Shell", "THREE"),
            transaction(ACCOUNT, "03/05/2026", "500,00", "1440,00", "Salary", "FOUR"),
        ])
        self.import_rows("business", [transaction(BUSINESS, "04/05/2026", "-99,00", "901,00", "Tools", "FIVE")])

    def import_rows(self, account_type, rows):
        staging = {}
        preview = core.stage_import(self.database, "export.csv", make_csv(rows).encode(), staging)
        core.commit_import(self.database, preview["token"], account_type, staging)

    def tid(self, description):
        with core.connect(self.database) as db:
            return db.execute(
                "SELECT id FROM transactions WHERE json_extract(source_json, '$.Omschrijving') = ?", (description,)
            ).fetchone()[0]

    def annotate(self, description, **changes):
        return self.client.patch(f"/api/transactions/{self.tid(description)}/annotation", json=changes)

    def listing(self, **params):
        response = self.client.get("/api/transactions", params={"account_type": "personal", **params})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def descriptions(self, **params):
        return [item["description"] for item in self.listing(**params)["items"]]


class AnnotateTests(AnnotationFixture):
    def test_note_is_trimmed_saved_and_cleared(self):
        self.assertEqual(
            self.annotate("ONE", note="  ask employer  ").json(),
            {"note": "ask employer", "follow_up": None, "follow_up_at": None},
        )
        row = next(item for item in self.listing()["items"] if item["description"] == "ONE")
        self.assertEqual(row["note"], "ask employer")
        self.assertEqual(self.annotate("ONE", note="   ").json()["note"], "")

    def test_follow_up_lifecycle_and_timestamp(self):
        opened = self.annotate("ONE", follow_up="open").json()
        self.assertEqual(opened["follow_up"], "open")
        self.assertTrue(opened["follow_up_at"])
        # The same state again keeps the original time; a note-only change too.
        self.assertEqual(self.annotate("ONE", follow_up="open").json(), opened)
        self.assertEqual(self.annotate("ONE", note="x").json()["follow_up_at"], opened["follow_up_at"])
        done = self.annotate("ONE", follow_up="done").json()
        self.assertEqual(done["follow_up"], "done")
        self.assertGreaterEqual(done["follow_up_at"], opened["follow_up_at"])
        reopened = self.annotate("ONE", follow_up="open").json()
        self.assertEqual(reopened["follow_up"], "open")
        self.assertGreaterEqual(reopened["follow_up_at"], done["follow_up_at"])
        cleared = self.annotate("ONE", follow_up=None).json()
        self.assertEqual((cleared["follow_up"], cleared["follow_up_at"]), (None, None))
        self.assertEqual(cleared["note"], "x")

    def test_rejects_bad_input(self):
        self.assertEqual(self.annotate("ONE", note="x" * 501).status_code, 400)
        self.assertEqual(self.annotate("ONE", note="x" * 500).status_code, 200)
        self.assertEqual(self.annotate("ONE", follow_up="pending").status_code, 422)
        self.assertEqual(self.annotate("ONE").status_code, 400)
        with self.assertRaises(ValueError):
            core.annotate_transaction(self.database, self.tid("ONE"), {"follow_up": "pending"})
        with self.assertRaises(ValueError):
            core.annotate_transaction(self.database, self.tid("ONE"), {"category_id": 1})

    def test_unknown_and_removed_transactions_are_not_found(self):
        self.assertEqual(self.client.patch("/api/transactions/9999/annotation", json={"note": "x"}).status_code, 404)
        old = self.tid("ONE")
        core.remove_transactions_before(self.database, "2025-01-01")
        response = self.client.patch(f"/api/transactions/{old}/annotation", json={"note": "x"})
        self.assertEqual(response.status_code, 404)

    def test_leaves_category_and_totals_alone(self):
        def snapshot():
            with core.connect(self.database) as db:
                rows = [tuple(r) for r in db.execute(
                    "SELECT id, category_id, flow_type, category_source, category_rule_id FROM transactions ORDER BY id"
                )]
            return rows, core.report(self.database, "personal")

        before = snapshot()
        self.annotate("TWO", note="claim it", follow_up="open")
        self.annotate("TWO", follow_up="done")
        self.annotate("FOUR", note="income note")
        self.assertEqual(snapshot(), before)


class FollowUpFilterTests(AnnotationFixture):
    def test_filter_ignores_year_and_orders_open_by_date(self):
        self.annotate("THREE", follow_up="open")
        self.annotate("ONE", follow_up="open")
        self.annotate("TWO", follow_up="done")
        for params in ({"year": "2026"}, {"month": "2026-05"}, {}):
            listing = self.listing(follow_up="open", **params)
            self.assertEqual([i["description"] for i in listing["items"]], ["ONE", "THREE"])
            self.assertEqual(listing["total"], 2)
        self.assertEqual(self.descriptions(year="2026"), ["FOUR", "THREE"])

    def test_done_is_most_recently_done_first(self):
        for name in ("ONE", "TWO", "THREE"):
            self.annotate(name, follow_up="done")
        self.annotate("TWO", follow_up="open")
        self.annotate("TWO", follow_up="done")
        self.assertEqual(self.descriptions(follow_up="done"), ["TWO", "THREE", "ONE"])

    def test_entities_stay_separate_and_deleted_rows_are_excluded(self):
        self.annotate("ONE", follow_up="open")
        self.annotate("TWO", follow_up="open")
        self.annotate("FIVE", follow_up="open")
        self.assertEqual(self.listing(follow_up="open")["open_follow_ups"], 2)
        business = self.client.get("/api/transactions", params={"account_type": "business", "follow_up": "open"}).json()
        self.assertEqual([i["description"] for i in business["items"]], ["FIVE"])
        self.assertEqual(business["open_follow_ups"], 1)
        core.remove_transactions_before(self.database, "2025-01-01")
        self.assertEqual(self.descriptions(follow_up="open"), ["TWO"])
        self.assertEqual(self.listing()["open_follow_ups"], 1)
        self.assertEqual(core.count_open_follow_ups(self.database, "personal"), 1)

    def test_count_ignores_other_filters_and_done_items(self):
        self.annotate("ONE", follow_up="open")
        self.annotate("TWO", follow_up="done")
        listing = self.listing(year="2026", q="nothing matches")
        self.assertEqual((listing["total"], listing["open_follow_ups"]), (0, 1))

    def test_search_and_paging_still_apply(self):
        self.annotate("ONE", follow_up="open")
        self.annotate("TWO", follow_up="open")
        self.annotate("THREE", follow_up="open")
        self.assertEqual(self.descriptions(follow_up="open", q="bol"), ["TWO"])
        page = self.listing(follow_up="open", limit=1, offset=1)
        self.assertEqual((page["total"], [i["description"] for i in page["items"]]), (3, ["TWO"]))

    def test_invalid_filter_value(self):
        self.assertEqual(
            self.client.get("/api/transactions", params={"account_type": "personal", "follow_up": "all"}).status_code, 422
        )
        with self.assertRaises(ValueError):
            core.list_transactions(self.database, "personal", None, None, "", 10, 0, follow_up="all")

    def test_search_finds_the_note(self):
        self.annotate("TWO", note="reimbursement from employer")
        self.assertEqual(self.descriptions(q="employer"), ["TWO"])
        self.assertEqual(self.descriptions(q="employer reimbursement"), [])  # no such phrase
        self.assertEqual(self.descriptions(q="reimbursement employer"), [])
        self.annotate("TWO", note="")
        self.assertEqual(self.descriptions(q="employer"), [])


class UndoCountTests(AnnotationFixture):
    def test_overview_counts_annotated_transactions_per_import(self):
        self.annotate("ONE", note="x")
        self.annotate("TWO", follow_up="done")
        self.annotate("THREE", note="y")
        self.annotate("THREE", note="")
        core.remove_transactions_before(self.database, "2024-12-01")  # hides ONE
        by_label = {}
        for account in core.account_overview(self.database):
            for record in account["imports"]:
                by_label[account["account_type"]] = record["annotated"]
        self.assertEqual(by_label, {"personal": 1, "business": 0})


class MigrationTests(unittest.TestCase):
    def test_old_database_gains_the_columns_with_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "finance.sqlite3"
            core.initialize(path)
            account = None
            with core.connect(path) as db:
                account = db.execute(
                    "INSERT INTO accounts (iban, account_type, created_at) VALUES ('BE1', 'personal', '2026-01-01')"
                ).lastrowid
                imported = db.execute(
                    "INSERT INTO imports (account_id, filename, imported_at, row_count, inserted_count, duplicate_count)"
                    " VALUES (?, 'x.csv', '2026-01-01', 1, 1, 0)", (account,)
                ).lastrowid
                db.execute(
                    "INSERT INTO transactions (account_id, import_id, row_number, booking_date, balance_date,"
                    " amount_cents, source_amount_cents, currency, flow_type, source_json, fingerprint)"
                    " VALUES (?, ?, 1, '2026-01-01', '2026-01-01', -100, -100, 'EUR', 'expense', '{}', 'f')",
                    (account, imported),
                )
                db.execute("DROP INDEX transactions_follow_up_idx")
                for column in ANNOTATION_COLUMNS:
                    db.execute(f"ALTER TABLE transactions DROP COLUMN {column}")
            with closing(sqlite3.connect(path)) as db:
                self.assertFalse(set(ANNOTATION_COLUMNS) & {r[1] for r in db.execute("PRAGMA table_info(transactions)")})

            backup = core.initialize(path, Path(folder))

            self.assertIsNotNone(backup)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT note, follow_up, follow_up_at FROM transactions").fetchall(), [("", None, None)])
                indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
                self.assertIn("transactions_follow_up_idx", indexes)
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE transactions SET follow_up = 'bogus'")
            self.assertIsNone(core.initialize(path, Path(folder)))


if __name__ == "__main__":
    unittest.main()
