import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.pfm import core
from backend.pfm.core.transactions import AmountCondition, SearchQuery, parse_amount_cents
from tests.test_core import make_csv, transaction

ACCOUNT = "BE00000000000000000001"


class ParseSearchTests(unittest.TestCase):
    def test_amount_notations(self):
        for text, cents in [
            ("41", 4100), ("41,5", 4150), ("41.50", 4150), ("41,00", 4100), ("1.250,00", 125000),
            ("1,250.00", 125000), ("1250.5", 125050), ("1.250", 125000), ("1,250", 125000),
            ("1.250.000", 125000000), ("0,5", 50), ("0.50", 50),
        ]:
            self.assertEqual(parse_amount_cents(text), cents, text)

    def test_not_amounts(self):
        for text in ["12.05.2026", "41.55,5", "1.25.0", "41,555,5", "1,250,00", "0.250", "abc", "41.5555", "1.2345"]:
            self.assertIsNone(parse_amount_cents(text), text)

    def test_unsigned_amount_keeps_the_text_match(self):
        for text, cents in [("41", 4100), ("41,5", 4150), ("41.50", 4150), ("1.250,00", 125000), ("1250.5", 125050)]:
            self.assertEqual(
                parse_search(text), SearchQuery(text, AmountCondition(cents, cents, False), match_any=True), text
            )

    def test_currency_symbols(self):
        expected = AmountCondition(4100, 4100, False)
        self.assertEqual(parse_search("€41"), SearchQuery("41", expected, match_any=True))
        self.assertEqual(parse_search("41 €"), SearchQuery("41", expected, match_any=True))
        self.assertEqual(parse_search("41€"), SearchQuery("41", expected, match_any=True))

    def test_signed_amounts(self):
        self.assertEqual(parse_search("-41,00"), SearchQuery("-41,00", AmountCondition(-4100, -4100, True), True))
        self.assertEqual(parse_search("+41"), SearchQuery("+41", AmountCondition(4100, 4100, True), True))

    def test_ranges_do_not_match_text(self):
        unsigned = AmountCondition(4000, 5000, False)
        for text in ["40..50", "40-50", "40 - 50", "40 .. 50", "50..40", "€40..50"]:
            self.assertEqual(parse_search(text), SearchQuery("", unsigned), text)
        self.assertEqual(parse_search("40,5..60"), SearchQuery("", AmountCondition(4050, 6000, False)))
        self.assertEqual(parse_search("-50..-40"), SearchQuery("", AmountCondition(-5000, -4000, True)))
        self.assertEqual(parse_search("1.000..2.000,50"), SearchQuery("", AmountCondition(100000, 200050, False)))

    def test_mixed_queries(self):
        self.assertEqual(parse_search("bol 25"), SearchQuery("bol", AmountCondition(2500, 2500, False)))
        self.assertEqual(parse_search("hema 41,50"), SearchQuery("hema", AmountCondition(4150, 4150, False)))
        self.assertEqual(parse_search("-12 bol com"), SearchQuery("bol com", AmountCondition(-1200, -1200, True)))
        self.assertEqual(parse_search("bol 40..50"), SearchQuery("bol", AmountCondition(4000, 5000, False)))

    def test_plain_text_fallbacks(self):
        for text in ["", "hema", "hema utrecht", "12 34", "bol 12 34", "12.05.2026", "abc 41.5555", "12 34 abc"]:
            self.assertEqual(parse_search(text), SearchQuery(text.strip()), text)


def parse_search(text):
    return core.parse_search(text)


