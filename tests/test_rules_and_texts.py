import tempfile
import unittest
from pathlib import Path

from backend.pfm import core
from backend.pfm.core.describe import summarize
from backend.pfm.core.rules import Rule
from tests.test_core import make_csv, transaction


class SummaryTests(unittest.TestCase):
    def test_kbc_card_payment_names_the_shop_and_place(self):
        self.assertEqual(
            summarize(
                "",
                "BETALING VIA DEBIT MASTERCARD 12-02 11-02-2026 OM 15.06 UUR KOFFIE HUIS BE9000 GENT "
                "MET APPLE PAY 5123 45XX XXXX 6789 KAARTHOUDER: PEETERS JAN",
                "",
            ),
            ("Koffie Huis", "Card · Gent · Apple Pay"),
        )

    def test_shop_names_may_contain_short_words(self):
        name, details = summarize(
            "", "BETALING VIA MAESTRO 07-01 06-01-2026 OM 11.48 UUR DILLE EN KAMILLE BE9000 GENT MET KBC-DEBETKAART", ""
        )
        self.assertEqual((name, details), ("Dille En Kamille", "Card · Gent"))

    def test_belfius_card_payment_names_the_cardholder(self):
        self.assertEqual(
            summarize(
                "CLIMBING BVBA",
                "BANCONTACT-AANKOOP - CLIMBING BVBA - 9820 MERELBEKE BE - 10/12/25 16:57 - CONTACTLOOS VIA WALLET APPLE PAY"
                " - KAART 5123 45XX XXXX 1111 - Peeters Jan REF. : 0012345678900 VAL. 10-12",
                "BANCONTACT-AANKOOP - CLIMBING BVBA - 9820 MERELBEKE BE - 10/12/25 16:57",
            ),
            ("Climbing BVBA", "Card · Merelbeke · Apple Pay · Jan"),
        )

    def test_direct_debits_keep_the_message_without_references(self):
        self.assertEqual(
            summarize(
                "",
                "EUROPESE DOMICILIERING 07-01 SCHULDEISER : ACME FUEL NV REF. SCHULDEISER: 000000000000 "
                "MANDAATREFERTE : ABCD-1 MEDEDELING : Fuel card January",
                "",
            ),
            ("Acme Fuel NV", "Direct debit · Fuel card January"),
        )

    def test_memo_repeating_the_description_is_left_out(self):
        text = "AANMAAKKOSTEN CODA/XML REPORTING REF. : 0000000000001 VAL. 06-01"
        self.assertEqual(summarize("", text, text, "Belfius"), ("Belfius", "CODA reporting fee"))


class MatchModeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "finance.sqlite3"
        core.initialize(self.database)
        staging = {}
        account = "BE00000000000000000001"
        preview = core.stage_import(self.database, "rules.csv", make_csv([
            transaction(account, "01/05/2026", "-10,00", "90,00", "SHOP"),
            transaction(account, "02/05/2026", "-20,00", "70,00", "SHOP ONLINE"),
        ]).encode(), staging)
        core.commit_import(self.database, preview["token"], "personal", staging)
        self.category_id = core.add_category(self.database, "personal", "Shopping")["id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_exact_rules_match_whole_fields_and_contains_rules_match_parts(self):
        exact = core.preview_category_rule(self.database, "personal", " shop ", ["merchant"], match_mode="exact")
        contains = core.preview_category_rule(self.database, "personal", "shop", ["merchant"])
        self.assertEqual((exact["match_count"], contains["match_count"]), (1, 2))

        rule_id = core.save_category_rule(
            self.database, "personal", self.category_id, "shop", ["merchant"], "expense", match_mode="exact"
        )["id"]
        self.assertEqual(core.run_category_rule(self.database, "personal", rule_id), {"updated": 1, "released": 0})
        rule = next(
            rule for category in core.category_management(self.database, "personal") for rule in category["rules"]
        )
        self.assertEqual((rule["match_mode"], rule["match_count"]), ("exact", 1))

    def test_impact_lists_matches_decisions_and_changes(self):
        dining_id = core.add_category(self.database, "personal", "Dining")["id"]
        first = core.save_category_rule(self.database, "personal", self.category_id, "online", ["merchant"], "expense")["id"]
        core.run_category_rule(self.database, "personal", first)
        second = core.save_category_rule(self.database, "personal", dining_id, "shop", ["merchant"], "expense")["id"]

        impact = core.rule_impact(self.database, "personal", "shop", ["merchant"], "contains", dining_id, "expense", second)
        self.assertEqual((impact["match_count"], impact["win_count"], impact["change_count"]), (2, 1, 1))
        self.assertEqual(impact["field_counts"], {"merchant": 2, "description": 0, "memo": 0})
        by_merchant = {item["merchant"]: item for item in impact["matches"]}
        self.assertEqual(by_merchant["Shop Online"]["decided_by"], "online")
        self.assertIsNone(by_merchant["Shop"]["decided_by"])
        self.assertTrue(by_merchant["Shop"]["changes"])

    def test_narrowing_a_rule_releases_the_transactions_it_no_longer_matches(self):
        dining_id = core.add_category(self.database, "personal", "Dining")["id"]
        broad = core.save_category_rule(self.database, "personal", self.category_id, "shop", ["merchant"], "expense")["id"]
        core.run_category_rule(self.database, "personal", broad)
        core.save_category_rule(self.database, "personal", dining_id, "online", ["merchant"], "expense")

        def categories():
            items = core.list_transactions(self.database, "personal", None, None, "", 50, 0)["items"]
            return {item["merchant"]: item["category"] for item in items}

        impact = core.rule_impact(self.database, "personal", "shop", ["merchant"], "exact", self.category_id, "expense", broad)
        self.assertEqual(impact["released_count"], 1)
        self.assertEqual(
            {key: impact["released"][0][key] for key in ("merchant", "category", "new_category", "decided_by")},
            {"merchant": "Shop Online", "category": "Shopping", "new_category": "Dining", "decided_by": "online"},
        )

        # Saving alone keeps the old categories, and the preview still lists the two it would release.
        core.save_category_rule(self.database, "personal", self.category_id, "shop", ["merchant"], "expense", broad, "exact")
        self.assertEqual(categories(), {"Shop": "Shopping", "Shop Online": "Shopping"})
        impact = core.rule_impact(self.database, "personal", "shop", ["merchant"], "exact", self.category_id, "expense", broad)
        self.assertEqual(impact["released_count"], 1)

        # Applying releases it to the next matching rule.
        self.assertEqual(core.run_category_rule(self.database, "personal", broad), {"updated": 0, "released": 1})
        self.assertEqual(categories(), {"Shop": "Shopping", "Shop Online": "Dining"})

        # With no other rule matching, applying releases to Uncategorized.
        core.save_category_rule(self.database, "personal", self.category_id, "nothing", ["merchant"], "expense", broad)
        self.assertEqual(core.run_category_rule(self.database, "personal", broad), {"updated": 0, "released": 1})
        self.assertEqual(categories()["Shop"], "Uncategorized")

    def test_similar_transactions_suggest_the_most_used_category(self):
        staging = {}
        account = "BE00000000000000000001"
        preview = core.stage_import(self.database, "more.csv", make_csv([
            transaction(account, "03/05/2026", "-5,00", "65,00", "shop"),
            transaction(account, "04/05/2026", "-6,00", "59,00", "SHOP"),
        ]).encode(), staging)
        core.commit_import(self.database, preview["token"], "personal", staging)
        items = {item["booking_date"]: item for item in core.list_transactions(self.database, "personal", None, None, "", 50, 0)["items"]}
        core.update_transaction(self.database, items["2026-05-03"]["id"], {"category_id": self.category_id})

        similar = core.similar_transactions(self.database, items["2026-05-04"]["id"])
        self.assertEqual(similar["merchant"], "Shop")
        self.assertEqual(similar["suggestion"], {"category_id": self.category_id, "name": "Shopping", "count": 1})
        self.assertEqual({other["id"] for other in similar["others"]}, {items["2026-05-01"]["id"], items["2026-05-03"]["id"]})
        self.assertIsNone(core.similar_transactions(self.database, items["2026-05-02"]["id"])["suggestion"])
        self.assertEqual(similar["merchant_count"], 2)

    def test_similar_transactions_keep_one_merchants_kinds_apart(self):
        staging = {}
        account = "BE00000000000000000001"
        preview = core.stage_import(self.database, "bank.csv", make_csv([
            transaction(account, "05/05/2026", "-900,00", "100,00", "Bank", "BEDRAG VOOR DE AFBETALING VAN UW WOONKREDIET"),
            transaction(account, "06/05/2026", "-1,00", "99,00", "Bank", "INTERESTEN : 01.04.2026 - 30.06.2026"),
        ]).encode(), staging)
        core.commit_import(self.database, preview["token"], "personal", staging)
        items = {item["booking_date"]: item for item in core.list_transactions(self.database, "personal", None, None, "", 50, 0)["items"]}
        core.update_transaction(self.database, items["2026-05-05"]["id"], {"category_id": self.category_id})

        similar = core.similar_transactions(self.database, items["2026-05-06"]["id"])
        self.assertEqual((similar["kind"], similar["others"], similar["suggestion"]), ("Interest", [], None))
        self.assertEqual(similar["merchant_count"], 1)

    def test_word_rules_match_whole_words_only(self):
        def matches(text, value, **options):
            return Rule(None, text, ("merchant",), match_mode="word", **options).matches({"merchant": value})

        for value in ("Zara 392", "ZARA.COM", "Shop Zara", "zara-kids", "Zara", "zara_home"):
            self.assertTrue(matches("Zara", value), value)
        for value in ("Zarakine", "Bizarre", "Zara1", "1Zara", ""):
            self.assertFalse(matches("Zara", value), value)
        self.assertTrue(matches("H&M", "H&M Be0227"))
        self.assertTrue(matches("30cc", "Pil 30cc"))
        self.assertFalse(matches("30cc", "130cc"))
        self.assertTrue(matches("Jan Peeters", "To Jan Peeters"))
        self.assertTrue(matches("Jan  Peeters", "to  jan\tpeeters"))
        self.assertFalse(matches("Jan Peeters", "Jan Peetersx"))
        self.assertFalse(matches("   ", "anything"))

    def test_word_rules_are_unicode_aware(self):
        def matches(text, value):
            return Rule(None, text, ("merchant",), match_mode="word").matches({"merchant": value})

        self.assertTrue(matches("Café", "Café Noir"))
        self.assertTrue(matches("café", "CAFÉ-NOIR"))
        self.assertFalse(matches("Café", "Cafés"))
        self.assertFalse(matches("Caf", "Café Noir"))
        self.assertTrue(matches("2026", "Invoice 2026-05"))
        self.assertFalse(matches("2026", "Invoice 20261"))

    def test_word_rules_work_in_preview_impact_and_save(self):
        staging = {}
        preview = core.stage_import(self.database, "more.csv", make_csv([
            transaction("BE00000000000000000001", "03/05/2026", "-5,00", "65,00", "Shopify"),
            transaction("BE00000000000000000001", "04/05/2026", "-6,00", "59,00", "Bishop Shop 12"),
        ]).encode(), staging)
        core.commit_import(self.database, preview["token"], "personal", staging)

        contains = core.preview_category_rule(self.database, "personal", "shop", ["merchant"])
        word = core.preview_category_rule(self.database, "personal", "shop", ["merchant"], match_mode="word")
        self.assertEqual((contains["match_count"], word["match_count"]), (4, 3))

        impact = core.rule_impact(self.database, "personal", "shop", ["merchant"], "word", self.category_id, "expense")
        self.assertEqual((impact["match_count"], impact["win_count"]), (3, 3))
        self.assertEqual(impact["field_counts"], {"merchant": 3, "description": 0, "memo": 0})
        self.assertNotIn("Shopify", [item["merchant"] for item in impact["matches"]])

        rule_id = core.save_category_rule(
            self.database, "personal", self.category_id, "shop", ["merchant"], "expense", match_mode="word"
        )["id"]
        self.assertEqual(core.run_category_rule(self.database, "personal", rule_id), {"updated": 3, "released": 0})
        rule = next(
            rule for category in core.category_management(self.database, "personal") for rule in category["rules"]
        )
        self.assertEqual((rule["match_mode"], rule["match_count"]), ("word", 3))

    def test_rejects_unknown_match_modes(self):
        with self.assertRaisesRegex(ValueError, "Match mode"):
            core.preview_category_rule(self.database, "personal", "shop", ["merchant"], match_mode="regex")


if __name__ == "__main__":
    unittest.main()
