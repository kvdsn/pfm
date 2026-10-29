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


class BudgetFixture(unittest.TestCase):
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
        self.travel = core.add_category(self.database, "personal", "Travel")["id"]
        self.food = core.add_category(self.database, "personal", "Food")["id"]
        self.work = core.add_category(self.database, "business", "Tools")["id"]
        with core.connect(self.database) as db:
            self.uncategorized = core.get_category_id(db, "personal", core.UNCATEGORIZED)

    def put(self, category_id, year, amount, account_type="personal"):
        return self.client.put(
            "/api/budgets",
            json={"account_type": account_type, "category_id": category_id, "year": year, "amount": amount},
        )

    def budgets(self, year="2026", account_type="personal"):
        response = self.client.get("/api/budgets", params={"account_type": account_type, "year": year})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def rows(self):
        with core.connect(self.database) as db:
            return [tuple(row) for row in db.execute(
                "SELECT category_id, year, amount_cents FROM category_budgets ORDER BY category_id, year"
            )]


class BudgetTests(BudgetFixture):
    def test_set_replace_and_remove(self):
        response = self.put(self.travel, "2026", "2.000")
        self.assertEqual(response.json(), {"category_id": self.travel, "year": "2026", "amount": "2000.00"})
        self.assertEqual(self.budgets(), [{"category_id": self.travel, "year": "2026", "amount": "2000.00"}])
        self.assertEqual(self.put(self.travel, "2026", "1500,50").json()["amount"], "1500.50")
        self.assertEqual(self.rows(), [(self.travel, "2026", 150050)])
        self.assertEqual(self.put(self.travel, "2026", "1500").json()["amount"], "1500.00")
        self.assertEqual(self.put(self.travel, "2026", 99.5).json()["amount"], "99.50")
        for empty in ("0", "", "  ", None, "€ 0,00", 0):
            self.put(self.travel, "2026", "100")
            response = self.put(self.travel, "2026", empty)
            self.assertEqual(response.json(), {"category_id": self.travel, "year": "2026", "amount": None})
            self.assertEqual(self.budgets(), [])

    def test_removing_a_missing_budget_is_fine(self):
        self.assertEqual(self.put(self.travel, "2026", None).status_code, 200)

    def test_get_filters_by_year_and_entity(self):
        self.put(self.travel, "2026", "100")
        self.put(self.food, "2026", "50")
        self.put(self.travel, "2025", "80")
        self.put(self.work, "2026", "70", "business")
        self.assertEqual(
            [(item["category_id"], item["amount"]) for item in self.budgets("2026")],
            [(self.food, "50.00"), (self.travel, "100.00")],
        )
        self.assertEqual([item["amount"] for item in self.budgets("2025")], ["80.00"])
        self.assertEqual(self.budgets("2024"), [])
        self.assertEqual([item["category_id"] for item in self.budgets("2026", "business")], [self.work])
        self.assertEqual(self.budgets("2026", "kids"), [])

    def test_validation(self):
        self.assertEqual(self.put(self.work, "2026", "10").status_code, 404)
        self.assertEqual(self.put(999, "2026", "10").status_code, 404)
        self.assertEqual(self.put(self.work, "2026", "10", "business").status_code, 200)
        self.assertEqual(self.put(self.uncategorized, "2026", "10").status_code, 400)
        for year in ("26", "20266", "abcd", "", "2026-01"):
            self.assertEqual(self.put(self.travel, year, "10").status_code, 400, year)
        for amount in ("-5", -5, "abc", "1.2.3", "1,5,5"):
            self.assertEqual(self.put(self.travel, "2026", amount).status_code, 400, amount)
        self.assertEqual(self.client.get("/api/budgets", params={"account_type": "personal", "year": "x"}).status_code, 400)
        self.assertEqual(self.client.get("/api/budgets", params={"account_type": "nobody", "year": "2026"}).status_code, 422)
        self.assertEqual(self.rows(), [(self.work, "2026", 1000)])

    def test_check_rejects_non_positive_amounts(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with core.connect(self.database) as db:
                db.execute("INSERT INTO category_budgets(category_id, year, amount_cents) VALUES (?, '2026', 0)", (self.travel,))

    def test_setting_a_budget_makes_no_backup(self):
        self.put(self.travel, "2026", "100")
        self.assertEqual(core.list_backups(self.database), [])

    def test_removing_a_category_deletes_its_budgets(self):
        self.put(self.travel, "2026", "100")
        self.put(self.travel, "2025", "90")
        self.put(self.food, "2026", "50")
        self.assertEqual(self.client.delete(f"/api/categories/{self.travel}").status_code, 200)
        self.assertEqual(self.rows(), [(self.food, "2026", 5000)])

    def test_merging_moves_budgets_and_adds_matching_years(self):
        self.put(self.travel, "2026", "100,50")
        self.put(self.travel, "2025", "90")
        self.put(self.food, "2026", "50")
        self.put(self.food, "2027", "10")
        response = self.client.post(
            "/api/categories/merge",
            json={"account_type": "personal", "source_id": self.travel, "target_id": self.food},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.rows(), [(self.food, "2025", 9000), (self.food, "2026", 15050), (self.food, "2027", 1000)]
        )

    def test_merging_into_a_category_without_budgets_moves_them(self):
        self.put(self.travel, "2026", "100")
        self.client.post(
            "/api/categories/merge",
            json={"account_type": "personal", "source_id": self.travel, "target_id": self.food},
        )
        self.assertEqual(self.rows(), [(self.food, "2026", 10000)])


class BudgetSchemaTests(BudgetFixture):
    def test_existing_database_without_the_table_gets_it_without_a_backup(self):
        self.put(self.travel, "2026", "100")
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP TABLE category_budgets")
            db.commit()
        self.assertIsNone(core.initialize(self.database))
        self.assertEqual(core.list_backups(self.database), [])
        self.assertEqual(core.list_budgets(self.database, "personal", "2026"), [])
        self.assertEqual(self.put(self.travel, "2026", "20").status_code, 200)
        self.assertIsNone(core.initialize(self.database))
        self.assertEqual(self.rows(), [(self.travel, "2026", 2000)])
