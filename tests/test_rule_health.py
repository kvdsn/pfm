import tempfile
import unittest
from pathlib import Path

from backend.pfm import core
from tests.test_core import make_csv, transaction

ACCOUNT = "BE00000000000000000001"

class RuleHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "finance.sqlite3"
        core.initialize(self.database)
        staging = {}
        rows = [
            transaction(ACCOUNT, f"{day:02d}/05/2026", "-10,00", "90,00", merchant, description)
            for day, (merchant, description) in enumerate([
                ("Marie", ""),
                ("Marie", ""),
                ("Marie", ""),
                ("Marie Cafe", ""),
                ("Marie Cafe", ""),
                ("Marie Cafe", ""),
                ("Kiosk", ""),
                ("Zinc", ""),
            ], start=1)
        ]
        preview = core.stage_import(self.database, "health.csv", make_csv(rows).encode(), staging)
        core.commit_import(self.database, preview["token"], "personal", staging)
        self.category_id = core.add_category(self.database, "personal", "Shopping")["id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def save(self, text, mode="contains"):
        return core.save_category_rule(
            self.database, "personal", self.category_id, text, ["merchant"], "expense", match_mode=mode
        )["id"]

    def rules(self):
        return {
            rule["id"]: rule
            for category in core.category_management(self.database, "personal")
            for rule in category["rules"]
        }

    def test_healthy_rule_has_no_signals(self):
        rule_id = self.save("Kiosk", "exact")
        rule = self.rules()[rule_id]
        self.assertEqual((rule["health"], rule["shadowed_by"]), ([], []))

    def test_idle_rule_decides_nothing(self):
        rule_id = self.save("Nothing matches this")
        self.assertEqual(self.rules()[rule_id]["health"], ["idle"])

    def test_shadowed_rule_names_the_earlier_rules_by_matches_taken(self):
        cafe = self.save("Marie Cafe", "exact")
        self.save("Marie Ca")  # takes nothing: the first rule decides all of its matches
        plain = self.save("Marie", "exact")
        broad = self.save("Marie")
        rules = self.rules()
        self.assertEqual((rules[broad]["match_count"], rules[broad]["first_match_count"]), (6, 0))
        self.assertEqual(
            rules[broad]["shadowed_by"],
            [
                {"id": cafe, "match_text": "Marie Cafe", "count": 3},
                {"id": plain, "match_text": "Marie", "count": 3},
            ],
        )
        self.assertEqual(rules[broad]["health"], ["idle", "shadowed"])

    def test_shadowed_by_is_ordered_by_count_then_priority_and_capped_at_three(self):
        kiosk = self.save("Kiosk", "exact")          # decides 1
        cafe = self.save("Marie Cafe", "exact")     # decides 3
        self.save("Zinc", "exact")                   # decides 1, but the cap cuts it
        plain = self.save("Marie", "exact")         # decides 3
        everything = self.save("i")
        shadowed = self.rules()[everything]
        self.assertEqual(shadowed["match_count"], 8)
        self.assertEqual(
            [(item["id"], item["count"]) for item in shadowed["shadowed_by"]],
            [(cafe, 3), (plain, 3), (kiosk, 1)],
        )

    def test_partly_shadowed_rule_still_wins_the_rest(self):
        cafe = self.save("Marie Cafe", "exact")
        broad = self.save("Marie")
        rule = self.rules()[broad]
        self.assertEqual((rule["match_count"], rule["first_match_count"]), (6, 3))
        self.assertEqual(rule["shadowed_by"], [{"id": cafe, "match_text": "Marie Cafe", "count": 3}])
        self.assertEqual(rule["health"], ["shadowed"])

    def test_broad_means_a_short_contains_rule(self):
        short = self.save(" Nad ")
        four = self.save("Kios")
        five = self.save("Kiosk")
        exact = self.save("Kio", "exact")
        word = self.save("Kio", "word")
        rules = self.rules()
        self.assertIn("broad", rules[short]["health"])
        self.assertIn("broad", rules[four]["health"])
        self.assertNotIn("broad", rules[five]["health"])
        self.assertNotIn("broad", rules[exact]["health"])
        self.assertNotIn("broad", rules[word]["health"])
