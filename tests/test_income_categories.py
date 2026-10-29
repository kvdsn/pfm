import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.pfm import core
from backend.pfm.server import create_app

OWN = "BE00000000000000000001"
OTHER_OWN = "BE00000000000000000002"


class IncomeFixture(unittest.TestCase):
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
        with core.connect(self.database) as db:
            self.account = self.add_account(db, OWN)
            self.import_id = db.execute(
                "INSERT INTO imports(account_id, filename, imported_at, row_count, inserted_count, duplicate_count)"
                " VALUES (?, 'x.csv', '2026-01-01T00:00:00', 0, 0, 0)",
                (self.account,),
            ).lastrowid
        self.rows = 0
        self.groceries = core.add_category(self.database, "personal", "Groceries")["id"]
        self.salary = core.add_category(self.database, "personal", "Salary")["id"]
        core.set_category_income(self.database, self.salary, True)

    def add_account(self, db, iban, account_type="personal"):
        return db.execute(
            "INSERT INTO accounts(iban, account_type, created_at) VALUES (?, ?, '2026-01-01')", (iban, account_type)
        ).lastrowid

    def add(self, category_id, cents, date="2026-03-05", counterparty="", replaced_by=None, deleted=False):
        """Insert a transaction directly; flow_type is stored as the old model would have, to show it is ignored."""
        self.rows += 1
        with core.connect(self.database) as db:
            return db.execute(
                """
                INSERT INTO transactions(account_id, import_id, row_number, booking_date, balance_date,
                    amount_cents, source_amount_cents, currency, merchant, counterparty_iban, category_id,
                    flow_type, source_json, fingerprint, deleted_at, replaced_by_statement_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'EUR', ?, ?, ?, 'transfer', '{}', ?, ?, ?)
                """,
                (self.account, self.import_id, self.rows, date, date, cents, cents, f"m{self.rows}",
                 counterparty, category_id, f"fp{self.rows}", "2026-06-01" if deleted else None, replaced_by),
            ).lastrowid

    def uncategorized(self):
        with core.connect(self.database) as db:
            return core.get_category_id(db, "personal", core.UNCATEGORIZED)

    def summary(self):
        return core.report(self.database, "personal")["summaries"][0]

    def totals(self):
        categories = {(c["flow_type"], c["name"]): c["amount"] for c in self.summary()["categories"]}
        return categories