class AmountSearchTests(unittest.TestCase):
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
        rows = [
            transaction(ACCOUNT, "01/05/2026", "-41,50", "1000,00", "Hema", "ONE"),
            transaction(ACCOUNT, "02/05/2026", "41,50", "1041,50", "Refund Hema", "TWO"),
            transaction(ACCOUNT, "03/05/2026", "-41,00", "1000,50", "Bol", "THREE"),
            transaction(ACCOUNT, "04/05/2026", "-45,00", "955,50", "Bol", "FOUR"),
            transaction(ACCOUNT, "05/05/2026", "-4411,00", "-3455,50", "Rent", "FIVE"),
            transaction(ACCOUNT, "06/05/2026", "-1,00", "-3456,50", "Shop 4411", "SIX"),
            transaction(ACCOUNT, "07/05/2026", "1250,00", "-2206,50", "Salary", "SEVEN"),
            transaction(ACCOUNT, "08/05/2026", "-60,00", "-2266,50", "Bol", "EIGHT"),
        ]
        staging = {}
        preview = core.stage_import(self.database, "export.csv", make_csv(rows).encode(), staging)
        core.commit_import(self.database, preview["token"], "personal", staging)

    def search(self, query, **kwargs):
        result = core.list_transactions(self.database, "personal", None, kwargs.pop("year", None), query, kwargs.pop("limit", 100), kwargs.pop("offset", 0), **kwargs)
        return result["total"], sorted(item["description"] for item in result["items"])

    def test_unsigned_matches_both_directions(self):
        self.assertEqual(self.search("41,50"), (2, ["ONE", "TWO"]))
        self.assertEqual(self.search("41.5"), (2, ["ONE", "TWO"]))
        self.assertEqual(self.search("€41"), (2, ["SIX", "THREE"]))
        self.assertEqual(self.search("41 €"), (2, ["SIX", "THREE"]))
        self.assertEqual(self.search("1.250"), (1, ["SEVEN"]))

    def test_signed_matches_one_direction(self):
        self.assertEqual(self.search("-41,50"), (1, ["ONE"]))
        self.assertEqual(self.search("+41,50"), (1, ["TWO"]))
        self.assertEqual(self.search("-41,00"), (1, ["THREE"]))
        self.assertEqual(self.search("+1250"), (1, ["SEVEN"]))

    def test_number_keeps_matching_text(self):
        self.assertEqual(self.search("4411"), (2, ["FIVE", "SIX"]))

    def test_ranges(self):
        self.assertEqual(self.search("40..50"), (4, ["FOUR", "ONE", "THREE", "TWO"]))
        self.assertEqual(self.search("40 - 41"), (1, ["THREE"]))
        self.assertEqual(self.search("-50..-41,5"), (2, ["FOUR", "ONE"]))
        self.assertEqual(self.search("41,5..60"), (4, ["EIGHT", "FOUR", "ONE", "TWO"]))
        self.assertEqual(self.search("4411..4411"), (1, ["FIVE"]))

    def test_mixed(self):
        self.assertEqual(self.search("hema 41,50"), (2, ["ONE", "TWO"]))
        self.assertEqual(self.search("hema -41,50"), (1, ["ONE"]))
        self.assertEqual(self.search("bol 40..50"), (2, ["FOUR", "THREE"]))
        self.assertEqual(self.search("bol 99"), (0, []))

    def test_total_ignores_paging_and_respects_other_filters(self):
        total, descriptions = self.search("40..50", limit=2, offset=1)
        self.assertEqual((total, len(descriptions)), (4, 2))
        self.assertEqual(self.search("40..50", year="2025"), (0, []))
        self.assertEqual(self.search("40..50", year="2026")[0], 4)
        self.assertEqual(self.search("41,50", flow_type_filter="expense"), (1, ["ONE"]))


if __name__ == "__main__":
    unittest.main()


class ShortNumberSearchTests(unittest.TestCase):
    def test_short_numbers_skip_descriptions_but_long_ones_search_them(self):
        with patch.dict(os.environ), tempfile.TemporaryDirectory() as folder:
            os.environ.pop("PFM_BACKUP_DIR", None)
            database = Path(folder) / "finance.sqlite3"
            core.initialize(database)
            staging = {}
            account = "BE00000000000000000001"
            preview = core.stage_import(database, "s.csv", make_csv([
                transaction(account, "01/05/2026", "-41,00", "59,00", "SHOP"),
                transaction(account, "02/05/2026", "-3,00", "56,00", "CAFE", "REF 1241 CARD 4471"),
            ]).encode(), staging)
            core.commit_import(database, preview["token"], "personal", staging)
            found = lambda q: [i["amount"] for i in core.list_transactions(database, "personal", None, None, q, 50, 0)["items"]]
            self.assertEqual(found("41"), ["-41.00"])
            self.assertEqual(found("4471"), ["-3.00"])

