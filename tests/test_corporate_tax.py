import unittest
from datetime import date

from backend.pfm import core
from backend.pfm.core.corporate_tax import _advice, _deadline_slot, _surcharge
from backend.pfm.core.forecast_settings import load_settings
from tests.test_forecast import BUSINESS, TODAY, ForecastFixture


class TaxFixture(ForecastFixture):
    def setUp(self):
        super().setUp()
        self.cat["business", "Loan"] = core.add_category(self.database, "business", "Loan")["id"]

    def book(self, merchant, category, when, amount):
        self.add(BUSINESS, "business", merchant, [(when, amount)], category)

    def loaded(self):
        with core.connect(self.database) as db:
            return load_settings(db)

    def result(self, today=TODAY):
        return core.corporate_tax(self.database, today)

    def year(self, year="2026", today=TODAY):
        return next(y for y in self.result(today)["years"] if y["year"] == year)

    def book_year(self):
        """Net income 100,000, salary 6,000, tools 4,000: profit 90,000. Left out: VAT, tax, transfer, loan."""
        self.book("Client", "Consulting income", date(2026, 2, 15), 121000.0)
        self.book("Pay", "Director remuneration", date(2026, 3, 31), -6000.0)
        self.book("Shop", "Tools", date(2026, 5, 10), -4000.0)
        self.book("Tax office VAT", "VAT", date(2026, 4, 15), -21000.0)
        self.book("Transfer", "Entity transfer", date(2026, 6, 1), -500.0)
        self.book("Bank loan", "Loan", date(2026, 7, 1), 1000.0)


class ProfitTests(TaxFixture):
    def test_profit_leaves_out_role_and_excluded_categories_and_nets_the_vat(self):
        self.book_year()
        self.settings(tax_profit_excluded=[self.cat["business", "Loan"]])
        year = self.year()
        self.assertEqual(year["profit"], "90000.00")
        self.assertEqual(year["salary"], "6000.00")
        self.assertEqual([(b["category"], b["amount"]) for b in year["breakdown"]],
                         [("Consulting income", "100000.00"), ("Director remuneration", "-6000.00"), ("Tools", "-4000.00")])

    def test_an_unexcluded_loan_counts_at_its_signed_amount(self):
        self.book_year()
        self.assertEqual(self.year()["profit"], "91000.00")

    def test_reduced_rate_when_the_salary_reaches_the_threshold(self):
        self.book_year()
        self.settings(tax_profit_excluded=[self.cat["business", "Loan"]], tax_reduced_threshold="6.000,00")
        year = self.year()
        self.assertTrue(year["eligible"])
        self.assertEqual(year["tax"], "18000.00")  # 20% of 90,000
        self.settings(tax_reduced_threshold="6.000,01")
        year = self.year()
        self.assertFalse(year["eligible"])
        self.assertEqual(year["tax"], "22500.00")  # 25% of 90,000

    def test_the_reduced_rate_applies_to_the_band_only(self):
        self.book_year()
        self.settings(tax_profit_excluded=[self.cat["business", "Loan"]], tax_reduced_threshold=5000, tax_reduced_band=50000)
        self.assertEqual(self.year()["tax"], "20000.00")  # 20% of 50,000 + 25% of 40,000

    def test_a_loss_has_no_tax(self):
        self.book("Shop", "Tools", date(2026, 5, 10), -500.0)
        year = self.year()
        self.assertEqual((year["profit"], year["tax"]), ("-500.00", "0.00"))

    def test_next_year_starts_empty_and_the_years_are_this_and_next(self):
        self.book_year()
        years = self.result()["years"]
        self.assertEqual([y["year"] for y in years], ["2026", "2027"])
        self.assertEqual(years[1]["tax"], "0.00")
        self.assertEqual(len(years[1]["deadlines"]), 4)

    def test_projected_lines_of_the_remaining_months_count(self):
        self.book("Pay", "Director remuneration", date(2026, 3, 31), -6000.0)
        core.set_salary(self.database, "2026", 1000, 12)
        year = self.year()
        # Booked 6,000 plus the projected salary of October (current month) to December: 3 x 1,000.
        self.assertEqual(year["salary"], "9000.00")
        self.assertEqual(year["profit"], "-9000.00")


