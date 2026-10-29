import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.pfm import core
from backend.pfm.core import database
from tests.test_core import make_csv, transaction

OLD_CHECK = "account_type IN ('personal', 'business')"
OLD_MODE_CHECK = "match_mode IN ('contains', 'exact')"
DATA_TABLES = ("accounts", "categories", "category_rules", "imports", "transactions", "card_statements")


def dump(path):
    """Every row of every data table, ordered by id, with column names."""
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        return {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in DATA_TABLES
        }


def build_old_database(path, with_added_columns=True, old_account_types=True):
    """A database as it was before "kids" and "word": the old CHECKs on the entity tables and rule modes."""
    schema = database.SCHEMA.replace(database.MATCH_MODE_CHECK, OLD_MODE_CHECK)
    assert OLD_MODE_CHECK in schema
    if old_account_types:
        schema = schema.replace(database.ACCOUNT_TYPE_CHECK, OLD_CHECK)
        assert OLD_CHECK in schema
    with closing(sqlite3.connect(path)) as db:
        db.executescript(schema)
        if with_added_columns:
            for table in ("transactions",):
                for name, definition in database.ADDED_COLUMNS[table].items():
                    if name not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        db.executescript(
            """
            INSERT INTO accounts(id, iban, account_type, created_at, bank, kind) VALUES
                (1, 'BE00000000000000000001', 'personal', '2026-01-01', 'KBC', 'bank'),
                (4, 'BE00000000000000000002', 'business', '2026-01-02', 'Belfius', 'bank'),
                (7, '512345XXXXXX6789', 'personal', '2026-01-03', 'KBC', 'card');
            INSERT INTO categories(id, account_type, name) VALUES
                (1, 'personal', 'Uncategorized'),
                (2, 'business', 'Uncategorized'),
                (9, 'personal', 'Groceries'),
                (12, 'business', 'Insurance');
            INSERT INTO category_rules(
                id, account_type, category_id, match_text, match_fields, flow_type, priority,
                legacy_normalized, match_mode
            ) VALUES
                (3, 'personal', 9, 'market', '["merchant"]', 'expense', 5, 0, 'exact'),
                (8, 'personal', 9, 'shop', '["merchant", "description"]', 'expense', 2, 1, 'contains'),
                (10, 'business', 12, 'insure', '["memo"]', 'expense', 0, 0, 'contains');
            INSERT INTO imports(id, account_id, filename, imported_at, row_count, inserted_count, duplicate_count)
                VALUES (2, 1, 'a.csv', '2026-02-01', 3, 3, 0), (5, 7, 'card.csv', '2026-02-02', 1, 1, 0);
            INSERT INTO card_statements(id, account_id, statement_key, debit_date) VALUES (6, 7, 'S1', '2026-02-05');
            INSERT INTO transactions(
                id, account_id, import_id, row_number, booking_date, balance_date, amount_cents,
                source_amount_cents, balance_cents, currency, merchant, category_id, flow_type, source_json,
                fingerprint, statement_id, replaced_by_statement_id
            ) VALUES
                (11, 1, 2, 1, '2026-01-02', '2026-01-02', -500, -500, 1000, 'EUR', 'Market', 9, 'expense', '{"Naam tegenpartij": "Market"}', 'f1', NULL, 6),
                (12, 1, 2, 2, '2026-01-03', '2026-01-03', -700, -700, 300, 'EUR', 'Shop', 1, 'expense', '{"Naam tegenpartij": "Shop"}', 'f2', NULL, NULL),
                (13, 7, 5, 1, '2026-01-04', '2026-01-04', -900, -900, NULL, 'EUR', 'Cafe', 9, 'expense', '{"Naam tegenpartij": "Cafe"}', 'f3', 6, NULL);
            """
        )
        if with_added_columns:
            db.execute("UPDATE transactions SET category_source = 'rule', category_rule_id = 3 WHERE id = 11")
            db.execute("UPDATE transactions SET category_source = 'default' WHERE id = 12")
            db.execute("UPDATE transactions SET category_source = 'manual' WHERE id = 13")
            db.execute("UPDATE transactions SET deleted_at = '2026-03-01' WHERE id = 12")
        db.commit()


class KidsMigrationTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.folder = Path(self.temp_dir.name)
        self.path = self.folder / "finance.sqlite3"

    def backups(self):
        return list(self.folder.glob("finance-backup-*-before-upgrade*.sqlite3"))

    def table_sql(self, name):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]

    def test_old_check_is_detected_and_rebuilt_with_every_row_and_id_kept(self):
        build_old_database(self.path)
        before = dump(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(database._stale_account_type_tables(db), ["accounts", "categories", "category_rules"])

        backup = core.initialize(self.path)

        self.assertIsNotNone(backup)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(dump(backup), before)
        self.assertIn(OLD_CHECK, self.sql_of(backup, "accounts"))
        after = dump(self.path)
        uncategorized_kids = [row for row in after["categories"] if row["account_type"] == "kids"]
        self.assertEqual([row["name"] for row in uncategorized_kids], ["Uncategorized"])
        after["categories"] = [row for row in after["categories"] if row["account_type"] != "kids"]
        self.assertEqual(after, before)
        for table in ("accounts", "categories", "category_rules"):
            self.assertIn("'kids'", self.table_sql(table))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            self.assertLessEqual({"categories_name_idx", "category_rules_account_priority_idx"}, indexes)
            self.assertEqual(db.execute("SELECT name FROM schema_migrations").fetchall(), [("merchant-is-counterparty",)])

        # kids now works at the database level
        with core.connect(self.path) as db:
            db.execute("INSERT INTO accounts(iban, account_type, created_at) VALUES ('BE9', 'kids', '2026-05-01')")
            category = db.execute("INSERT INTO categories(account_type, name) VALUES ('kids', 'Toys')").lastrowid
            db.execute(
                "INSERT INTO category_rules(account_type, category_id, match_text, match_fields, flow_type, priority)"
                " VALUES ('kids', ?, 'toy', '[\"merchant\"]', 'expense', 0)",
                (category,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO categories(account_type, name) VALUES ('other', 'x')")

    def sql_of(self, path, name):
        with closing(sqlite3.connect(path)) as db:
            return db.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]

    def test_second_initialize_changes_nothing_and_makes_no_backup(self):
        build_old_database(self.path)
        core.initialize(self.path)
        settled = dump(self.path)
        self.assertIsNone(core.initialize(self.path))
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(dump(self.path), settled)

    def test_database_without_the_later_added_columns_is_rebuilt_and_backfilled(self):
        build_old_database(self.path, with_added_columns=False)
        with closing(sqlite3.connect(self.path)) as db:
            # Rebuild the old tables without the columns added later, as the first release had them.
            db.executescript(
                """
                DROP TABLE category_rules;
                CREATE TABLE category_rules (
                    id INTEGER PRIMARY KEY,
                    account_type TEXT NOT NULL CHECK(account_type IN ('personal', 'business')),
                    category_id INTEGER NOT NULL REFERENCES categories(id),
                    match_text TEXT NOT NULL, match_fields TEXT NOT NULL,
                    flow_type TEXT NOT NULL CHECK(flow_type IN ('income', 'expense', 'transfer')),
                    priority INTEGER NOT NULL, legacy_normalized INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO category_rules VALUES (3, 'personal', 9, 'market', '["merchant"]', 'expense', 5, 0);
                """
            )
        self.assertIsNotNone(core.initialize(self.path))
        with core.connect(self.path) as db:
            self.assertEqual(
                [tuple(row) for row in db.execute("SELECT id, match_mode FROM category_rules")], [(3, "contains")]
            )
            self.assertEqual(
                [tuple(row) for row in db.execute("SELECT id, category_source, category_rule_id FROM transactions ORDER BY id")],
                [(11, "rule", 3), (12, "default", None), (13, "manual", None)],
            )
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIsNone(core.initialize(self.path))

    def test_failed_rebuild_rolls_back_and_leaves_the_old_tables(self):
        build_old_database(self.path)
        before = dump(self.path)
        with patch.object(database, "ACCOUNT_TYPE_TABLES", {**database.ACCOUNT_TYPE_TABLES, "categories": "CREATE TABLE IF NOT EXISTS categories (broken"}):
            with self.assertRaises(sqlite3.Error):
                core.initialize(self.path)
        self.assertEqual(dump(self.path), before)
        for table in ("accounts", "categories", "category_rules"):
            self.assertIn(OLD_CHECK, self.table_sql(table))

    def test_fresh_database_needs_no_rebuild_or_backup(self):
        self.assertIsNone(core.initialize(self.path))
        self.assertEqual(self.backups(), [])


class KidsEntityTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Path(self.temp_dir.name) / "finance.sqlite3"
        core.initialize(self.database)
        self.staging = {}

    def import_csv(self, account_type, rows, filename="export.csv"):
        preview = core.stage_import(self.database, filename, make_csv(rows).encode(), self.staging)
        return core.commit_import(self.database, preview["token"], account_type, self.staging)

    def test_new_database_has_only_uncategorized_for_kids(self):
        self.assertEqual([item["name"] for item in core.category_list(self.database, "kids")], ["Uncategorized"])
        self.assertEqual(core.report(self.database, "kids")["summaries"], [])
        self.assertEqual(core.import_history(self.database, "kids"), [])
        with self.assertRaises(ValueError):
            database.require_account_type("teens")

    def test_kids_import_rules_and_reports_are_separate(self):
        kids, personal = "BE00000000000000000011", "BE00000000000000000001"
        self.import_csv("personal", [
            transaction(personal, "01/05/2026", "1000,00", "1000,00", "Employer"),
            transaction(personal, "02/05/2026", "-20,00", "980,00", "Toy Shop"),
            {**transaction(personal, "03/05/2026", "-50,00", "930,00", "Kid pocket money"), "rekeningnummer tegenpartij": kids},
        ], "personal.csv")
        self.import_csv("kids", [
            transaction(kids, "03/05/2026", "50,00", "50,00", "Parent"),
            transaction(kids, "04/05/2026", "-12,00", "38,00", "Toy Shop"),
            {**transaction(kids, "05/05/2026", "-5,00", "33,00", "Toy Shop"), "Afschriftnummer": "other"},
        ], "kids.csv")

        toys = core.add_category(self.database, "kids", "Toys")["id"]
        self.assertEqual([c["name"] for c in core.category_list(self.database, "kids")], ["Toys", "Uncategorized"])
        self.assertNotIn("Toys", [c["name"] for c in core.category_list(self.database, "personal")])
        rule = core.save_category_rule(self.database, "kids", toys, "toy", ["merchant"], "expense")["id"]
        self.assertEqual(core.run_category_rule(self.database, "kids", rule), {"updated": 2, "released": 0})
        with self.assertRaises(ValueError):
            core.save_category_rule(self.database, "personal", toys, "toy", ["merchant"], "expense")

        kids_report = core.report(self.database, "kids")["summaries"][0]
        self.assertEqual(kids_report["expenses"], "17.00")
        self.assertEqual(
            [(c["name"], c["amount"]) for c in kids_report["categories"] if c["flow_type"] == "expense"],
            [("Toys", "17.00")],
        )
        personal_report = core.report(self.database, "personal")["summaries"][0]
        self.assertEqual(personal_report["income"], "1000.00")
        self.assertEqual(personal_report["expenses"], "70.00")  # the toy shop and the 50 sent to kids
        self.assertEqual(core.report(self.database, "business")["summaries"], [])
        personal_items = core.list_transactions(self.database, "personal", None, None, "", 50, 0)["items"]
        self.assertTrue(all(item["category"] != "Toys" for item in personal_items))
        self.assertEqual(core.list_transactions(self.database, "kids", None, None, "", 50, 0)["total"], 3)
        self.assertEqual(len(core.import_history(self.database, "kids")), 1)
        self.assertEqual(len(core.import_history(self.database, "personal")), 1)

    def test_transfer_between_personal_and_kids_is_not_an_own_transfer_across_entities(self):
        # Entities are separate: only a counterparty of the same entity is muted, so the transfer
        # counts as an expense on the personal side and as income on the kids side.
        kids, personal = "BE00000000000000000011", "BE00000000000000000001"
        self.import_csv("personal", [
            {**transaction(personal, "03/05/2026", "-50,00", "950,00", "Kid pocket money"), "rekeningnummer tegenpartij": kids},
        ], "personal.csv")
        self.import_csv("kids", [
            {**transaction(kids, "03/05/2026", "50,00", "50,00", "Parent"), "rekeningnummer tegenpartij": personal},
        ], "kids.csv")
        personal_summary = core.report(self.database, "personal")["summaries"][0]
        kids_summary = core.report(self.database, "kids")["summaries"][0]
        self.assertEqual((personal_summary["income"], personal_summary["expenses"]), ("0.00", "50.00"))
        self.assertEqual((kids_summary["income"], kids_summary["expenses"]), ("50.00", "0.00"))
        for entity in ("personal", "kids"):
            own = core.list_transactions(self.database, entity, None, None, "", 50, 0, flow_type_filter="own_transfer")
            self.assertEqual(own["total"], 0)


if __name__ == "__main__":
    unittest.main()
