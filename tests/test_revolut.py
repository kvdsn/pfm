import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.pfm import core
from backend.pfm.core import database
from backend.pfm.core.rules import transaction_texts
from backend.pfm.core.parsing import parse_export, transaction_fingerprint

HEADER = "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance"
ROWS = [
    "Topup,Current,2026-05-03 22:12:00,2026-05-03 22:12:01,Payment from MARIE EXAMPLE,100.00,0.00,EUR,COMPLETED,100.00",
    "Card Payment,Current,2026-05-04 09:00:00,2026-05-06 03:10:00,Bakery Test,-12.50,0.00,EUR,COMPLETED,87.50",
    "Transfer,Current,2026-05-07 10:00:00,2026-05-07 10:00:05,To PIETER VOORBEELD,-20.00,0.25,EUR,COMPLETED,67.25",
    "Transfer,Current,2026-05-08 10:00:00,2026-05-08 10:00:03,Payment from Jan Voorbeeld,5.00,0.00,EUR,COMPLETED,72.25",
    "Card Payment,Current,2026-05-09 12:00:00,,Pending Shop,-3.00,0.00,EUR,PENDING,",
    "Card Payment,Current,2026-05-09 13:00:00,,Reverted Shop,-4.00,0.00,EUR,REVERTED,",
    "Card Payment,Current,2026-05-09 14:00:00,,Declined Shop,-5.00,0.00,EUR,DECLINED,",
    "Card Payment,Current,2026-05-09 15:00:00,,Failed Shop,-6.00,0.00,EUR,FAILED,",
]
EXTRA = "Card Payment,Current,2026-05-10 08:00:00,2026-05-10 08:00:09,KOFFIE TEST,-2.00,0.00,EUR,COMPLETED,70.25"


def make_export(rows, bom=False):
    text = "\n".join([HEADER, *rows]) + "\n"
    return (("﻿" if bom else "") + text).encode("utf-8")


class RevolutParsingTests(unittest.TestCase):
    def test_detects_revolut_with_or_without_bom(self):
        for bom in (False, True):
            parsed = parse_export(make_export(ROWS, bom))
            self.assertEqual((parsed.bank, parsed.kind), ("Revolut", "bank"))
            self.assertEqual(parsed.account, "REVOLUT-CURRENT-EUR")
            self.assertEqual(parsed.unknown_columns, [])

    def test_only_completed_rows_are_imported(self):
        parsed = parse_export(make_export(ROWS))
        self.assertEqual(len(parsed.rows), 4)
        self.assertEqual({row["State"] for row in parsed.rows}, {"COMPLETED"})
        with self.assertRaises(ValueError):
            parse_export(make_export(ROWS[4:]))

    def test_fee_dates_and_balance(self):
        rows = {row["Description"]: row for row in parse_export(make_export(ROWS)).rows}
        transfer = rows["To PIETER VOORBEELD"]
        self.assertEqual(transfer["_amount_cents"], "-2025")
        self.assertEqual(transfer["Fee"], "0.25")  # kept in the source data
        self.assertEqual(transfer["_balance_cents"], "6725")
        card = rows["Bakery Test"]
        self.assertEqual(card["_booking_date"], "2026-05-06")  # completed date
        self.assertEqual(card["_value_date"], "2026-05-04")  # started date

    def test_merchant_and_detail_for_each_type(self):
        texts = {
            row["Description"]: transaction_texts(row, "Revolut")
            for row in parse_export(make_export([*ROWS, EXTRA])).rows
        }
        self.assertEqual((texts["Payment from MARIE EXAMPLE"]["merchant"], texts["Payment from MARIE EXAMPLE"]["details"]), ("Marie Example", "Top-up"))
        self.assertEqual((texts["Bakery Test"]["merchant"], texts["Bakery Test"]["details"]), ("Bakery Test", "Card"))
        self.assertEqual((texts["To PIETER VOORBEELD"]["merchant"], texts["To PIETER VOORBEELD"]["details"]), ("Pieter Voorbeeld", "Transfer"))
        self.assertEqual(texts["Payment from Jan Voorbeeld"]["merchant"], "Jan Voorbeeld")
        self.assertEqual(texts["KOFFIE TEST"]["merchant"], "Koffie Test")
        # A staged row (no bank known yet) reads the same.
        row = parse_export(make_export(ROWS)).rows[0]
        self.assertEqual(transaction_texts(row)["details"], transaction_texts(row, "Revolut")["details"])

    def test_transfer_from_prefix_is_stripped_also_for_stored_rows(self):
        self.assertEqual(
            transaction_texts({"Started Date": "x", "Type": "Transfer", "Description": "Transfer from JAN VOORBEELD"})["merchant"],
            "Jan Voorbeeld",
        )
        # A row stored before this cleanup existed still reads the cleaned name from its original texts.
        stored = {"source_json": '{"Started Date": "x", "Type": "Transfer", "Description": "Transfer from JAN VOORBEELD", '
                  '"Naam tegenpartij": "Transfer from Jan Voorbeeld"}'}
        self.assertEqual(transaction_texts(stored)["merchant"], "Jan Voorbeeld")

    def test_other_currencies_are_rejected(self):
        usd = ROWS[0].replace(",EUR,", ",USD,")
        with self.assertRaisesRegex(ValueError, "Unsupported currency"):
            parse_export(make_export([usd]))

    def test_identical_rows_get_distinct_fingerprints(self):
        parsed = parse_export(make_export([ROWS[0], ROWS[0]]))
        self.assertEqual(len({transaction_fingerprint(row) for row in parsed.rows}), 2)

    def test_account_label(self):
        self.assertEqual(database.masked_iban("REVOLUT-CURRENT-EUR"), "Current EUR")