class PrepaymentTests(TaxFixture):
    def setUp(self):
        super().setUp()
        self.book_year()
        self.settings(tax_profit_excluded=[self.cat["business", "Loan"]], tax_reduced_threshold=5000)  # tax 18,000
        for when, amount in [(date(2026, 3, 20), -1000.0), (date(2026, 4, 10), -500.0), (date(2026, 4, 11), -200.0),
                             (date(2026, 10, 12), -400.0), (date(2026, 12, 21), -300.0)]:
            self.book(f"Tax {when}", "Corporate tax", when, amount)
        core.add_planned(self.database, "business", "2026-12-15", -700, self.cat["business", "Corporate tax"], "Guess")

    def test_deadline_slots(self):
        self.assertEqual([_deadline_slot(date(2026, *md)) for md in ((1, 1), (4, 10), (4, 11), (7, 10), (10, 10), (10, 11), (12, 20))],
                         [0, 0, 1, 1, 2, 3, 3])
        self.assertIsNone(_deadline_slot(date(2026, 12, 21)))

    def test_payments_count_toward_the_first_deadline_on_or_after_their_date(self):
        year = self.year()
        self.assertEqual([d["prepaid"] for d in year["deadlines"]], ["1500.00", "200.00", "0.00", "400.00"])
        self.assertEqual([d["planned"] for d in year["deadlines"]], ["0.00", "0.00", "0.00", "700.00"])
        self.assertEqual([d["past"] for d in year["deadlines"]], [True, True, True, False])
        self.assertEqual((year["prepaid_total"], year["planned_total"]), ("2100.00", "700.00"))  # 300 after 20 Dec ignored

    def test_surcharge_as_planned_by_hand(self):
        # 18,000 x 6.75% = 1,215; credits 1,500 x 9% + 200 x 7.5% + 1,100 (400 + 700) x 4.5% = 135 + 15 + 49.50.
        self.assertEqual(self.year()["surcharge_as_planned"], "1015.50")

    def test_surcharge_is_never_negative_and_can_be_switched_off(self):
        settings = self.loaded()
        self.assertEqual(_surcharge(settings, 1000000, [0, 0, 0, 0]), 67500)
        self.assertEqual(_surcharge(settings, 1000000, [0, 0, 0, 100000000]), 0)
        self.assertEqual(_surcharge({**settings, "tax_surcharge_applies": False}, 1000000, [0, 0, 0, 0]), 0)
        self.settings(tax_surcharge_applies=False)
        self.assertEqual(self.year()["surcharge_as_planned"], "0.00")

    def test_advice_with_one_remaining_deadline_counts_what_is_prepaid_and_tops_up(self):
        # Unpaid 18,000 - 2,100 = 15,900 on 20 December; surcharge 331.50 left, / 4.5% = 7,366.67 -> 7,367 more.
        self.assertEqual([d["advised"] for d in self.year()["deadlines"]], ["0.00", "0.00", "0.00", "23267.00"])

    def test_the_year_is_closed_after_the_last_deadline(self):
        year = self.year(today=date(2026, 12, 21))
        self.assertTrue(year["closed"])
        self.assertEqual([d["advised"] for d in year["deadlines"]], ["0.00"] * 4)
        self.assertTrue(any("closed" in a for a in year["assumptions"]))


