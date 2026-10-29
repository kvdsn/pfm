import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.pfm import core
from backend.pfm.server import create_app
from tests.test_core import make_csv, transaction

ACCOUNT = "BE00000000000000000001"
OTHER = "BE00000000000000000002"
LOAN = "BEDRAG VOOR DE AFBETALING VAN UW WOONKREDIET"
INTEREST = "INTERESTEN : 01.04.2026 - 30.06.2026"


class TriageTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        os.environ.pop("PFM_HOME", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Path(self.temp_dir.name) / "finance.sqlite3"
        core.initialize(self.database)
        self.staging = {}
        self.shopping = core.add_category(self.database, "personal", "Shopping")["id"]
        self.business_category = core.add_category(self.database, "business", "Stock")["id"]

    def import_rows(self, rows, account_type="personal"):
        preview = core.stage_import(self.database, "export.csv", make_csv(rows).encode(), self.staging)
        core.commit_import(self.database, preview["token"], account_type, self.staging)

    def groups(self):
        return core.uncategorized_groups(self.database, "personal")

    def ids(self, merchant):
        items = core.list_transactions(self.database, "personal", None, None, "", 100, 0)["items"]
        return [item["id"] for item in items if item["merchant"] == merchant]

    def test_groups_split_a_merchant_by_kind(self):
        self.import_rows([
            transaction(ACCOUNT, "05/05/2026", "-900,00", "100,00", "Bank", LOAN),
            transaction(ACCOUNT, "06/05/2026", "-1,00", "99,00", "Bank", INTEREST),
            transaction(ACCOUNT, "07/05/2026", "-2,00", "97,00", "Bank", INTEREST),
        ])
        groups = self.groups()
        self.assertEqual([(g["merchant"], g["kind"], g["count"]) for g in groups], [
            ("Bank", "Mortgage repayment", 1),
            ("Bank", "Interest", 2),
        ])

    def test_group_fields_totals_and_sorting(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop", "ONE"),
            transaction(ACCOUNT, "03/05/2026", "-5,50", "84,50", "shop", "TWO"),
            transaction(ACCOUNT, "02/05/2026", "-12,00", "72,50", "Cafe"),
            transaction(ACCOUNT, "04/05/2026", "-12,00", "60,50", "Cafe"),
            transaction(ACCOUNT, "04/05/2026", "20,00", "80,50", "Refund"),
            transaction(ACCOUNT, "05/05/2026", "-1,00", "79,50", "Tiny"),
        ])
        groups = self.groups()
        # Shop and shop are one group, named after its newest transaction. Cafe (24) leads, then Refund (20), then Shop (15.50), then Tiny.
        self.assertEqual([g["merchant"] for g in groups], ["Cafe", "Refund", "shop", "Tiny"])
        cafe, refund, shop = groups[:3]
        self.assertEqual((cafe["total"], cafe["count"]), ("-24.00", 2))
        self.assertEqual(refund["total"], "20.00")
        self.assertEqual((shop["total"], shop["count"]), ("-15.50", 2))
        self.assertEqual((shop["first_date"], shop["last_date"]), ("2026-05-01", "2026-05-03"))
        self.assertEqual(sorted(shop["transaction_ids"]), sorted(self.ids("Shop") + self.ids("shop")))
        self.assertEqual([t["id"] for t in shop["transactions"]], shop["transaction_ids"])
        self.assertEqual(shop["transactions"][0]["date"], "2026-05-03")
        self.assertEqual(shop["transactions"][0]["amount"], "-5.50")
        self.assertEqual(len(shop["sample_details"]), len(set(shop["sample_details"])))
        self.assertLessEqual(len(shop["sample_details"]), 3)
        self.assertIsNone(shop["suggestion"])

    def test_equal_totals_sort_by_count(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Single"),
            transaction(ACCOUNT, "02/05/2026", "-5,00", "85,00", "Double"),
            transaction(ACCOUNT, "03/05/2026", "-5,00", "80,00", "Double"),
        ])
        self.assertEqual([g["merchant"] for g in self.groups()], ["Double", "Single"])

    def test_empty_merchant_forms_its_own_group_and_other_entities_are_ignored(self):
        self.import_rows([transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "")])
        self.import_rows([transaction(OTHER, "01/05/2026", "-50,00", "50,00", "Supplier")], "business")
        groups = self.groups()
        self.assertEqual([(g["merchant"], g["merchant_rule_matches"]) for g in groups], [("", 0)])
        self.assertEqual([g["merchant"] for g in core.uncategorized_groups(self.database, "business")], ["Supplier"])

    def test_categorized_and_deleted_transactions_are_left_out(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-20,00", "70,00", "Cafe"),
            transaction(ACCOUNT, "03/05/2026", "-30,00", "40,00", "Gone"),
        ])
        core.update_transaction(self.database, self.ids("Shop")[0], {"category_id": self.shopping})
        with core.connect(self.database) as db:
            db.execute("UPDATE transactions SET deleted_at = '2026-05-04' WHERE id = ?", (self.ids("Gone")[0],))
        self.assertEqual([g["merchant"] for g in self.groups()], ["Cafe"])

    def test_own_transfers_and_itemized_settlements_are_excluded(self):
        self.import_rows([
            {**transaction(ACCOUNT, "02/05/2026", "-100,00", "900,00", "Me"), "rekeningnummer tegenpartij": OTHER},
            transaction(ACCOUNT, "03/05/2026", "-10,00", "890,00", "Card settlement"),
            transaction(ACCOUNT, "04/05/2026", "-5,00", "885,00", "Shop"),
        ])
        self.import_rows([
            {**transaction(OTHER, "02/05/2026", "100,00", "100,00", "Me"), "rekeningnummer tegenpartij": ACCOUNT},
        ])
        with core.connect(self.database) as db:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute(
                "UPDATE transactions SET replaced_by_statement_id = 999 WHERE id = ?",
                (self.ids("Card settlement")[0],),
            )
        self.assertEqual([g["merchant"] for g in self.groups()], ["Shop"])

    def test_suggestion_is_the_most_used_category_of_the_same_merchant_and_kind(self):
        dining = core.add_category(self.database, "personal", "Dining")["id"]
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-1,00", "99,00", "Bank", INTEREST),
            transaction(ACCOUNT, "02/05/2026", "-1,00", "98,00", "Bank", INTEREST),
            transaction(ACCOUNT, "03/05/2026", "-1,00", "97,00", "Bank", INTEREST),
            transaction(ACCOUNT, "04/05/2026", "-1,00", "96,00", "Bank", INTEREST),
            transaction(ACCOUNT, "05/05/2026", "-900,00", "-804,00", "Bank", LOAN),
            transaction(ACCOUNT, "06/05/2026", "-900,00", "-1704,00", "Bank", LOAN),
        ])
        interest = [i for i in core.list_transactions(self.database, "personal", None, None, "", 100, 0)["items"]
                    if i["details"].startswith("Interest")]
        loans = [i for i in core.list_transactions(self.database, "personal", None, None, "", 100, 0)["items"]
                 if not i["details"].startswith("Interest")]
        core.bulk_set_category(self.database, "personal", [interest[0]["id"], interest[1]["id"]], self.shopping)
        core.bulk_set_category(self.database, "personal", [interest[2]["id"]], dining)
        core.bulk_set_category(self.database, "personal", [loans[0]["id"]], dining)
        groups = {g["kind"]: g for g in self.groups()}
        # The remaining loan line is judged by the categorized loan line only, not the interest ones.
        self.assertEqual(groups["Mortgage repayment"]["suggestion"], {"category_id": dining, "name": "Dining", "count": 1})
        self.assertEqual(groups["Interest"]["suggestion"], {"category_id": self.shopping, "name": "Shopping", "count": 2})

    def test_merchant_rule_matches_counts_what_a_new_last_rule_would_categorize(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-900,00", "100,00", "Bank", LOAN),
            transaction(ACCOUNT, "02/05/2026", "-1,00", "99,00", "Bank", INTEREST),
            transaction(ACCOUNT, "03/05/2026", "-1,00", "98,00", "Bank Of Elsewhere"),
        ])
        by_kind = {g["kind"]: g for g in self.groups() if g["merchant"] == "Bank"}
        self.assertEqual(by_kind["Interest"]["count"], 1)
        self.assertEqual(by_kind["Interest"]["merchant_rule_matches"], 2)
        self.assertEqual(by_kind["Mortgage repayment"]["merchant_rule_matches"], 2)
        # A transaction set by hand, or one an existing rule decides, is not taken by a new last rule.
        loan_id = by_kind["Mortgage repayment"]["transaction_ids"][0]
        core.update_transaction(self.database, loan_id, {"category_id": self.shopping})
        self.assertEqual({g["kind"]: g for g in self.groups() if g["merchant"] == "Bank"}["Interest"]["merchant_rule_matches"], 1)
        core.save_category_rule(self.database, "personal", self.shopping, "Bank", ["merchant"], "expense", match_mode="exact")
        self.assertEqual({g["kind"]: g for g in self.groups() if g["merchant"] == "Bank"}["Interest"]["merchant_rule_matches"], 0)

    def test_bulk_category_sets_manual_category_and_clears_the_rule(self):
        rule = core.save_category_rule(self.database, "personal", self.shopping, "shop", ["merchant"], "expense")["id"]
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "10,00", "100,00", "Gift"),
        ])
        core.run_category_rule(self.database, "personal", rule)
        ids = self.ids("Shop") + self.ids("Gift")
        self.assertEqual(core.bulk_set_category(self.database, "personal", ids, self.shopping), {"updated": 2})
        items = {i["merchant"]: i for i in core.list_transactions(self.database, "personal", None, None, "", 50, 0)["items"]}
        for item in items.values():
            self.assertEqual((item["category"], item["category_source"], item["category_rule_id"]), ("Shopping", "manual", None))
        # Like a single manual edit, the flow type stays as it was.
        self.assertEqual(items["Shop"]["flow_type"], "expense")
        self.assertEqual(items["Gift"]["flow_type"], "income")

    def test_bulk_category_to_uncategorized_resets_the_flow_to_the_amount_default(self):
        self.import_rows([transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop")])
        transaction_id = self.ids("Shop")[0]
        core.update_transaction(self.database, transaction_id, {"category_id": self.shopping, "flow_type": "income"})
        with core.connect(self.database) as db:
            uncategorized = core.get_category_id(db, "personal", core.UNCATEGORIZED)
        core.bulk_set_category(self.database, "personal", [transaction_id], uncategorized)
        item = core.list_transactions(self.database, "personal", None, None, "", 50, 0)["items"][0]
        self.assertEqual((item["category"], item["flow_type"], item["category_source"]), ("Uncategorized", "expense", "manual"))

    def test_bulk_category_validates_the_entity_and_changes_nothing_on_failure(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-10,00", "80,00", "Cafe"),
        ])
        self.import_rows([transaction(OTHER, "01/05/2026", "-50,00", "50,00", "Supplier")], "business")
        business_id = [g for g in core.uncategorized_groups(self.database, "business")][0]["transaction_ids"][0]
        personal_ids = self.ids("Shop") + self.ids("Cafe")
        with self.assertRaisesRegex(ValueError, "do not belong"):
            core.bulk_set_category(self.database, "personal", [*personal_ids, business_id], self.shopping)
        with self.assertRaisesRegex(ValueError, "do not belong"):
            core.bulk_set_category(self.database, "personal", [*personal_ids, 99999], self.shopping)
        with self.assertRaisesRegex(ValueError, "Category does not belong"):
            core.bulk_set_category(self.database, "personal", personal_ids, self.business_category)
        with self.assertRaisesRegex(ValueError, "No transactions"):
            core.bulk_set_category(self.database, "personal", [], self.shopping)
        self.assertEqual(len(self.groups()), 2)

    def test_endpoints(self):
        with TestClient(create_app(self.database, Path(self.temp_dir.name) / "dist")) as client:
            self.import_rows([
                transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
                transaction(ACCOUNT, "02/05/2026", "-10,00", "80,00", "Shop"),
            ])
            groups = client.get("/api/uncategorized-groups", params={"account_type": "personal"}).json()
            self.assertEqual([(g["merchant"], g["count"], g["total"]) for g in groups], [("Shop", 2, "-20.00")])
            response = client.post("/api/transactions/bulk-category", json={
                "account_type": "personal", "transaction_ids": groups[0]["transaction_ids"], "category_id": self.shopping,
            })
            self.assertEqual(response.json(), {"updated": 2})
            self.assertEqual(client.get("/api/uncategorized-groups", params={"account_type": "personal"}).json(), [])
            bad = client.post("/api/transactions/bulk-category", json={
                "account_type": "personal", "transaction_ids": [1], "category_id": self.business_category,
            })
            self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