class IncomeCategoryTests(IncomeFixture):
    def test_refund_lowers_expense_and_total_can_go_negative(self):
        self.add(self.groceries, -5000)
        self.add(self.groceries, 1500)
        self.assertEqual(self.totals(), {("expense", "Groceries"): "35.00"})
        summary = self.summary()
        self.assertEqual((summary["income"], summary["expenses"], summary["net"]), ("0.00", "35.00", "-35.00"))
        self.add(self.groceries, 6000)
        self.assertEqual(self.totals(), {("expense", "Groceries"): "-25.00"})
        self.assertEqual((self.summary()["expenses"], self.summary()["net"]), ("-25.00", "25.00"))

    def test_negative_amount_in_income_category_lowers_income(self):
        self.add(self.salary, 200000)
        self.add(self.salary, -30000)
        self.assertEqual(self.totals(), {("income", "Salary"): "1700.00"})
        summary = self.summary()
        self.assertEqual((summary["income"], summary["expenses"], summary["net"]), ("1700.00", "0.00", "1700.00"))

    def test_uncategorized_keeps_the_sign_rule(self):
        uncategorized = self.uncategorized()
        self.add(uncategorized, 1000)
        self.add(uncategorized, -400)
        self.add(None, -250)
        self.assertEqual(
            self.totals(), {("income", "Uncategorized"): "10.00", ("expense", "Uncategorized"): "6.50"}
        )
        summary = self.summary()
        self.assertEqual((summary["income"], summary["expenses"]), ("10.00", "6.50"))

    def test_each_category_appears_in_one_group(self):
        self.add(self.groceries, -1000)
        self.add(self.groceries, 500)
        self.add(self.salary, 3000)
        self.add(self.salary, -100)
        self.assertEqual(
            self.totals(), {("expense", "Groceries"): "5.00", ("income", "Salary"): "29.00"}
        )

    def test_own_transfers_and_itemized_settlements_are_excluded(self):
        with core.connect(self.database) as db:
            self.add_account(db, OTHER_OWN)
            statement = db.execute(
                "INSERT INTO card_statements(account_id, statement_key, debit_date) VALUES (?, 's', '2026-03-05')",
                (self.account,),
            ).lastrowid
        self.add(self.groceries, -1000)
        self.add(self.groceries, -9999, counterparty=OTHER_OWN)
        self.add(self.salary, 9999, counterparty=OTHER_OWN)
        self.add(self.groceries, -7777, replaced_by=statement)
        self.add(self.groceries, 100, deleted=True)
        summary = self.summary()
        self.assertEqual(self.totals(), {("expense", "Groceries"): "10.00"})
        self.assertEqual((summary["income"], summary["expenses"], summary["own_transfers"]), ("0.00", "10.00", "99.99"))
        self.assertEqual(summary["transaction_count"], 1)

    def test_listing_by_flow_matches_report_rows(self):
        uncategorized = self.uncategorized()
        self.add(self.groceries, -5000)
        self.add(self.groceries, 6000)
        self.add(self.salary, 20000)
        self.add(self.salary, -3000)
        self.add(uncategorized, 700)
        self.add(uncategorized, -300)
        self.add(None, -200)
        with core.connect(self.database) as db:
            self.add_account(db, OTHER_OWN)
        self.add(self.groceries, -123, counterparty=OTHER_OWN)
        for entry in self.summary()["categories"]:
            category_id = entry["category_id"]
            listed = core.list_transactions(
                self.database, "personal", "2026-03", None, "", 100, 0,
                category_filter="uncategorized" if category_id is None else str(category_id),
                flow_type_filter=entry["flow_type"],
            )["items"]
            amounts = sum(float(item["amount"]) for item in listed)
            expected = float(entry["amount"]) * (-1 if entry["flow_type"] == "expense" else 1)
            self.assertAlmostEqual(amounts, expected, msg=str(entry))
            self.assertEqual(len(listed), entry["transaction_count"], msg=str(entry))
        by_flow = {
            flow: len(core.list_transactions(
                self.database, "personal", "2026-03", None, "", 100, 0, flow_type_filter=flow
            )["items"])
            for flow in ("income", "expense")
        }
        self.assertEqual(by_flow, {"income": 3, "expense": 4})

    def test_category_payloads_include_is_income(self):
        listed = {c["name"]: c["is_income"] for c in core.category_list(self.database, "personal")}
        self.assertEqual(listed, {"Groceries": False, "Salary": True, "Uncategorized": False})
        managed = {c["name"]: c["is_income"] for c in core.category_management(self.database, "personal")}
        self.assertEqual(managed, listed)


