import csv
import io
import os
import sqlite3
from datetime import date
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.pfm import core


HEADERS = [
    "Rekeningnummer",
    "Rubrieknaam",
    "Naam",
    "Munt",
    "Afschriftnummer",
    "Datum",
    "Omschrijving",
    "Valuta",
    "Bedrag",
    "Saldo",
    "credit",
    "debet",
    "rekeningnummer tegenpartij",
    "BIC tegenpartij",
    "Naam tegenpartij",
    "Adres tegenpartij",
    "gestructureerde mededeling",
    "Vrije mededeling",
]


def make_csv(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS, delimiter=";")
    writer.writeheader()
    for row in rows:
        values = {header: "" for header in HEADERS}
        values.update({"Munt": "EUR", "Valuta": row.get("Datum", "01/05/2026")})
        values.update(row)
        writer.writerow(values)
    return output.getvalue()


def transaction(account, booking_date, amount, balance, merchant, description="", memo=""):
    return {
        "Rekeningnummer": account,
        "Afschriftnummer": f"statement-{booking_date}-{amount}",
        "Datum": booking_date,
        "Bedrag": amount,
        "Saldo": balance,
        "Naam tegenpartij": merchant,
        "Omschrijving": description,
        "Vrije mededeling": memo,
    }


LEGACY_SCHEMA = """
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY, iban TEXT NOT NULL UNIQUE,
    account_type TEXT NOT NULL CHECK(account_type IN ('personal', 'business')), created_at TEXT NOT NULL
);
CREATE TABLE categories (
    id INTEGER PRIMARY KEY, account_type TEXT NOT NULL CHECK(account_type IN ('personal', 'business')),
    name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX categories_active_name_idx ON categories(account_type, name) WHERE active = 1;
CREATE TABLE imports (
    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), filename TEXT NOT NULL,
    imported_at TEXT NOT NULL, row_count INTEGER NOT NULL, inserted_count INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL
);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
    import_id INTEGER NOT NULL REFERENCES imports(id), row_number INTEGER NOT NULL,
    booking_date TEXT NOT NULL, balance_date TEXT NOT NULL, value_date TEXT,
    amount_cents INTEGER NOT NULL, source_amount_cents INTEGER NOT NULL, balance_cents INTEGER,
    currency TEXT NOT NULL, merchant TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
    counterparty_iban TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
    category_id INTEGER REFERENCES categories(id),
    flow_type TEXT NOT NULL CHECK(flow_type IN ('income', 'expense', 'transfer')),
    source_json TEXT NOT NULL, fingerprint TEXT NOT NULL, deleted_at TEXT, UNIQUE(account_id, fingerprint)
);
CREATE TABLE merchant_rules (
    id INTEGER PRIMARY KEY, account_type TEXT NOT NULL, merchant_key TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id), flow_type TEXT NOT NULL,
    UNIQUE(account_type, merchant_key)
);
CREATE TABLE category_rules (
    id INTEGER PRIMARY KEY, account_type TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id),
    match_text TEXT NOT NULL, match_fields TEXT NOT NULL, flow_type TEXT NOT NULL, priority INTEGER NOT NULL,
    legacy_normalized INTEGER NOT NULL DEFAULT 0, legacy_rule_id INTEGER UNIQUE REFERENCES merchant_rules(id)
);
CREATE TABLE schema_migrations (name TEXT PRIMARY KEY);
"""


TEST_CATEGORIES = {
    "personal": ["Groceries", "Housing", "Salary", "Utilities", "Shopping", "Health & medical"],
    "business": ["Equipment & supplies", "Insurance", "Water & utilities", "Transport & mobility"],
}

BELFIUS_HEADERS = (
    "Rekening;Boekingsdatum;Rekeninguittrekselnummer;Transactienummer;Rekening tegenpartij;"
    "Naam tegenpartij bevat;Straat en nummer;Postcode en gemeente;Transactie;Valutadatum;"
    "Bedrag;Devies;BIC;Landcode;Mededelingen"
)


def belfius_csv(rows, range_end, saldo="500,00 EUR", saldo_time="16/10/2026 17:02:51"):
    lines = [
        "Boekingsdatum vanaf;01/01/2025",
        f"Boekingsdatum tot en met;{range_end}",
        "Bedrag vanaf;",
        "Bedrag tot en met;",
        "Rekeninguittrekselnummer vanaf;",
        "Rekeninguittrekselnummer tot en met;",
        "Mededeling;",
        "Naam tegenpartij bevat;",
        "Rekening tegenpartij;",
        f"Laatste saldo;{saldo}",
        f"Datum/uur van het laatste saldo;{saldo_time}",
        ";",
        BELFIUS_HEADERS,
    ]
    header_names = BELFIUS_HEADERS.split(";")
    for index, row in enumerate(rows):
        values = {header: "" for header in header_names}
        values.update(
            {
                "Rekening": "BE00 0000 0000 0101",
                "Rekeninguittrekselnummer": "00001",
                "Transactienummer": str(100 - index),
                "Devies": "EUR",
                **row,
            }
        )
        lines.append(";".join(values[header] for header in header_names))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


class FinanceCoreTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "finance.sqlite3"
        core.initialize(self.database)
        for account_type, names in TEST_CATEGORIES.items():
            for name in names:
                core.add_category(self.database, account_type, name)
        self.staging = {}

    def tearDown(self):
        self.temp_dir.cleanup()

    def category_id(self, account_type, name):
        return next(
            item["id"] for item in core.category_list(self.database, account_type)
            if item["name"] == name
        )

    def merge(self, account_type, source_name, target_name):
        core.merge_categories(
            self.database,
            account_type,
            self.category_id(account_type, source_name),
            self.category_id(account_type, target_name),
        )

    def test_default_database_is_in_data_folder_and_can_be_overridden(self):
        project_root = Path(core.__file__).resolve().parents[3]
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(core.default_database_path(), project_root / "data" / "finance.sqlite3")
        with patch.dict(os.environ, {"PFM_HOME": str(project_root / "custom-data")}):
            self.assertEqual(
                core.default_database_path(),
                project_root / "custom-data" / "finance.sqlite3",
            )

    def test_parses_cp1252_and_utf8_bom_exports(self):
        row = transaction("BE00000000000000000001", "13/10/2026", "-12,34", "987,66", "Café")
        for content in (make_csv([row]).encode("cp1252"), b"\xef\xbb\xbf" + make_csv([row]).encode("utf-8")):
            export = core.parse_export(content)
            iban, parsed, unknown = export.account, export.rows, export.unknown_columns
            self.assertEqual(iban, "BE00000000000000000001")
            self.assertEqual(parsed[0]["_booking_date"], "2026-10-13")
            self.assertEqual(parsed[0]["_amount_cents"], "-1234")
            self.assertEqual(parsed[0]["_balance_cents"], "98766")
            self.assertEqual(unknown, [])

    def test_rejects_mixed_account_files_and_unsupported_currencies(self):
        first = transaction("BE00000000000000000001", "01/05/2026", "-1,00", "1,00", "Shop")
        second = transaction("BE00000000000000000002", "01/05/2026", "-1,00", "1,00", "Shop")
        with self.assertRaisesRegex(ValueError, "exactly one account"):
            core.parse_export(make_csv([first, second]).encode("utf-8"))
        first["Munt"] = "USD"
        with self.assertRaisesRegex(ValueError, "Unsupported currency"):
            core.parse_export(make_csv([first]).encode("utf-8"))

    def test_parses_belfius_export_and_reconstructs_balances(self):
        contents = belfius_csv(
            [
                {
                    "Boekingsdatum": "16/10/2026",
                    "Valutadatum": "16/10/2026",
                    "Bedrag": "-20,15",
                    "Naam tegenpartij bevat": "HEMA GENT",
                    "Transactie": "BETALING HEMA",
                    "Mededelingen": "BETALING HEMA",
                    "Rekening tegenpartij": "BE00 0000 0000 0202",
                },
                {
                    "Boekingsdatum": "15/10/2026",
                    "Valutadatum": "15/10/2026",
                    "Bedrag": "1.250,00",
                    "Naam tegenpartij bevat": "WERKGEVER",
                    "Transactie": "SALARIS",
                    "Mededelingen": "SALARIS",
                },
            ],
            range_end="16/10/2026",
        )
        export = core.parse_export(contents)
        iban, rows, unknown_columns = export.account, export.rows, export.unknown_columns
        self.assertEqual(iban, "BE00000000000101")
        self.assertEqual(unknown_columns, [])
        newest, oldest = rows
        self.assertEqual(newest["Naam tegenpartij"], "HEMA GENT")
        self.assertEqual(newest["Omschrijving"], "BETALING HEMA")
        self.assertEqual(newest["rekeningnummer tegenpartij"], "BE00000000000202")
        self.assertEqual(newest["Munt"], "EUR")
        self.assertEqual(newest["Valuta"], "16/10/2026")
        self.assertEqual(newest["_booking_date"], "2026-10-16")
        self.assertEqual(newest["_amount_cents"], "-2015")
        self.assertEqual(newest["_balance_cents"], "50000")
        self.assertNotIn("_chain_anchor", newest)
        self.assertEqual(oldest["_amount_cents"], "125000")
        self.assertEqual(oldest["_balance_cents"], str(50000 + 2015))

    def test_belfius_messages_are_memo_for_rules_search_and_display(self):
        contents = belfius_csv(
            [
                {
                    "Boekingsdatum": "16/10/2026",
                    "Bedrag": "-80,00",
                    "Naam tegenpartij bevat": "ENERGY CO",
                    "Transactie": "EUROPESE DOMICILIERING",
                    "Mededelingen": "Contract 4471 October",
                },
            ],
            range_end="16/10/2026",
        )
        preview = core.stage_import(self.database, "belfius.csv", contents, self.staging)
        self.assertEqual(preview["preview"][0]["description"], "EUROPESE DOMICILIERING")
        self.assertEqual(preview["preview"][0]["memo"], "Contract 4471 October")
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        utilities_id = next(
            item["id"] for item in core.category_list(self.database, "personal")
            if item["name"] == "Utilities"
        )
        rule_id = core.save_category_rule(
            self.database, "personal", utilities_id, "contract 4471", ["memo"], "expense"
        )["id"]
        self.assertEqual(core.run_category_rule(self.database, "personal", rule_id)["updated"], 1)
        found = core.list_transactions(self.database, "personal", None, None, "4471", 500, 0)
        self.assertEqual(found["total"], 1)
        item = found["items"][0]
        self.assertEqual(
            (item["merchant"], item["description"], item["memo"], item["category"]),
            ("Energy Co", "EUROPESE DOMICILIERING", "Contract 4471 October", "Utilities"),
        )
        self.assertEqual(
            core.list_transactions(self.database, "personal", None, None, "domiciliering", 500, 0)["total"],
            1,
        )

    def test_belfius_historical_export_defers_balances_for_chaining(self):
        contents = belfius_csv(
            [
                {
                    "Boekingsdatum": "30/12/2025",
                    "Valutadatum": "30/12/2025",
                    "Bedrag": "-10,00",
                    "Transactie": "PAYMENT",
                }
            ],
            range_end="31/12/2025",
        )
        export = core.parse_export(contents)
        iban, rows, unknown_columns = export.account, export.rows, export.unknown_columns
        self.assertEqual(iban, "BE00000000000101")
        self.assertEqual(rows[0]["_balance_cents"], "")
        self.assertEqual(rows[0]["_chain_anchor"], "1")

    def test_belfius_historical_import_chains_balances_from_newer_import(self):
        newer = belfius_csv(
            [
                {"Boekingsdatum": "02/01/2026", "Valutadatum": "02/01/2026", "Bedrag": "-30,00", "Transactie": "NEWEST"},
                {"Boekingsdatum": "02/01/2026", "Valutadatum": "02/01/2026", "Bedrag": "20,00", "Transactie": "SAME DAY OLDER"},
                {"Boekingsdatum": "01/01/2026", "Valutadatum": "01/01/2026", "Bedrag": "50,00", "Transactie": "OLDER"},
            ],
            range_end="16/10/2026",
            saldo="120,00 EUR",
        )
        older = belfius_csv(
            [
                {"Boekingsdatum": "30/12/2025", "Valutadatum": "30/12/2025", "Bedrag": "-10,00", "Transactie": "OLD NEWEST"},
                {"Boekingsdatum": "29/12/2025", "Valutadatum": "29/12/2025", "Bedrag": "5,00", "Transactie": "OLD OLDEST"},
            ],
            range_end="31/12/2025",
            saldo="120,00 EUR",
        )
        preview = core.stage_import(self.database, "newer.csv", newer, self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        preview = core.stage_import(self.database, "older.csv", older, self.staging)
        staged = self.staging[preview["token"]]["rows"]
        self.assertEqual(staged[0]["_balance_cents"], "8000")
        self.assertEqual(staged[1]["_balance_cents"], "9000")
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            balances = [
                (row["booking_date"], row["balance_cents"])
                for row in db.execute(
                    "SELECT booking_date, balance_cents FROM transactions "
                    "ORDER BY booking_date ASC, id DESC"
                )
            ]
        self.assertEqual(
            balances,
            [
                ("2025-12-29", 9000),
                ("2025-12-30", 8000),
                ("2026-01-01", 13000),
                ("2026-01-02", 15000),
                ("2026-01-02", 12000),
            ],
        )

    def _belfius_numbered_exports(self):
        """The same October rows exported while pending (no numbers, with a balance) and once numbered, the
        numbered export taken the morning after its range with two newer rows, so its balance is not used."""
        pending_rows = [
            {"Boekingsdatum": "16/10/2026", "Bedrag": "-12,50", "Transactie": "SHOP"},
            {"Boekingsdatum": "12/10/2026", "Bedrag": "-3,20", "Transactie": "COFFEE"},
            {"Boekingsdatum": "12/10/2026", "Bedrag": "-3,20", "Transactie": "COFFEE"},
            {"Boekingsdatum": "01/10/2026", "Bedrag": "900,00", "Transactie": "SALARY"},
        ]
        pending = belfius_csv(
            [{**row, "Valutadatum": row["Boekingsdatum"], "Rekeninguittrekselnummer": "", "Transactienummer": ""}
             for row in pending_rows],
            range_end="16/10/2026",
            saldo="500,00 EUR",
        )
        numbered = belfius_csv(
            [
                {"Boekingsdatum": "23/10/2026", "Valutadatum": "23/10/2026", "Bedrag": "-8,00", "Transactie": "BAKERY"},
                {"Boekingsdatum": "19/10/2026", "Valutadatum": "19/10/2026", "Bedrag": "-150,00", "Transactie": "RENT"},
                *({**row, "Valutadatum": row["Boekingsdatum"]} for row in pending_rows),
            ],
            range_end="31/10/2026",
            saldo="342,00 EUR",
            saldo_time="01/11/2026 10:50:08",
        )
        return pending, numbered

    def _import(self, name, contents):
        preview = core.stage_import(self.database, name, contents, self.staging)
        result = core.commit_import(self.database, preview["token"], "personal", self.staging)
        return preview, result

    def test_belfius_rows_numbered_since_an_earlier_export_are_duplicates_and_chain_balances_forward(self):
        pending, numbered = self._belfius_numbered_exports()
        self._import("pending.csv", pending)
        preview, result = self._import("numbered.csv", numbered)
        self.assertEqual((preview["duplicate_count"], preview["new_count"]), (4, 2))
        self.assertEqual((result["imported"], result["duplicates_skipped"]), (2, 4))
        with core.connect(self.database) as db:
            balances = db.execute(
                "SELECT booking_date, balance_cents FROM transactions WHERE booking_date > '2026-10-16' "
                "ORDER BY booking_date"
            ).fetchall()
        self.assertEqual([tuple(row) for row in balances], [("2026-10-19", 35000), ("2026-10-23", 34200)])
        account = next(item for item in core.account_overview(self.database) if item["bank"] == "Belfius")
        self.assertEqual(account["latest_balance"], "342.00")

    def test_belfius_pending_export_imported_after_the_numbered_one_is_all_duplicates(self):
        pending, numbered = self._belfius_numbered_exports()
        self._import("numbered.csv", numbered)
        preview, result = self._import("pending.csv", pending)
        self.assertEqual((preview["duplicate_count"], result["imported"]), (4, 0))

    def test_belfius_import_without_anchor_keeps_balances_empty(self):
        contents = belfius_csv(
            [
                {
                    "Boekingsdatum": "30/12/2025",
                    "Valutadatum": "30/12/2025",
                    "Bedrag": "-10,00",
                    "Transactie": "PAYMENT",
                }
            ],
            range_end="31/12/2025",
        )
        preview = core.stage_import(self.database, "old.csv", contents, self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            row = db.execute("SELECT balance_cents FROM transactions").fetchone()
            self.assertIsNone(row["balance_cents"])

    def test_import_is_idempotent_and_account_mapping_is_persistent(self):
        row = transaction("BE00000000000000000001", "01/05/2026", "-25,00", "75,00", "Shop")
        contents = make_csv([row]).encode("utf-8")
        preview = core.stage_import(self.database, "personal.csv", contents, self.staging)
        self.assertTrue(preview["account_type_required"])
        result = core.commit_import(self.database, preview["token"], "personal", self.staging)
        self.assertEqual(result["imported"], 1)

        repeated = core.stage_import(self.database, "again.csv", contents, self.staging)
        self.assertEqual(repeated["duplicate_count"], 1)
        result = core.commit_import(self.database, repeated["token"], "personal", self.staging)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["duplicates_skipped"], 1)
        remap_preview = core.stage_import(self.database, "wrong.csv", contents, self.staging)
        remapped = core.commit_import(self.database, remap_preview["token"], "business", self.staging)
        self.assertEqual(remapped["duplicates_skipped"], 1)
        self.assertEqual(len(core.accounts_list(self.database)), 1)
        self.assertEqual(core.accounts_list(self.database)[0]["account_type"], "business")

    def test_import_history_can_undo_an_import_without_removing_account_mapping(self):
        account = "BE00000000000000000001"
        row = transaction(account, "01/05/2026", "-25,00", "75,00", "Shop")
        preview = core.stage_import(
            self.database, "may.csv", make_csv([row]).encode(), self.staging
        )
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        history = core.import_history(self.database, "personal")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["filename"], "may.csv")
        self.assertEqual(history[0]["transaction_count"], 1)

        duplicate_preview = core.stage_import(
            self.database, "may.csv", make_csv([row]).encode(), self.staging
        )
        duplicate_result = core.commit_import(
            self.database, duplicate_preview["token"], "personal", self.staging
        )
        self.assertEqual(duplicate_result["imported"], 0)
        duplicate_import = core.import_history(self.database, "personal")[0]
        self.assertEqual(duplicate_import["duplicate_count"], 1)
        self.assertEqual(core.undo_import(self.database, duplicate_import["id"])["removed_transactions"], 0)
        self.assertEqual(core.import_history(self.database, "personal")[0]["transaction_count"], 1)

        undone = core.undo_import(self.database, history[0]["id"])
        self.assertEqual(undone, {"filename": "may.csv", "removed_transactions": 1})
        self.assertEqual(core.import_history(self.database, "personal"), [])
        self.assertEqual(core.accounts_list(self.database)[0]["account_type"], "personal")
        self.assertEqual(core.report(self.database, "personal")["summaries"], [])

        reimport = core.stage_import(
            self.database, "may.csv", make_csv([row]).encode(), self.staging
        )
        self.assertEqual(reimport["new_count"], 1)

        with self.assertRaisesRegex(LookupError, "Import not found"):
            core.undo_import(self.database, history[0]["id"])

    def test_reimbursement_and_transfer_reporting_and_original_data(self):
        account = "BE00000000000000000001"
        rows = [
            transaction(account, "01/05/2026", "1000,00", "1100,00", "Employer", memo="Salary"),
            transaction(account, "02/05/2026", "-200,00", "900,00", "Shop"),
            transaction(account, "03/05/2026", "50,00", "950,00", "Clinic", memo="Medical refund"),
            transaction(account, "04/05/2026", "-100,00", "850,00", "Own savings"),
        ]
        preview = core.stage_import(self.database, "personal.csv", make_csv(rows).encode(), self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        unclassified = core.list_transactions(
            self.database, "personal", None, None, "", 500, 0, "uncategorized", "expense"
        )
        self.assertGreaterEqual(unclassified["total"], 1)
        self.assertIn("Shop", {item["merchant"] for item in unclassified["items"]})
        self.assertTrue(all(item["category"] == "Uncategorized" for item in unclassified["items"]))
        with core.connect(self.database) as db:
            shop_id = db.execute("SELECT id FROM transactions WHERE merchant = 'Shop'").fetchone()["id"]
            refund_id = db.execute("SELECT id FROM transactions WHERE merchant = 'Clinic'").fetchone()["id"]
            transfer_id = db.execute("SELECT id FROM transactions WHERE merchant = 'Own savings'").fetchone()["id"]
            original_source = db.execute("SELECT source_json FROM transactions WHERE id = ?", (refund_id,)).fetchone()["source_json"]
        health_category = next(category["id"] for category in core.category_list(self.database, "personal") if category["name"] == "Health & medical")
        core.update_transaction(self.database, shop_id, {"category_id": health_category})
        core.update_transaction(self.database, refund_id, {"category_id": health_category, "flow_type": "expense"})
        core.update_transaction(self.database, transfer_id, {"flow_type": "transfer"})

        summary = core.report(self.database, "personal")["summaries"][0]
        self.assertEqual(summary["income"], "1000.00")
        self.assertEqual(summary["expenses"], "250.00")
        self.assertEqual(summary["net"], "750.00")
        transfer_row = next(
            item for item in summary["categories"]
            if item["name"] == "Uncategorized" and item["flow_type"] == "expense"
        )
        self.assertEqual(transfer_row["amount"], "100.00")
        transfers = core.list_transactions(
            self.database, "personal", "2026-05", None, "", 500, 0, "uncategorized", "expense"
        )
        self.assertEqual([item["merchant"] for item in transfers["items"]], ["Own savings"])
        self.assertEqual(summary["account_balances"][0]["opening"], "100.00")
        self.assertEqual(summary["account_balances"][0]["closing"], "850.00")
        balances = core.balance_history(self.database, "personal", "2026-05", None)
        self.assertEqual(len(balances["points"]), 31)
        self.assertEqual(
            [point["balance"] for point in balances["points"][:4]],
            ["1100.00", "900.00", "950.00", "850.00"],
        )
        health = next(item for item in summary["categories"] if item["name"] == "Health & medical")
        self.assertEqual(health["amount"], "150.00")
        self.assertEqual(health["category_id"], health_category)
        filtered = core.list_transactions(
            self.database, "personal", "2026-05", None, "", 500, 0,
            str(health_category), "expense",
        )
        self.assertEqual(filtered["total"], 2)
        self.assertEqual({item["merchant"] for item in filtered["items"]}, {"Shop", "Clinic"})
        with core.connect(self.database) as db:
            self.assertEqual(
                db.execute("SELECT source_json FROM transactions WHERE id = ?", (refund_id,)).fetchone()["source_json"],
                original_source,
            )

    def test_null_and_default_uncategorized_transactions_share_one_breakdown_row(self):
        account = "BE00000000000000000001"
        rows = [
            transaction(account, "01/05/2026", "-10,00", "90,00", "Unknown one"),
            transaction(account, "02/05/2026", "-20,00", "70,00", "Unknown two"),
        ]
        preview = core.stage_import(self.database, "uncategorized.csv", make_csv(rows).encode(), self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            db.execute(
                "UPDATE transactions SET category_id = NULL WHERE merchant = 'Unknown two'"
            )

        summary = core.report(self.database, "personal")["summaries"][0]
        uncategorized = [item for item in summary["categories"] if item["name"] == "Uncategorized"]
        self.assertEqual(len(uncategorized), 1)
        self.assertEqual(uncategorized[0]["amount"], "30.00")
        self.assertIsNone(uncategorized[0]["category_id"])

    def test_other_income_and_expense_are_not_active_default_categories(self):
        self.assertNotIn("Other income", {item["name"] for item in core.category_list(self.database, "personal")})
        self.assertNotIn("Other income", {item["name"] for item in core.category_list(self.database, "business")})
        self.assertNotIn("Other expenses", {item["name"] for item in core.category_list(self.database, "business")})

    def test_transactions_can_be_filtered_by_year(self):
        account = "BE00000000000000000001"
        rows = [
            transaction(account, "01/12/2025", "-10,00", "90,00", "Old Shop"),
            transaction(account, "02/01/2026", "-20,00", "70,00", "New Shop"),
        ]
        preview = core.stage_import(self.database, "years.csv", make_csv(rows).encode(), self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)

        result = core.list_transactions(self.database, "personal", None, "2026", "", 500, 0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["merchant"], "New Shop")

    def test_transactions_show_the_account_of_their_export(self):
        kbc_rows = [transaction("BE00000000000000000001", "02/01/2026", "-20,00", "70,00", "KBC Shop")]
        for filename, contents in (
            ("kbc.csv", make_csv(kbc_rows).encode()),
            ("belfius.csv", belfius_csv(
                [{"Boekingsdatum": "03/01/2026", "Valutadatum": "03/01/2026", "Bedrag": "-5,00", "Transactie": "BELFIUS SHOP"}],
                "03/01/2026",
            )),
        ):
            preview = core.stage_import(self.database, filename, contents, self.staging)
            core.commit_import(self.database, preview["token"], "personal", self.staging)

        items = core.list_transactions(self.database, "personal", None, None, "", 500, 0)["items"]
        self.assertEqual([item["account"] for item in items], ["Belfius", "KBC"])

    def test_balance_history_sums_accounts_at_daily_and_monthly_granularity(self):
        first_account = "BE00000000000000000001"
        second_account = "BE00000000000000000002"
        first_rows = [
            transaction(first_account, "01/05/2026", "100,00", "1100,00", "Income"),
            transaction(first_account, "03/05/2026", "-50,00", "1050,00", "Expense"),
        ]
        second_rows = [
            transaction(second_account, "02/05/2026", "-200,00", "800,00", "Expense"),
        ]
        for filename, rows in (("first.csv", first_rows), ("second.csv", second_rows)):
            preview = core.stage_import(
                self.database, filename, make_csv(rows).encode(), self.staging
            )
            core.commit_import(self.database, preview["token"], "personal", self.staging)

        daily = core.balance_history(self.database, "personal", "2026-05", None)["points"]
        self.assertEqual(len(daily), 31)
        self.assertEqual(
            [(point["date"], point["balance"]) for point in daily[:3]],
            [("2026-05-01", "2100.00"), ("2026-05-02", "1900.00"), ("2026-05-03", "1850.00")],
        )
        yearly = core.balance_history(self.database, "personal", None, "2026", today=date(2027, 1, 1))["points"]
        self.assertEqual(len(yearly), 12)
        self.assertEqual(yearly[4], {"date": "2026-05", "balance": "1850.00"})

        # Months and days after today are left out.
        current_year = core.balance_history(self.database, "personal", None, "2026", today=date(2026, 5, 15))
        self.assertEqual([point["date"] for point in current_year["points"]], [f"2026-0{month}" for month in range(1, 6)])
        current_month = core.balance_history(self.database, "personal", "2026-05", None, today=date(2026, 5, 3))
        self.assertEqual(len(current_month["points"]), 3)
        self.assertEqual(core.balance_history(self.database, "personal", None, "2027", today=date(2026, 5, 3)), {"points": []})

    def test_unmatched_classification_uses_only_amount_direction(self):
        with core.connect(self.database) as db:
            self.assertEqual(
                core.suggest_classification(
                    db,
                    "personal",
                    100,
                    {"Naam tegenpartij": "Known former hard-coded match", "Vrije mededeling": "REFUND"},
                ),
                ("Uncategorized", "income"),
            )
            self.assertEqual(
                core.suggest_classification(
                    db,
                    "business",
                    -100,
                    {"Naam tegenpartij": "Known former hard-coded match", "Vrije mededeling": "VERLONING"},
                ),
                ("Uncategorized", "expense"),
            )

    def test_category_rules_match_selected_fields_case_insensitively_and_by_account(self):
        with core.connect(self.database) as db:
            personal_category = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Groceries'"
            ).fetchone()["id"]
            business_category = db.execute(
                "SELECT id FROM categories WHERE account_type = 'business' AND name = 'Equipment & supplies'"
            ).fetchone()["id"]
            utilities_category = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Utilities'"
            ).fetchone()["id"]
        core.save_category_rule(
            self.database, "personal", personal_category, "Fresh Market", ["merchant"], "expense"
        )
        core.save_category_rule(
            self.database, "business", business_category, "INV-42", ["description"], "transfer"
        )
        core.save_category_rule(
            self.database, "personal", utilities_category, "electricity", ["memo"], "expense"
        )
        with core.connect(self.database) as db:
            self.assertEqual(
                core.suggest_classification(
                    db, "personal", -2500, {"Naam tegenpartij": "FRESH MARKET Gent"}
                ),
                ("Groceries", "expense"),
            )
            self.assertEqual(
                core.suggest_classification(
                    db, "business", 2500, {"Omschrijving": "invoice inv-42 from supplier"}
                ),
                ("Equipment & supplies", "transfer"),
            )
            self.assertEqual(
                core.suggest_classification(
                    db, "personal", -2500, {"Omschrijving": "invoice inv-42"}
                ),
                ("Uncategorized", "expense"),
            )
            self.assertEqual(
                core.suggest_classification(
                    db, "personal", -2500, {"Vrije mededeling": "ELECTRICITY"}
                ),
                ("Utilities", "expense"),
            )

    def test_rule_preview_and_runs_change_only_matching_transactions(self):
        rows = [
            transaction("BE00000000000000000001", "01/05/2026", "-10,00", "90,00", "Fresh Market"),
            transaction("BE00000000000000000001", "02/05/2026", "-20,00", "70,00", "Home Shop"),
            transaction("BE00000000000000000001", "03/05/2026", "30,00", "100,00", "Unmatched"),
        ]
        preview = core.stage_import(
            self.database, "rules.csv", make_csv(rows).encode(), self.staging
        )
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            groceries_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Groceries'"
            ).fetchone()["id"]
            housing_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Housing'"
            ).fetchone()["id"]
            salary_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Salary'"
            ).fetchone()["id"]
            transaction_ids = [
                row["id"] for row in db.execute("SELECT id FROM transactions ORDER BY booking_date")
            ]
            db.execute(
                "UPDATE transactions SET category_id = ?, flow_type = 'transfer' WHERE id = ?",
                (salary_id, transaction_ids[2]),
            )
        core.save_category_rule(
            self.database, "personal", groceries_id, "fresh", ["merchant"], "expense"
        )
        core.save_category_rule(
            self.database, "personal", housing_id, "home", ["merchant"], "transfer"
        )
        rule_preview = core.preview_category_rule(
            self.database, "personal", "FRESH", ["merchant"]
        )
        self.assertEqual(rule_preview["match_count"], 1)
        # An unsaved rule ranks after the saved "fresh" rule, which already wins.
        self.assertEqual(rule_preview["first_match_count"], 0)
        self.assertEqual(rule_preview["samples"][0]["merchant"], "Fresh Market")

        rule_ids = [
            rule["id"]
            for category in core.category_management(self.database, "personal")
            for rule in category["rules"]
        ]
        self.assertEqual(
            sum(core.run_category_rule(self.database, "personal", rule_id)["updated"] for rule_id in rule_ids),
            2,
        )
        with core.connect(self.database) as db:
            actual = db.execute(
                """
                SELECT id, category_id, flow_type FROM transactions
                ORDER BY booking_date
                """
            ).fetchall()
            original_unmatched = actual[2]
            self.assertEqual(actual[0]["id"], transaction_ids[0])
            self.assertEqual(actual[0]["category_id"], groceries_id)
            self.assertEqual(actual[0]["flow_type"], "expense")
            self.assertEqual(actual[1]["category_id"], housing_id)
            self.assertEqual(actual[1]["flow_type"], "transfer")
            self.assertEqual(original_unmatched["category_id"], salary_id)
            self.assertEqual(original_unmatched["flow_type"], "transfer")

    def test_deleting_rule_uncategorizes_only_transactions_it_categorized(self):
        rows = [
            transaction("BE00000000000000000001", "01/05/2026", "-10,00", "90,00", "Fresh Market"),
            transaction(
                "BE00000000000000000001",
                "02/05/2026",
                "20,00",
                "110,00",
                "Other Shop",
                description="FRESH MARKET refund",
            ),
            transaction("BE00000000000000000001", "03/05/2026", "-30,00", "80,00", "Other Shop"),
            transaction("BE00000000000000000001", "04/05/2026", "-5,00", "75,00", "Fresh Market Cafe"),
            transaction("BE00000000000000000001", "05/05/2026", "-7,00", "68,00", "Fresh Market"),
        ]
        preview = core.stage_import(
            self.database, "delete-rule.csv", make_csv(rows).encode(), self.staging
        )
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            category_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Groceries'"
            ).fetchone()["id"]
            housing_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Housing'"
            ).fetchone()["id"]
            uncategorized_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Uncategorized'"
            ).fetchone()["id"]
            ids = [row["id"] for row in db.execute("SELECT id FROM transactions ORDER BY booking_date")]
        # Set by hand: the rule's text matches the last one, but it stays in Housing.
        core.update_transaction(self.database, ids[2], {"category_id": housing_id, "flow_type": "income"})
        core.update_transaction(self.database, ids[4], {"category_id": housing_id})
        cafe_rule_id = core.save_category_rule(
            self.database, "personal", category_id, "cafe", ["merchant"], "expense"
        )["id"]
        rule_id = core.save_category_rule(
            self.database,
            "personal",
            category_id,
            "fresh market",
            ["merchant", "description"],
            "transfer",
        )["id"]
        core.reorder_category_rules(self.database, "personal", [cafe_rule_id, rule_id])
        core.run_category_rule(self.database, "personal", cafe_rule_id)
        core.run_category_rule(self.database, "personal", rule_id)

        core.delete_category_rule(self.database, "personal", rule_id)

        with core.connect(self.database) as db:
            actual = db.execute(
                "SELECT category_id, flow_type, category_source, category_rule_id FROM transactions ORDER BY booking_date"
            ).fetchall()
        self.assertEqual(
            [tuple(row) for row in actual],
            [
                (uncategorized_id, "expense", "default", None),
                (uncategorized_id, "income", "default", None),
                (housing_id, "income", "manual", None),
                # Categorized by the other rule: kept.
                (category_id, "expense", "rule", cafe_rule_id),
                (housing_id, "expense", "manual", None),
            ],
        )

    def test_earliest_matching_rule_wins_and_can_be_reordered(self):
        with core.connect(self.database) as db:
            groceries_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Groceries'"
            ).fetchone()["id"]
            housing_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Housing'"
            ).fetchone()["id"]
        first = core.save_category_rule(
            self.database, "personal", groceries_id, "market", ["merchant"], "expense"
        )["id"]
        second = core.save_category_rule(
            self.database, "personal", housing_id, "market", ["merchant"], "transfer"
        )["id"]
        row = {"Naam tegenpartij": "Market"}
        with core.connect(self.database) as db:
            self.assertEqual(core.suggest_classification(db, "personal", -100, row), ("Groceries", "expense"))
        core.reorder_category_rules(self.database, "personal", [second, first])
        with core.connect(self.database) as db:
            self.assertEqual(core.suggest_classification(db, "personal", -100, row), ("Housing", "transfer"))

    def test_run_rule_updates_only_transactions_where_it_is_first_match(self):
        rows = [
            transaction("BE00000000000000000001", "01/05/2026", "-10,00", "90,00", "Fresh Market"),
            transaction("BE00000000000000000001", "02/05/2026", "-20,00", "70,00", "Local Market"),
            transaction("BE00000000000000000001", "03/05/2026", "-30,00", "40,00", "Corner Shop"),
        ]
        preview = core.stage_import(
            self.database, "run-rule.csv", make_csv(rows).encode(), self.staging
        )
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            groceries_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Groceries'"
            ).fetchone()["id"]
            housing_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Housing'"
            ).fetchone()["id"]
            salary_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Salary'"
            ).fetchone()["id"]
            transaction_ids = [
                row["id"] for row in db.execute("SELECT id FROM transactions ORDER BY booking_date")
            ]
            db.execute(
                "UPDATE transactions SET category_id = ?, flow_type = 'income'",
                (salary_id,),
            )
        core.save_category_rule(
            self.database, "personal", groceries_id, "Fresh", ["merchant"], "expense"
        )
        market_rule_id = core.save_category_rule(
            self.database, "personal", housing_id, "Market", ["merchant"], "transfer"
        )["id"]

        self.assertEqual(
            core.run_category_rule(self.database, "personal", market_rule_id),
            {"updated": 1, "released": 0},
        )
        with core.connect(self.database) as db:
            updated = db.execute(
                """
                SELECT id, category_id, flow_type FROM transactions
                ORDER BY booking_date
                """
            ).fetchall()
        self.assertEqual(
            (updated[0]["category_id"], updated[0]["flow_type"]),
            (salary_id, "income"),
        )
        self.assertEqual(
            (updated[1]["category_id"], updated[1]["flow_type"]),
            (housing_id, "transfer"),
        )
        self.assertEqual(
            (updated[2]["category_id"], updated[2]["flow_type"]),
            (salary_id, "income"),
        )
        with self.assertRaisesRegex(LookupError, "Rule not found"):
            core.run_category_rule(self.database, "business", market_rule_id)

    def test_initialize_upgrades_legacy_schema_once_with_a_backup(self):
        legacy = Path(self.temp_dir.name) / "legacy" / "finance.sqlite3"
        legacy.parent.mkdir()
        with sqlite3.connect(legacy) as db:
            db.executescript(LEGACY_SCHEMA)
            db.executescript(
                """
                INSERT INTO categories(id, account_type, name, active) VALUES
                    (1, 'personal', 'Uncategorized', 1),
                    (2, 'business', 'Uncategorized', 1),
                    (3, 'business', 'Insurance', 1),
                    (4, 'personal', 'Archived', 0),
                    (5, 'personal', 'Groceries', 1);
                INSERT INTO accounts(id, iban, account_type, created_at)
                    VALUES (1, 'BE00000000000000000001', 'personal', '2026-01-01');
                INSERT INTO imports(id, account_id, filename, imported_at, row_count, inserted_count, duplicate_count)
                    VALUES (1, 1, 'old.csv', '2026-01-01', 1, 1, 0);
                INSERT INTO transactions(
                    id, account_id, import_id, row_number, booking_date, balance_date, amount_cents,
                    source_amount_cents, currency, category_id, flow_type, source_json, fingerprint
                ) VALUES (1, 1, 1, 1, '2026-01-02', '2026-01-02', -500, -500, 'EUR', 4, 'expense', '{}', 'f1');
                INSERT INTO merchant_rules(id, account_type, merchant_key, category_id, flow_type) VALUES
                    (1, 'business', 'OLD MERCHANT', 3, 'expense'),
                    (2, 'personal', 'ARCHIVED SHOP', 4, 'expense');
                INSERT INTO category_rules(
                    id, account_type, category_id, match_text, match_fields, flow_type, priority, legacy_rule_id
                ) VALUES
                    (10, 'personal', 5, 'market', '["merchant"]', 'expense', 0, NULL),
                    (11, 'personal', 4, 'archived', '["merchant"]', 'expense', 1, NULL);
                """
            )

        backup = core.initialize(legacy)

        self.assertIsNotNone(backup)
        self.assertTrue(backup.name.startswith("finance-backup-") and backup.suffix == ".sqlite3")
        with sqlite3.connect(backup) as copy:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0], 2)
        with core.connect(legacy) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertNotIn("merchant_rules", tables)
            self.assertNotIn("active", {row[1] for row in db.execute("PRAGMA table_info(categories)")})
            self.assertNotIn("legacy_rule_id", {row[1] for row in db.execute("PRAGMA table_info(category_rules)")})
            self.assertIsNone(db.execute("SELECT id FROM categories WHERE id = 4").fetchone())
            self.assertEqual(db.execute("SELECT category_id FROM transactions WHERE id = 1").fetchone()[0], 1)
            self.assertEqual(
                [tuple(row) for row in db.execute(
                    "SELECT account_type, category_id, match_text, legacy_normalized FROM category_rules ORDER BY id"
                )],
                [("personal", 5, "market", 0), ("business", 3, "OLD MERCHANT", 1)],
            )
            self.assertEqual(
                core.suggest_classification(db, "business", -100, {"Naam tegenpartij": "Old/Merchant"}),
                ("Insurance", "expense"),
            )
        self.assertIsNone(core.initialize(legacy))
        self.assertEqual(len(list(legacy.parent.glob("finance-backup-*.sqlite3"))), 1)

    def test_new_database_has_no_legacy_structures_and_needs_no_backup(self):
        fresh = Path(self.temp_dir.name) / "fresh" / "finance.sqlite3"
        self.assertIsNone(core.initialize(fresh))
        with core.connect(fresh) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertNotIn("merchant_rules", tables)
            self.assertEqual(
                {row["account_type"] for row in db.execute("SELECT account_type FROM categories WHERE name = 'Uncategorized'")},
                {"personal", "business", "kids"},
            )
        self.assertEqual(list(fresh.parent.glob("*backup*")), [])

    def test_category_management_includes_transaction_counts(self):
        rows = [
            transaction("BE00000000000000000001", "01/05/2026", "-10,00", "90,00", "Local Market"),
            transaction("BE00000000000000000001", "02/05/2026", "-20,00", "70,00", "Other Shop"),
        ]
        preview = core.stage_import(
            self.database, "category-counts.csv", make_csv(rows).encode(), self.staging
        )
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            groceries_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Groceries'"
            ).fetchone()["id"]
            db.execute(
                "UPDATE transactions SET category_id = ? WHERE merchant = 'Local Market'",
                (groceries_id,),
            )
            db.execute(
                "UPDATE transactions SET category_id = NULL WHERE merchant = 'Other Shop'"
            )

        categories = core.category_management(self.database, "personal")
        groceries = next(category for category in categories if category["id"] == groceries_id)
        uncategorized = next(category for category in categories if category["name"] == "Uncategorized")

        self.assertEqual(groceries["transaction_count"], 1)
        self.assertEqual(uncategorized["transaction_count"], 1)

    def test_uncategorized_category_is_protected(self):
        with core.connect(self.database) as db:
            category_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Uncategorized'"
            ).fetchone()["id"]
        with self.assertRaisesRegex(ValueError, "protected system category"):
            core.rename_category(self.database, category_id, "Other")
        with self.assertRaisesRegex(ValueError, "protected system category"):
            core.remove_category(self.database, category_id)

    def test_remove_category_reassigns_transactions_and_deletes_rules(self):
        row = transaction(
            "BE00000000000000000001", "01/05/2026", "-10,00", "90,00", "Local Market"
        )
        preview = core.stage_import(
            self.database, "remove-category.csv", make_csv([row]).encode(), self.staging
        )
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        category = core.add_category(self.database, "personal", "Custom category")
        category_id = category["id"]
        with core.connect(self.database) as db:
            transaction_id = db.execute("SELECT id FROM transactions").fetchone()["id"]
            uncategorized_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Uncategorized'"
            ).fetchone()["id"]
            db.execute(
                "UPDATE transactions SET category_id = ?, flow_type = 'transfer' WHERE id = ?",
                (category_id, transaction_id),
            )
        core.save_category_rule(
            self.database, "personal", category_id, "local market", ["merchant"], "transfer"
        )

        core.remove_category(self.database, category_id)

        with core.connect(self.database) as db:
            transaction_after = db.execute(
                "SELECT category_id, flow_type FROM transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            self.assertEqual(transaction_after["category_id"], uncategorized_id)
            self.assertEqual(transaction_after["flow_type"], "expense")
            self.assertIsNone(
                db.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM category_rules WHERE category_id = ?", (category_id,)).fetchone()[0],
                0,
            )

    def test_merge_categories_moves_transactions_and_rules(self):
        with core.connect(self.database) as db:
            source_id = db.execute(
                "INSERT INTO categories(account_type, name) VALUES ('business', 'Utilities')"
            ).lastrowid
            target_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'business' AND name = 'Water & utilities'"
            ).fetchone()["id"]
            account_id = db.execute(
                "INSERT INTO accounts(iban, account_type, created_at) VALUES (?, 'business', ?)",
                ("BE00000000000000000001", "2026-10-14"),
            ).lastrowid
            import_id = db.execute(
                """
                INSERT INTO imports(account_id, filename, imported_at, row_count, inserted_count, duplicate_count)
                VALUES (?, 'test.csv', '2026-10-14', 1, 1, 0)
                """,
                (account_id,),
            ).lastrowid
            db.execute(
                """
                INSERT INTO transactions(
                    account_id, import_id, row_number, booking_date, balance_date,
                    amount_cents, source_amount_cents, currency, category_id,
                    flow_type, source_json, fingerprint
                ) VALUES (?, ?, 1, '2026-10-14', '2026-10-14', -100, -100, 'EUR', ?,
                          'expense', '{}', 'test-fingerprint')
                """,
                (account_id, import_id, source_id),
            )
        core.save_category_rule(self.database, "business", source_id, "ACME ENERGIE", ["merchant"], "expense")

        self.merge("business", "Utilities", "Water & utilities")

        with core.connect(self.database) as db:
            self.assertEqual(
                db.execute("SELECT category_id FROM transactions").fetchone()["category_id"],
                target_id,
            )
            self.assertEqual(
                db.execute("SELECT category_id FROM category_rules").fetchone()["category_id"],
                target_id,
            )
            self.assertIsNone(
                db.execute("SELECT id FROM categories WHERE id = ?", (source_id,)).fetchone()
            )

    def test_merge_vehicle_categories_into_transport_and_mobility(self):
        with core.connect(self.database) as db:
            target_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'business' AND name = 'Transport & mobility'"
            ).fetchone()["id"]
            source_ids = []
            for name in ("Vehicle lease", "Vehicle & road tax"):
                source_ids.append(
                    db.execute(
                        "INSERT INTO categories(account_type, name) VALUES ('business', ?)",
                        (name,),
                    ).lastrowid
                )
        for index, category_id in enumerate(source_ids):
            core.save_category_rule(
                self.database, "business", category_id, f"MERCHANT {index}", ["merchant"], "expense"
            )

        for name in ("Vehicle lease", "Vehicle & road tax"):
            self.merge("business", name, "Transport & mobility")

        with core.connect(self.database) as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM category_rules WHERE category_id IN (?, ?)",
                    source_ids,
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM category_rules WHERE category_id = ?",
                    (target_id,),
                ).fetchone()[0],
                2,
            )
            self.assertTrue(
                all(
                    db.execute("SELECT id FROM categories WHERE id = ?", (source_id,)).fetchone() is None
                    for source_id in source_ids
                )
            )

    def test_merge_cannot_remove_protected_uncategorized(self):
        with self.assertRaisesRegex(ValueError, "protected"):
            self.merge("personal", "Uncategorized", "Groceries")
        with core.connect(self.database) as db:
            self.assertIsNotNone(
                db.execute(
                    "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Uncategorized'"
                ).fetchone()
            )

    def test_corrections_do_not_create_rules_and_survive_account_remapping(self):
        account = "BE00000000000000000001"
        row = transaction(account, "01/05/2026", "-45,00", "55,00", "Local Market")
        first = core.stage_import(self.database, "first.csv", make_csv([row]).encode(), self.staging)
        core.commit_import(self.database, first["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            item = db.execute("SELECT id FROM transactions").fetchone()
            category_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Groceries'"
            ).fetchone()["id"]
        core.update_transaction(self.database, item["id"], {"category_id": category_id})
        with core.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0], 0)

        refund = transaction(account, "02/05/2026", "45,00", "100,00", "Local Market", memo="REFUND")
        second = core.stage_import(self.database, "refund.csv", make_csv([refund]).encode(), self.staging)
        core.commit_import(self.database, second["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            learned = db.execute(
                "SELECT category_id, flow_type FROM transactions WHERE amount_cents = 4500"
            ).fetchone()
        self.assertNotEqual(learned["category_id"], category_id)
        self.assertEqual(learned["flow_type"], "income")
        self.assertEqual(core.report(self.database, "business")["summaries"], [])

        account_id = core.accounts_list(self.database)[0]["id"]
        core.remap_account(self.database, account_id, "business")
        business_summary = core.report(self.database, "business")["summaries"][0]
        self.assertEqual(business_summary["expenses"], "45.00")
        self.assertEqual(business_summary["income"], "45.00")
        with core.connect(self.database) as db:
            mapped_category = db.execute(
                """
                SELECT c.account_type, c.name FROM transactions t
                JOIN categories c ON c.id = t.category_id WHERE t.id = ?
                """,
                (item["id"],),
            ).fetchone()
        self.assertEqual(mapped_category["account_type"], "business")
        self.assertEqual(mapped_category["name"], "Groceries")

    def test_only_category_and_flow_type_are_editable(self):
        account = "BE00000000000000000001"
        row = transaction(account, "01/05/2026", "-10,00", "90,00", "Shop")
        preview = core.stage_import(self.database, "personal.csv", make_csv([row]).encode(), self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            transaction_id = db.execute("SELECT id FROM transactions").fetchone()["id"]
        for field, value in (("amount", "-20.00"), ("merchant", "Other"), ("booking_date", "2026-05-02")):
            with self.assertRaisesRegex(ValueError, "Unsupported transaction fields"):
                core.update_transaction(self.database, transaction_id, {field: value})

    def test_choosing_uncategorized_stores_the_category_and_resets_flow_type(self):
        account = "BE00000000000000000001"
        row = transaction(account, "01/05/2026", "-10,00", "90,00", "Shop")
        preview = core.stage_import(self.database, "personal.csv", make_csv([row]).encode(), self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            transaction_id = db.execute("SELECT id FROM transactions").fetchone()["id"]
            uncategorized_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Uncategorized'"
            ).fetchone()["id"]
            shopping_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Shopping'"
            ).fetchone()["id"]
        core.update_transaction(self.database, transaction_id, {"category_id": shopping_id, "flow_type": "income"})
        core.update_transaction(self.database, transaction_id, {"category_id": None})
        with core.connect(self.database) as db:
            stored = db.execute(
                "SELECT category_id, flow_type FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
        self.assertEqual((stored["category_id"], stored["flow_type"]), (uncategorized_id, "expense"))

    def test_month_balances_carry_forward_accounts_without_activity(self):
        first = [
            transaction("BE00000000000000000001", "01/05/2026", "-10,00", "90,00", "Shop"),
            transaction("BE00000000000000000001", "01/06/2026", "-10,00", "80,00", "Shop"),
        ]
        second = [transaction("BE00000000000000000002", "02/05/2026", "-5,00", "495,00", "Cafe")]
        for name, rows in (("first.csv", first), ("second.csv", second)):
            preview = core.stage_import(self.database, name, make_csv(rows).encode(), self.staging)
            core.commit_import(self.database, preview["token"], "personal", self.staging)
        report = core.report(self.database, "personal")
        self.assertEqual(report["months"], ["2026-05", "2026-06"])
        june = report["summaries"][1]
        self.assertEqual(june["month"], "2026-06")
        self.assertEqual(
            sorted(balance["closing"] for balance in june["account_balances"]),
            ["495.00", "80.00"],
        )
        with core.connect(self.database) as db:
            db.execute("UPDATE transactions SET deleted_at = 'now' WHERE merchant = 'Cafe'")
        june = core.report(self.database, "personal")["summaries"]
        self.assertEqual(len(june[0]["account_balances"]), 1)

    def test_refund_credit_is_not_automatically_matched_to_prior_expense(self):
        account = "BE00000000000000000001"
        refund = transaction(account, "02/05/2026", "20,00", "75,00", "Pluxee", memo="REFUND")
        original = transaction(account, "01/05/2026", "-45,00", "55,00", "Pluxee")
        preview = core.stage_import(
            self.database, "personal.csv", make_csv([refund, original]).encode(), self.staging
        )
        core.commit_import(self.database, preview["token"], "personal", self.staging)
        summary = core.report(self.database, "personal")["summaries"][0]
        self.assertEqual(summary["income"], "20.00")
        self.assertEqual(summary["expenses"], "45.00")
        with core.connect(self.database) as db:
            refund_id = db.execute(
                "SELECT id FROM transactions WHERE amount_cents = 2000"
            ).fetchone()["id"]
            shopping_id = db.execute(
                "SELECT id FROM categories WHERE account_type = 'personal' AND name = 'Shopping'"
            ).fetchone()["id"]
        core.update_transaction(self.database, refund_id, {"category_id": shopping_id})
        repeated = core.stage_import(
            self.database, "personal.csv", make_csv([refund, original]).encode(), self.staging
        )
        self.assertEqual(repeated["duplicate_count"], 2)
        core.commit_import(self.database, repeated["token"], "personal", self.staging)
        with core.connect(self.database) as db:
            category_after_reimport = db.execute(
                "SELECT category_id FROM transactions WHERE id = ?", (refund_id,)
            ).fetchone()["category_id"]
        self.assertEqual(category_after_reimport, shopping_id)

    def test_cancelled_preview_is_removed_from_staging(self):
        row = transaction("BE00000000000000000001", "01/05/2026", "-1,00", "9,00", "Shop")
        preview = core.stage_import(self.database, "sample.csv", make_csv([row]).encode(), self.staging)
        core.cancel_staged_import(preview["token"], self.staging)
        with self.assertRaisesRegex(LookupError, "expired"):
            core.commit_import(self.database, preview["token"], "personal", self.staging)


if __name__ == "__main__":
    unittest.main()


class PreviousYearToDateTests(unittest.TestCase):
    setUp = FinanceCoreTests.setUp
    tearDown = FinanceCoreTests.tearDown

    def import_rows(self, *rows):
        contents = make_csv([transaction("BE00000000000000000001", *row) for row in rows]).encode("utf-8")
        preview = core.stage_import(self.database, "export.csv", contents, self.staging)
        core.commit_import(self.database, preview["token"], "personal", self.staging)

    def test_cuts_last_years_month_off_at_the_latest_day_this_year(self):
        self.import_rows(
            ("05/10/2025", "-10,00", "90,00", "Shop"),
            ("20/10/2025", "-20,00", "70,00", "Shop"),
            ("16/10/2026", "-30,00", "40,00", "Shop"),
        )
        result = core.report(self.database, "personal", today=date(2026, 10, 18))
        partial = result["previous_year_to_date"]
        self.assertEqual((partial["month"], partial["through"]), ("2025-10", "2025-10-16"))
        self.assertEqual(partial["expenses"], core.format_money(1000))
        full = next(summary for summary in result["summaries"] if summary["month"] == "2025-10")
        self.assertEqual(full["expenses"], core.format_money(3000))

    def test_leap_day_compares_with_the_28th_and_no_data_this_year_gives_none(self):
        self.import_rows(("28/02/2027", "-10,00", "90,00", "Shop"), ("29/02/2028", "-5,00", "85,00", "Shop"))
        partial = core.report(self.database, "personal", today=date(2028, 3, 1))["previous_year_to_date"]
        self.assertEqual(partial["through"], "2027-02-28")
        self.assertEqual(partial["expenses"], core.format_money(1000))
        self.assertIsNone(core.report(self.database, "personal", today=date(2029, 1, 1))["previous_year_to_date"])
