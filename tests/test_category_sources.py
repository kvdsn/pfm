import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.pfm import core
from tests.test_core import make_csv, transaction

ACCOUNT = "BE00000000000000000001"


class CategorySourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "finance.sqlite3"
        core.initialize(self.database)
        self.staging = {}
        self.shopping = core.add_category(self.database, "personal", "Shopping")["id"]
        self.dining = core.add_category(self.database, "personal", "Dining")["id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def import_rows(self, rows):
        preview = core.stage_import(self.database, "export.csv", make_csv(rows).encode(), self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)

    def rule(self, category_id, text, flow_type="expense"):
        return core.save_category_rule(self.database, "personal", category_id, text, ["merchant"], flow_type)["id"]

    def listed(self):
        items = core.list_transactions(self.database, "personal", None, None, "", 50, 0)["items"]
        return {item["merchant"]: item for item in items}

    def stored(self):
        with core.connect(self.database) as db:
            return {
                row["merchant"]: (row["name"], row["flow_type"], row["category_source"], row["category_rule_id"])
                for row in db.execute(
                    "SELECT t.merchant, c.name, t.flow_type, t.category_source, t.category_rule_id "
                    "FROM transactions t JOIN categories c ON c.id = t.category_id"
                )
            }

    def test_import_records_the_deciding_rule_or_the_default(self):
        shop = self.rule(self.shopping, "shop")
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-20,00", "70,00", "Elsewhere"),
        ])
        self.assertEqual(self.stored(), {
            "Shop": ("Shopping", "expense", "rule", shop),
            "Elsewhere": ("Uncategorized", "expense", "default", None),
        })
        items = self.listed()
        self.assertEqual((items["Shop"]["category_source"], items["Shop"]["category_rule"]), ("rule", "shop"))
        self.assertEqual((items["Elsewhere"]["category_source"], items["Elsewhere"]["category_rule"]), ("default", None))

    def test_listing_gives_the_rule_id_and_its_priority_position(self):
        first = self.rule(self.dining, "cafe")
        second = self.rule(self.shopping, "shop")
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-20,00", "70,00", "Cafe"),
            transaction(ACCOUNT, "03/05/2026", "-30,00", "40,00", "Elsewhere"),
        ])
        items = self.listed()
        self.assertEqual((items["Cafe"]["category_rule_id"], items["Cafe"]["category_rule_position"]), (first, 1))
        self.assertEqual((items["Shop"]["category_rule_id"], items["Shop"]["category_rule_position"]), (second, 2))
        self.assertEqual((items["Elsewhere"]["category_rule_id"], items["Elsewhere"]["category_rule_position"]), (None, None))
        # Set by hand: no rule is reported.
        core.update_transaction(self.database, items["Shop"]["id"], {"category_id": self.dining})
        self.assertEqual(self.listed()["Shop"]["category_rule_position"], None)
        # Deleting the rule leaves no position to open.
        core.delete_category_rule(self.database, "personal", first)
        self.assertEqual(self.listed()["Cafe"]["category_rule_position"], None)

    def test_a_manual_edit_marks_the_transaction_as_set_by_hand(self):
        self.rule(self.shopping, "shop")
        self.import_rows([transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop")])
        transaction_id = self.listed()["Shop"]["id"]
        core.update_transaction(self.database, transaction_id, {"flow_type": "transfer"})
        self.assertEqual(self.stored()["Shop"], ("Shopping", "transfer", "manual", None))
        # Choosing Uncategorized by hand counts too.
        core.update_transaction(self.database, transaction_id, {"category_id": None})
        self.assertEqual(self.stored()["Shop"], ("Uncategorized", "expense", "manual", None))
        self.assertEqual(self.listed()["Shop"]["category_source"], "manual")

    def test_applying_a_rule_never_changes_transactions_set_by_hand(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-20,00", "70,00", "Shop Two"),
        ])
        core.update_transaction(self.database, self.listed()["Shop Two"]["id"], {"category_id": self.dining})
        shop = self.rule(self.shopping, "shop")
        self.assertEqual(core.run_category_rule(self.database, "personal", shop), {"updated": 1, "released": 0})
        self.assertEqual(self.stored()["Shop"], ("Shopping", "expense", "rule", shop))
        self.assertEqual(self.stored()["Shop Two"], ("Dining", "expense", "manual", None))

    def test_applying_releases_what_a_saved_narrower_rule_no_longer_decides(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-20,00", "70,00", "Shop Online"),
            transaction(ACCOUNT, "03/05/2026", "-30,00", "40,00", "Shop Kiosk"),
        ])
        online = self.rule(self.dining, "online")
        broad = self.rule(self.shopping, "shop")
        core.run_category_rule(self.database, "personal", online)
        core.run_category_rule(self.database, "personal", broad)
        self.assertEqual(self.stored()["Shop Online"], ("Dining", "expense", "rule", online))

        # Narrow the broad rule to one transaction; saving leaves everything as it was.
        core.save_category_rule(self.database, "personal", self.shopping, "shop", ["merchant"], "expense", broad, "exact")
        self.assertEqual(self.stored()["Shop Kiosk"], ("Shopping", "expense", "rule", broad))
        impact = core.rule_impact(self.database, "personal", "shop", ["merchant"], "exact", self.shopping, "expense", broad)
        self.assertEqual(impact["released_count"], 1)
        self.assertEqual(impact["released"][0]["merchant"], "Shop Kiosk")
        self.assertEqual((impact["released"][0]["new_category"], impact["released"][0]["decided_by"]), ("Uncategorized", None))

        self.assertEqual(core.run_category_rule(self.database, "personal", broad), {"updated": 0, "released": 1})
        self.assertEqual(self.stored()["Shop Kiosk"], ("Uncategorized", "expense", "default", None))
        self.assertEqual(self.stored()["Shop Online"], ("Dining", "expense", "rule", online))
        self.assertEqual(self.stored()["Shop"], ("Shopping", "expense", "rule", broad))

    def test_deleting_a_rule_only_moves_its_own_transactions(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-20,00", "70,00", "Cafe"),
            transaction(ACCOUNT, "03/05/2026", "-30,00", "40,00", "Bakery"),
        ])
        shop, cafe = self.rule(self.shopping, "shop"), self.rule(self.dining, "cafe")
        core.run_category_rule(self.database, "personal", shop)
        core.run_category_rule(self.database, "personal", cafe)
        core.update_transaction(self.database, self.listed()["Bakery"]["id"], {"category_id": self.shopping})
        core.delete_category_rule(self.database, "personal", shop)
        self.assertEqual(self.stored(), {
            "Shop": ("Uncategorized", "expense", "default", None),
            "Cafe": ("Dining", "expense", "rule", cafe),
            "Bakery": ("Shopping", "expense", "manual", None),
        })

    def test_removing_a_category_marks_its_transactions_as_default(self):
        self.import_rows([transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop")])
        shop = self.rule(self.shopping, "shop")
        core.run_category_rule(self.database, "personal", shop)
        core.remove_category(self.database, self.shopping)
        self.assertEqual(self.stored()["Shop"], ("Uncategorized", "expense", "default", None))

    def test_merging_categories_keeps_the_source_and_rule(self):
        self.import_rows([transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop")])
        shop = self.rule(self.shopping, "shop")
        core.run_category_rule(self.database, "personal", shop)
        core.merge_categories(self.database, "personal", self.shopping, self.dining)
        self.assertEqual(self.stored()["Shop"], ("Dining", "expense", "rule", shop))

    def test_impact_flags_manual_matches_and_counts_only_the_others(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-20,00", "70,00", "Shop Two"),
        ])
        core.update_transaction(self.database, self.listed()["Shop Two"]["id"], {"category_id": self.dining})
        impact = core.rule_impact(self.database, "personal", "shop", ["merchant"], "contains", self.shopping, "expense")
        self.assertEqual((impact["win_count"], impact["change_count"], impact["released"]), (2, 1, []))
        by_merchant = {item["merchant"]: item for item in impact["matches"]}
        self.assertEqual((by_merchant["Shop"]["manual"], by_merchant["Shop"]["changes"]), (False, True))
        self.assertEqual((by_merchant["Shop Two"]["manual"], by_merchant["Shop Two"]["changes"]), (True, False))

    def test_an_unedited_saved_rule_can_have_released_transactions(self):
        self.import_rows([transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop")])
        shop = self.rule(self.shopping, "shop")
        core.run_category_rule(self.database, "personal", shop)
        core.save_category_rule(self.database, "personal", self.shopping, "nothing", ["merchant"], "expense", shop)
        impact = core.rule_impact(self.database, "personal", "nothing", ["merchant"], "contains", self.shopping, "expense", shop)
        self.assertEqual((impact["change_count"], impact["released_count"]), (0, 1))

    def test_existing_transactions_are_classified_conservatively(self):
        self.import_rows([
            transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction(ACCOUNT, "02/05/2026", "-20,00", "70,00", "Shop Two"),
            transaction(ACCOUNT, "03/05/2026", "-5,00", "65,00", "Cafe"),
            transaction(ACCOUNT, "04/05/2026", "-6,00", "59,00", "Bakery"),
            transaction(ACCOUNT, "05/05/2026", "-7,00", "52,00", "Shop Three"),
            transaction(ACCOUNT, "06/05/2026", "-8,00", "44,00", "Shop Four"),
        ])
        shop = self.rule(self.shopping, "shop")
        with core.connect(self.database) as db:
            ids = {row["merchant"]: row["id"] for row in db.execute("SELECT id, merchant FROM transactions")}
            def set_category(merchant, category_id, flow_type="expense"):
                db.execute(
                    "UPDATE transactions SET category_id = ?, flow_type = ? WHERE id = ?",
                    (category_id, flow_type, ids[merchant]),
                )
            set_category("Shop", self.shopping)
            set_category("Shop Two", self.dining)
            set_category("Bakery", self.dining)
            set_category("Shop Three", self.shopping, "transfer")
            set_category("Shop Four", self.shopping)
            db.execute("UPDATE transactions SET deleted_at = '2026-06-01' WHERE id = ?", (ids["Shop Four"],))
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("ALTER TABLE transactions DROP COLUMN category_source")
            db.execute("ALTER TABLE transactions DROP COLUMN category_rule_id")
            db.commit()

        core.initialize(self.database, Path(self.temp_dir.name) / "backups")

        self.assertEqual(self.stored(), {
            "Shop": ("Shopping", "expense", "rule", shop),
            "Shop Two": ("Dining", "expense", "manual", None),
            "Cafe": ("Uncategorized", "expense", "default", None),
            "Bakery": ("Dining", "expense", "manual", None),
            "Shop Three": ("Shopping", "transfer", "manual", None),
            "Shop Four": ("Shopping", "expense", "rule", shop),
        })


if __name__ == "__main__":
    unittest.main()