class BackfillTests(IncomeFixture):
    """Builds a database that predates is_income and runs the upgrade."""

    def setUp(self):
        super().setUp()
        self.backups = self.root / "backups"
        self.rule_count = 0

    def categories(self):
        with core.connect(self.database) as db:
            return {
                (row["account_type"], row["name"]): bool(row["is_income"])
                for row in db.execute("SELECT account_type, name, is_income FROM categories")
            }

    def drop_column(self):
        with core.connect(self.database) as db:
            db.execute("ALTER TABLE categories DROP COLUMN is_income")

    def rule(self, category_id, flow_type, account_type="personal"):
        self.rule_count += 1
        with core.connect(self.database) as db:
            db.execute(
                "INSERT INTO category_rules(account_type, category_id, match_text, match_fields, flow_type, priority)"
                " VALUES (?, ?, ?, '[\"merchant\"]', ?, ?)",
                (account_type, category_id, f"text{self.rule_count}", flow_type, self.rule_count),
            )

    def category(self, name):
        return core.add_category(self.database, "personal", name)["id"]

    def test_backfill_rules(self):
        all_income_rules = self.category("Interest")
        self.rule(all_income_rules, "income")
        self.rule(all_income_rules, "income")
        mixed_rules = self.category("Mixed rules")
        self.rule(mixed_rules, "income")
        self.rule(mixed_rules, "expense")
        transfer_rule = self.category("Transfer rule")
        self.rule(transfer_rule, "income")
        self.rule(transfer_rule, "transfer")
        mostly_in = self.category("Mostly in")
        self.add(mostly_in, 9000)
        self.add(mostly_in, -1000)  # exactly 90% of the volume
        slightly_less = self.category("Slightly less")
        self.add(slightly_less, 8999)
        self.add(slightly_less, -1000)
        mostly_out = self.category("Mostly out")
        self.add(mostly_out, 100)
        self.add(mostly_out, -5000)
        empty = self.category("Empty")
        with core.connect(self.database) as db:
            second = self.add_account(db, OTHER_OWN)
            statement = db.execute(
                "INSERT INTO card_statements(account_id, statement_key, debit_date) VALUES (?, 's', '2026-03-05')",
                (second,),
            ).lastrowid
        in_but_excluded = self.category("Excluded volume")
        self.add(in_but_excluded, 1000)
        self.add(in_but_excluded, -50000, deleted=True)
        self.add(in_but_excluded, -50000, counterparty=OTHER_OWN)
        only_deleted = self.category("Only deleted")
        self.add(only_deleted, 1000, deleted=True)
        self.add(self.uncategorized(), 99999)
        settled = self.category("Settled")
        self.add(settled, 500)
        self.add(settled, -90000, replaced_by=statement)
        # A rule marked income on Uncategorized never counts.
        self.rule(self.uncategorized(), "income")
        # Also covers another entity with its own categories.
        kids = core.add_category(self.database, "kids", "Pocket money")["id"]
        self.rule(kids, "income", "kids")

        self.drop_column()
        backup = core.initialize(self.database, self.backups)
        self.assertIsNotNone(backup)
        income = {name for (account_type, name), flag in self.categories().items() if flag}
        self.assertEqual(
            income, {"Interest", "Mostly in", "Excluded volume", "Settled", "Pocket money"}
        )
        self.assertFalse(self.categories()[("personal", "Uncategorized")])

    def test_migration_is_idempotent_and_backs_up_once(self):
        interest = self.category("Interest")
        self.rule(interest, "income")
        self.add(self.groceries, -1000)
        self.drop_column()

        first = core.initialize(self.database, self.backups)
        self.assertIsNotNone(first)
        self.assertEqual(len(list(self.backups.glob("*.sqlite3"))), 1)
        self.assertIn("before-upgrade", first.name)
        self.assertTrue(self.categories()[("personal", "Interest")])

        core.set_category_income(self.database, interest, False)
        core.set_category_income(self.database, self.groceries, True)
        before = self.categories()
        self.assertIsNone(core.initialize(self.database, self.backups))
        self.assertEqual(len(list(self.backups.glob("*.sqlite3"))), 1)
        self.assertEqual(self.categories(), before)

    def test_fresh_database_needs_no_backup(self):
        fresh = self.root / "fresh" / "finance.sqlite3"
        self.assertIsNone(core.initialize(fresh, self.backups))
        self.assertFalse(self.backups.exists())
        with core.connect(fresh) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM categories WHERE is_income = 1").fetchone()[0], 0)


class IncomeEndpointTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.database = root / "finance.sqlite3"
        dist = root / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html>app</html>")
        self.client = TestClient(create_app(self.database, dist))
        self.addCleanup(self.client.close)
        self.category_id = self.client.post(
            "/api/categories", json={"account_type": "personal", "name": "Groceries"}
        ).json()["id"]

    def categories(self):
        return {c["name"]: c for c in self.client.get("/api/categories", params={"account_type": "personal"}).json()}

    def test_toggle_income_without_a_backup(self):
        response = self.client.patch(f"/api/categories/{self.category_id}", json={"is_income": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.categories()["Groceries"]["is_income"])
        managed = self.client.get("/api/category-management", params={"account_type": "personal"}).json()
        self.assertTrue(next(c for c in managed if c["name"] == "Groceries")["is_income"])
        self.client.patch(f"/api/categories/{self.category_id}", json={"is_income": False})
        self.assertFalse(self.categories()["Groceries"]["is_income"])
        self.assertEqual(list(self.database.parent.glob("*backup*")), [])

    def test_rename_still_works_and_leaves_the_flag(self):
        self.client.patch(f"/api/categories/{self.category_id}", json={"is_income": True})
        self.client.patch(f"/api/categories/{self.category_id}", json={"name": "Food"})
        self.assertTrue(self.categories()["Food"]["is_income"])

    def test_uncategorized_is_refused(self):
        uncategorized = self.categories()["Uncategorized"]["id"]
        response = self.client.patch(f"/api/categories/{uncategorized}", json={"is_income": True})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.categories()["Uncategorized"]["is_income"])

    def test_empty_update_and_unknown_category_are_refused(self):
        self.assertEqual(self.client.patch(f"/api/categories/{self.category_id}", json={}).status_code, 400)
        self.assertEqual(self.client.patch("/api/categories/9999", json={"is_income": True}).status_code, 404)