class AdviceTests(TaxFixture):
    """Tax 10,000, base rate 3%: the surcharge is 675 and the credits are 9%, 7.5%, 6% and 4.5%."""

    def advice(self, remaining):
        settings = self.loaded()
        advised = _advice(settings, 1000000, [0, 0, 0, 0], remaining)
        credit = sum(a * r for a, r in zip(advised, (9, 7.5, 6, 4.5)))
        if remaining:
            self.assertGreaterEqual(credit / 100 / 100, 675 - 1e-9)  # in euros: cents x percent / 100
        return [a // 100 for a in advised]

    def test_four_remaining_deadlines_split_evenly_with_no_surcharge(self):
        self.assertEqual(self.advice([0, 1, 2, 3]), [2500, 2500, 2500, 2500])

    def test_three_remaining_deadlines(self):
        # 3,334 each; credits 600.12; surcharge 74.88 / 7.5% = 998.4 -> 999 more on 10 July.
        self.assertEqual(self.advice([1, 2, 3]), [0, 4333, 3334, 3334])

    def test_two_remaining_deadlines(self):
        # 5,000 each; credits 525; surcharge 150 / 6% = 2,500 more on 10 October.
        self.assertEqual(self.advice([2, 3]), [0, 0, 7500, 5000])

    def test_one_remaining_deadline(self):
        # 10,000; credit 450; surcharge 225 / 4.5% = 5,000 more.
        self.assertEqual(self.advice([3]), [0, 0, 0, 15000])

    def test_without_a_surcharge_the_advice_is_the_spread_only(self):
        settings = {**self.loaded(), "tax_surcharge_applies": False}
        self.assertEqual(_advice(settings, 1000000, [0, 0, 0, 0], [2, 3]), [0, 0, 500000, 500000])

    def test_no_remaining_deadlines_no_advice(self):
        self.assertEqual(self.advice([]), [0, 0, 0, 0])


class ApplyTests(TaxFixture):
    def setUp(self):
        super().setUp()
        self.book_year()
        self.settings(tax_profit_excluded=[self.cat["business", "Loan"]], tax_reduced_threshold=5000)
        self.book("Tax", "Corporate tax", date(2026, 10, 12), -400.0)
        tax = self.cat["business", "Corporate tax"]
        for when, amount, category, note in [
            ("2026-12-15", -700, tax, "Guess"), ("2026-10-01", -50, tax, "Past"), ("2027-03-01", -90, tax, "Next year"),
            ("2026-11-20", -30, self.cat["business", "Tools"], "Other category"),
        ]:
            core.add_planned(self.database, "business", when, amount, category, note)

    def planned(self):
        return sorted((p["date"], p["amount"], p["note"]) for p in core.list_planned(self.database, "business"))

    def test_apply_replaces_only_this_years_future_corporate_tax_items(self):
        # Tax 17,994 (the planned Tools -30 lowers the profit to 89,970); unpaid 17,594 -> 17,600 on 20 December;
        # surcharge 17,994 x 6.75% - 18,000 x 4.5% = 404.595 / 4.5% = 8,991 -> 8,992 more.
        result = core.apply_corporate_tax(self.database, "2026", TODAY)
        self.assertEqual(self.planned(), [
            ("2026-10-01", "-50.00", "Past"), ("2026-11-20", "-30.00", "Other category"),
            ("2026-12-20", "-26592.00", "Corporate tax prepayment (advised)"), ("2027-03-01", "-90.00", "Next year"),
        ])
        year = next(y for y in result["years"] if y["year"] == "2026")
        self.assertEqual(year["planned_total"], "26592.00")
        self.assertEqual(year["surcharge_as_planned"], "0.00")

    def test_applying_next_years_advice_keeps_this_years_items(self):
        tax = self.cat["business", "Corporate tax"]
        core.add_planned(self.database, "business", "2026-12-20", -15000, tax, "This year's guess")
        core.apply_corporate_tax(self.database, "2027", TODAY)
        planned = self.planned()
        self.assertIn(("2026-12-20", "-15000.00", "This year's guess"), planned)
        # The 2027 corporate tax item is replaced by the advice, which is nothing here: the fixture books only 2026.
        self.assertNotIn(("2027-03-01", "-90.00", "Next year"), planned)

    def test_apply_is_repeatable_and_rejects_other_years(self):
        core.apply_corporate_tax(self.database, "2026", TODAY)
        core.apply_corporate_tax(self.database, "2026", TODAY)
        self.assertEqual(len([p for p in self.planned() if p[2].endswith("(advised)")]), 1)
        with self.assertRaises(ValueError):
            core.apply_corporate_tax(self.database, "2025", TODAY)

    def test_apply_needs_the_corporate_tax_category(self):
        self.settings(roles={"corporate_tax": None})
        with self.assertRaises(ValueError):
            core.apply_corporate_tax(self.database, "2026", TODAY)


class SettingsAndApiTests(TaxFixture):
    def test_defaults_and_validation(self):
        settings = core.get_settings(self.database)
        self.assertEqual((settings["tax_reduced_threshold"], settings["tax_reduced_band"]), ("51000.00", "100000.00"))
        self.assertEqual((settings["tax_reduced_rate"], settings["tax_standard_rate"], settings["tax_base_rate"]), (20, 25, 3))
        self.assertEqual((settings["tax_surcharge_applies"], settings["tax_profit_excluded"]), (True, []))
        for bad in ({"tax_base_rate": 21}, {"tax_standard_rate": 101}, {"tax_reduced_rate": -1}, {"tax_surcharge_applies": "yes"},
                    {"tax_reduced_threshold": "abc"}, {"tax_profit_excluded": [self.cat["personal", "Salary"]]},
                    {"tax_profit_excluded": 4}):
            with self.assertRaises(ValueError, msg=bad):
                self.settings(**bad)

    def test_excluded_categories_follow_a_merge_and_a_removal(self):
        loan, tools = self.cat["business", "Loan"], self.cat["business", "Tools"]
        self.settings(tax_profit_excluded=[loan, tools])
        core.merge_categories(self.database, "business", loan, tools)
        self.assertEqual(core.get_settings(self.database)["tax_profit_excluded"], [tools])
        core.remove_category(self.database, tools)
        self.assertEqual(core.get_settings(self.database)["tax_profit_excluded"], [])

    def test_endpoints(self):
        self.book_year()
        self.assertEqual(self.client.put("/api/forecast/settings", json={"tax_base_rate": 2.5, "tax_reduced_band": "50.000,00"}).status_code, 200)
        body = self.client.get("/api/forecast/corporate-tax").json()
        self.assertEqual([y["year"] for y in body["years"]], [str(date.today().year), str(date.today().year + 1)])
        self.assertEqual(self.client.post("/api/forecast/corporate-tax/1999/apply").status_code, 400)
        self.assertEqual(self.client.put("/api/forecast/settings", json={"tax_base_rate": 30}).status_code, 400)
        year = str(date.today().year)
        self.assertEqual(self.client.post(f"/api/forecast/corporate-tax/{year}/apply").status_code, 200)


if __name__ == "__main__":
    unittest.main()
