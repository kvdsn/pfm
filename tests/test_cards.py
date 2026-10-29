import tempfile
import unittest
from pathlib import Path

from backend.pfm import core
from tests.test_core import make_csv, transaction

KBC_CARD_HEADER = (
    "kredietkaart;kaarthouder;uitgavenstaat;datum verrichting;Datum verrekening;bedrag;credit;debet;"
    "munt;koers;bedrag in EUR;Kosten op verrichting;Handelaar;locatie;land;toelichting"
)
CARD = "512345XXXXXX6789"


def kbc_card_csv(rows):
    """Rows of (statement, date, amount, merchant); an empty card number marks a bank payment."""
    lines = [KBC_CARD_HEADER]
    for card, statement, day, amount, merchant in rows:
        lines.append(
            f"{card};{'HOLDER' if card else ''};{statement};{day};{day};{amount};;;EUR;1,000000000;"
            f"{amount};0,00;{merchant};GENT;België;; "
        )
    return "\r".join(lines).encode("cp1252")


def belfius_statement_pdf(lines):
    """A minimal one-page PDF whose text extracts as the given lines."""
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = "BT /F1 9 Tf 20 800 Td 11 TL " + " ".join(f"({line}) '" for line in escaped) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    output = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output.encode("latin-1")))
        output += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(output.encode("latin-1"))
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    output += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    output += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return output.encode("latin-1")


def belfius_lines(closing, debit, period_start, items, holder="Jan Peeters"):
    return [
        f"Afsluitingsdatum {closing}",
        f"Datum van debet {debit}",
        f"Transacties van {period_start} tot {closing}",
        "UITGAVENSTAAT",
        "Klantenreferentie 1234565000",
        f"Transacties - Kaartnummer 5200 XXXX XXXX 1234 - {holder}",
        "VERREKENING OMSCHRIJVING BEDRAG",
        *items,
    ]


class CardImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "finance.sqlite3"
        core.initialize(self.database)
        self.staging = {}

    def tearDown(self):
        self.temp_dir.cleanup()

    def import_file(self, contents, account_type="personal", filename="export.csv"):
        preview = core.stage_import(self.database, filename, contents, self.staging)
        return preview, core.commit_import(self.database, preview["token"], account_type, self.staging)

    def counted_expenses(self, month):
        summary = next(s for s in core.report(self.database, "personal")["summaries"] if s["month"] == month)
        return summary["expenses"]

    def test_parses_kbc_card_exports_and_dates_statements_by_their_bank_payment(self):
        export = core.parse_export(kbc_card_csv([
            ("", "augustus 2026", "01/08/2026", "50,00", "DOMICILIERING VIA JE BANK"),
            (CARD, "juli 2026", "07/07/2026", "-30,00", "AUTOWAY"),
            (CARD, "juli 2026", "07/07/2026", "-30,00", "AUTOWAY"),
            (CARD, "augustus 2026", "22/07/2026", "-59,00", "Swatch"),
        ]))
        self.assertEqual((export.account, export.bank, export.kind), (CARD, "KBC", "card"))
        self.assertEqual([row["_amount_cents"] for row in export.rows], ["-3000", "-3000", "-5900"])
        self.assertNotEqual(*[core.transaction_fingerprint(row) for row in export.rows[:2]])
        self.assertEqual(
            [(statement.key, statement.debit_date) for statement in export.statements],
            [("augustus 2026", "2026-09-01"), ("juli 2026", "2026-08-01")],
        )

    def test_parses_belfius_statement_pdfs_with_cardholders_and_foreign_currency(self):
        export = core.parse_export(belfius_statement_pdf(belfius_lines(
            "07/01/2026", "16/01/2026", "08/12/2025",
            [
                "28/12 29/12  HOMEEXCHANGE PARIS FR 215,00 USD 184,23 EUR -",
                "(Via HOMEEXCHANGE) 1 EUR =",
                "1,16701948USD",
                "24/12 24/12  Vinted Vilnius LT 37,09 EUR+",
                "Transacties - Kaartnummer 5200 XXXX XXXX 5678 - Marie Peeters",
                "03/01 04/01  Thee en Taart Gent BE 50,00 EUR -",
                "(Via Apple Pay)",
            ],
        )))
        self.assertEqual((export.account, export.bank, export.kind), ("1234565000", "Belfius", "card"))
        self.assertEqual(
            [(row["_booking_date"], row["_amount_cents"], row["Naam tegenpartij"]) for row in export.rows],
            [
                ("2025-12-28", "-18423", "HOMEEXCHANGE PARIS"),
                ("2025-12-24", "3709", "Vinted Vilnius"),
                ("2026-01-03", "-5000", "Thee en Taart Gent"),
            ],
        )
        self.assertEqual(
            export.rows[0]["Vrije mededeling"],
            "Jan Peeters · via HOMEEXCHANGE · 215,00 USD (1 EUR = 1,16701948 USD)",
        )
        self.assertEqual(export.rows[2]["Vrije mededeling"], "Marie Peeters · via Apple Pay")
        self.assertEqual([(s.key, s.debit_date) for s in export.statements], [("2026-01-07", "2026-01-16")])

    def test_statement_purchases_replace_the_bank_settlement_and_refunds_carry_over(self):
        bank = "BE00000000000000000001"
        self.import_file(make_csv([
            transaction(bank, "16/10/2025", "-97,20", "900,00", "", "MASTERCARD AFREKENING NUMMER 280"),
            transaction(bank, "16/08/2025", "-20,00", "997,20", "", "MASTERCARD AFREKENING NUMMER 220"),
        ]).encode())
        statements = [
            ("07/08/2025", "16/08/2025", "08/07/2025", "20/07 21/07  Shop Gent BE 20,00 EUR -"),
            # A refund-only statement is not debited; its credit lowers the next payment.
            ("07/09/2025", "16/09/2025", "08/08/2025", "10/08 10/08  Vinted Vilnius LT 68,25 EUR+"),
            ("07/10/2025", "16/10/2025", "08/09/2025", "15/09 16/09  Airline Paris FR 165,45 EUR -"),
            ("07/11/2025", "17/11/2025", "08/10/2025", "12/10 13/10  Bakery Gent BE 5,64 EUR -"),
        ]
        for closing, debit, start, item in statements:
            self.import_file(belfius_statement_pdf(belfius_lines(closing, debit, start, [item])), filename="statement.pdf")

        settlements = [
            item for item in core.list_transactions(self.database, "personal", None, None, "", 500, 0)["items"]
            if item["account"] == "KBC"
        ]
        self.assertEqual([item["itemized"] for item in settlements], [True, True])
        self.assertEqual(self.counted_expenses("2025-07"), "20.00")
        self.assertEqual(self.counted_expenses("2025-09"), "165.45")
        # October counts the bakery purchase, not the 97,20 settlement it no longer replaces.
        self.assertEqual(self.counted_expenses("2025-10"), "5.64")

        card_id = next(a["id"] for a in core.accounts_list(self.database) if a["label"] == "Belfius card")
        statements = core.card_statements(self.database, card_id)
        self.assertEqual(
            [(s["period"], s["status"], s["due"], s["carried_credit"]) for s in statements],
            [
                ("2025-11-07", "unpaid", "5.64", None),
                ("2025-10-07", "paid", "97.20", "68.25"),
                ("2025-09-07", "credit", "0.00", None),
                ("2025-08-07", "paid", "20.00", None),
            ],
        )
        self.assertEqual(statements[1]["settlement"]["booking_date"], "2025-10-16")
        # Each statement names the import of its purchases: here one PDF per statement.
        self.assertEqual(len({s["import_id"] for s in statements}), 4)
        settlement_id = statements[1]["settlement"]["id"]
        detail = core.settlement_statement(self.database, settlement_id)
        self.assertEqual(detail["id"], statements[1]["id"])
        # The purchases come as listed transactions, so the table can show them like any other row.
        self.assertEqual(
            [(p["merchant"], p["amount"], p["account"], p["category"]) for p in detail["purchases"]],
            [("Airline Paris", "-165.45", "Belfius card", "Uncategorized")],
        )
        with self.assertRaises(LookupError):
            core.settlement_statement(self.database, detail["purchases"][0]["id"])
        with self.assertRaises(LookupError):
            core.card_statements(self.database, statements[1]["settlement"]["id"])

        preview = core.preview_category_rule(self.database, "personal", "MASTERCARD AFREKENING", ["description"])
        self.assertEqual((preview["match_count"], preview["itemized_count"]), (2, 2))

        airline_import = core.import_history(self.database, "personal")[1]
        core.undo_import(self.database, airline_import["id"])
        settlements = [
            item for item in core.list_transactions(self.database, "personal", None, None, "", 500, 0)["items"]
            if item["account"] == "KBC"
        ]
        self.assertEqual([item["itemized"] for item in settlements], [False, True])
        self.assertEqual(self.counted_expenses("2025-10"), "102.84")

    def test_removed_purchases_keep_their_statement_settled(self):
        bank = "BE00000000000000000001"
        self.import_file(make_csv([
            transaction(bank, "16/01/2025", "-70,00", "930,00", "", "MASTERCARD AFREKENING NUMMER 7"),
        ]).encode())
        statement = belfius_statement_pdf(belfius_lines("07/01/2025", "16/01/2025", "08/12/2024", [
            "28/12 29/12  Shop Gent BE 50,00 EUR -",
            "03/01 04/01  Bakery Gent BE 20,00 EUR -",
        ]))
        self.import_file(statement, filename="statement.pdf")

        self.assertEqual(core.remove_transactions_before(self.database, "2025-01-01"), 1)
        self.assertEqual(self.counted_expenses("2025-01"), "20.00")
        self.assertEqual([s["month"] for s in core.report(self.database, "personal")["summaries"]], ["2025-01"])
        # Re-importing the statement does not bring the removed purchase back.
        _, result = self.import_file(statement, filename="statement.pdf")
        self.assertEqual((result["imported"], result["duplicates_skipped"]), (0, 2))
        self.assertEqual(self.counted_expenses("2025-01"), "20.00")

    def test_transfers_between_own_accounts_leave_report_totals(self):
        first, second, business = (f"BE0000000000000000000{n}" for n in (1, 2, 3))
        self.import_file(make_csv([
            {**transaction(first, "02/05/2026", "-100,00", "900,00", "Me"), "rekeningnummer tegenpartij": "BE00 0000 0000 0000 0000 02"},
            transaction(first, "03/05/2026", "-10,00", "890,00", "Shop"),
            {**transaction(first, "04/05/2026", "500,00", "1390,00", "My company"), "rekeningnummer tegenpartij": business},
        ]).encode())
        self.import_file(make_csv([
            {**transaction(second, "02/05/2026", "100,00", "100,00", "Me"), "rekeningnummer tegenpartij": first},
        ]).encode())
        self.import_file(make_csv([
            {**transaction(business, "04/05/2026", "-500,00", "0,00", "Owner"), "rekeningnummer tegenpartij": first},
        ]).encode(), account_type="business")

        summary = core.report(self.database, "personal")["summaries"][0]
        self.assertEqual((summary["income"], summary["expenses"], summary["own_transfers"]), ("500.00", "10.00", "100.00"))
        own = core.list_transactions(self.database, "personal", None, None, "", 500, 0, flow_type_filter="own_transfer")
        self.assertEqual({item["amount"] for item in own["items"]}, {"-100.00", "100.00"})
        self.assertTrue(all(item["own_transfer"] for item in own["items"]))
        expenses = core.list_transactions(self.database, "personal", None, None, "", 500, 0, flow_type_filter="expense")
        self.assertEqual([item["amount"] for item in expenses["items"]], ["-10.00"])


if __name__ == "__main__":
    unittest.main()
