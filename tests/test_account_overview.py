import tempfile
import unittest
from pathlib import Path

from backend.pfm import core
from backend.pfm.core.database import connect


class AccountOverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Path(self.temp_dir.name) / "finance.sqlite3"
        core.initialize(self.database)

    def add_account(self, iban, account_type, bank, kind="bank"):
        with connect(self.database) as db:
            return db.execute(
                "INSERT INTO accounts (iban, account_type, created_at, bank, kind) VALUES (?, ?, '2026-01-01', ?, ?)",
                (iban, account_type, bank, kind),
            ).lastrowid

    def add_import(self, account_id, imported_at):
        with connect(self.database) as db:
            return db.execute(
                "INSERT INTO imports (account_id, filename, imported_at, row_count, inserted_count, duplicate_count)"
                " VALUES (?, 'x.csv', ?, 1, 1, 0)",
                (account_id, imported_at),
            ).lastrowid

    def add_transaction(self, account_id, import_id, booking_date, balance_cents, row_number, deleted=False):
        with connect(self.database) as db:
            db.execute(
                """
                INSERT INTO transactions (account_id, import_id, row_number, booking_date, balance_date, amount_cents,
                    source_amount_cents, balance_cents, currency, flow_type, source_json, fingerprint, deleted_at)
                VALUES (?, ?, ?, ?, ?, -100, -100, ?, 'EUR', 'expense', '{}', ?, ?)
                """,
                (account_id, import_id, row_number, booking_date, booking_date, balance_cents,
                 f"{account_id}-{booking_date}-{row_number}", "2026-06-01" if deleted else None),
            )

    def test_summarizes_each_account(self):
        account = self.add_account("BE00000000000000000001", "personal", "KBC")
        early, late = self.add_import(account, "2026-05-01T10:00:00"), self.add_import(account, "2026-06-02T09:00:00")
        self.add_transaction(account, early, "2026-03-05", 50000, 1)
        self.add_transaction(account, late, "2026-06-01", 40000, 1)
        self.add_transaction(account, late, "2026-06-01", 41000, 2)  # same day, later in the export: older
        self.add_transaction(account, late, "2026-06-10", None, 1)  # newest, but no balance
        [item] = core.account_overview(self.database)
        self.assertEqual(item["id"], account)
        self.assertEqual((item["account_type"], item["bank"], item["kind"], item["label"]), ("personal", "KBC", "bank", "KBC"))
        self.assertEqual(item["masked"], core.masked_iban("BE00000000000000000001"))
        self.assertEqual(item["transaction_count"], 4)
        self.assertEqual((item["first_date"], item["last_date"]), ("2026-03-05", "2026-06-10"))
        self.assertEqual(item["latest_balance"], "400.00")
        self.assertEqual(item["last_import_at"], "2026-06-02T09:00:00")

    def test_lists_each_accounts_imports_newest_first_with_counts(self):
        first = self.add_account("BE00000000000000000001", "personal", "KBC")
        kids = self.add_account("BE00000000000000000002", "kids", "Belfius")
        early, late = self.add_import(first, "2026-05-01T10:00:00"), self.add_import(first, "2026-06-02T09:00:00")
        other = self.add_import(kids, "2026-04-01T08:00:00")
        self.add_transaction(first, early, "2026-03-05", None, 1)
        self.add_transaction(first, late, "2026-06-01", None, 1)
        self.add_transaction(first, late, "2026-06-02", None, 2, deleted=True)
        by_id = {item["id"]: item for item in core.account_overview(self.database)}
        self.assertEqual([item["id"] for item in by_id[first]["imports"]], [late, early])
        newest = by_id[first]["imports"][0]
        self.assertEqual(
            (newest["account_id"], newest["filename"], newest["imported_at"], newest["transaction_count"], newest["duplicate_count"]),
            (first, "x.csv", "2026-06-02T09:00:00", 1, 0),
        )
        self.assertEqual([item["id"] for item in by_id[kids]["imports"]], [other])

    def test_account_without_imports_has_an_empty_list(self):
        self.add_account("BE00000000000000000009", "kids", "Belfius", "card")
        [item] = core.account_overview(self.database)
        self.assertEqual(item["imports"], [])

    def test_deleted_transactions_are_excluded(self):
        account = self.add_account("BE00000000000000000001", "personal", "KBC")
        imported = self.add_import(account, "2026-05-01T10:00:00")
        self.add_transaction(account, imported, "2026-03-05", 50000, 1)
        self.add_transaction(account, imported, "2026-04-05", 99900, 1, deleted=True)
        self.add_transaction(account, imported, "2026-01-05", 1000, 1, deleted=True)
        [item] = core.account_overview(self.database)
        self.assertEqual(item["transaction_count"], 1)
        self.assertEqual((item["first_date"], item["last_date"]), ("2026-03-05", "2026-03-05"))
        self.assertEqual(item["latest_balance"], "500.00")

    def test_account_without_transactions_or_imports_has_nulls(self):
        self.add_account("BE00000000000000000009", "kids", "Belfius", "card")
        [item] = core.account_overview(self.database)
        self.assertEqual(item["label"], "Belfius card")
        self.assertEqual(item["transaction_count"], 0)
        for key in ("first_date", "last_date", "latest_balance", "last_import_at"):
            self.assertIsNone(item[key])

    def test_card_without_balance_has_no_latest_balance(self):
        card = self.add_account("CARD-1", "personal", "KBC", "card")
        imported = self.add_import(card, "2026-05-01T10:00:00")
        self.add_transaction(card, imported, "2026-04-05", None, 1)
        [item] = core.account_overview(self.database)
        self.assertEqual(item["transaction_count"], 1)
        self.assertIsNone(item["latest_balance"])

    def test_orders_by_entity_bank_kind_and_label(self):
        ids = {
            "kids": self.add_account("BE1", "kids", "KBC"),
            "business": self.add_account("BE2", "business", "Belfius"),
            "p-kbc-card": self.add_account("BE3", "personal", "KBC", "card"),
            "p-kbc": self.add_account("BE4", "personal", "KBC"),
            "p-belfius-card": self.add_account("BE5", "personal", "Belfius", "card"),
            "p-belfius": self.add_account("BE6", "personal", "Belfius"),
        }
        order = [item["id"] for item in core.account_overview(self.database)]
        self.assertEqual(order, [ids[key] for key in ("p-belfius", "p-belfius-card", "p-kbc", "p-kbc-card", "business", "kids")])


if __name__ == "__main__":
    unittest.main()