class RevolutImportTests(unittest.TestCase):
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

    def import_rows(self, rows, account_type="kids"):
        preview = core.stage_import(self.database, "revolut.csv", make_export(rows), self.staging)
        return preview, core.commit_import(self.database, preview["token"], account_type, self.staging)

    def test_preview_and_stable_account_across_imports(self):
        preview, result = self.import_rows(ROWS[:3])
        self.assertEqual(preview["account_label"], "Revolut")
        self.assertEqual(preview["masked_iban"], "Current EUR")
        self.assertEqual(preview["account_type"], None)
        self.assertEqual((preview["transaction_count"], preview["new_count"]), (3, 3))
        self.assertEqual(preview["preview"][0]["merchant"], "Marie Example")
        self.assertEqual(result["imported"], 3)

        second = core.stage_import(self.database, "later.csv", make_export([*ROWS, EXTRA]), self.staging)
        self.assertEqual(second["account_type"], "kids")  # same account found again
        self.assertEqual((second["duplicate_count"], second["new_count"]), (3, 2))
        result = core.commit_import(self.database, second["token"], "kids", self.staging)
        self.assertEqual((result["imported"], result["duplicates_skipped"]), (2, 3))
        self.assertEqual(len(core.accounts_list(self.database)), 1)
        self.assertEqual(core.accounts_list(self.database)[0]["label"], "Revolut")

        again = core.stage_import(self.database, "again.csv", make_export([*ROWS, EXTRA]), self.staging)
        self.assertEqual(again["new_count"], 0)

    def test_kids_entity_report_and_transactions(self):
        self.import_rows([*ROWS, EXTRA])
        summary = core.report(self.database, "kids")["summaries"][0]
        self.assertEqual(summary["income"], "105.00")
        self.assertEqual(summary["expenses"], "34.75")  # 12.50 + 20.00 + 0.25 fee + 2.00
        self.assertEqual(core.report(self.database, "personal")["summaries"], [])
        listing = core.list_transactions(self.database, "kids", None, None, "", 50, 0)
        self.assertEqual(listing["total"], 5)
        by_merchant = {item["merchant"]: item for item in listing["items"]}
        self.assertEqual(by_merchant["Pieter Voorbeeld"]["amount"], "-20.25")
        self.assertEqual(by_merchant["Pieter Voorbeeld"]["balance"], "67.25")
        self.assertEqual(by_merchant["Pieter Voorbeeld"]["details"], "Transfer")
        self.assertEqual(by_merchant["Pieter Voorbeeld"]["account"], "Revolut")
        self.assertEqual(by_merchant["Bakery Test"]["booking_date"], "2026-05-06")
        self.assertEqual(by_merchant["Bakery Test"]["value_date"], "2026-05-04")
        self.assertEqual(by_merchant["Koffie Test"]["details"], "Card")
        self.assertFalse(any(item["own_transfer"] for item in listing["items"]))


if __name__ == "__main__":
    unittest.main()
