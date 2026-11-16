import unittest
from datetime import date

from backend.pfm import core
from backend.pfm.core.forecast_history import take_snapshot
from backend.pfm.core.forecast_settings import _save
from tests.test_forecast import BUSINESS, PERSONAL, TODAY, ForecastFixture


class MoneyPlanFixture(ForecastFixture):
    def setUp(self):
        super().setUp()
        core.set_day_rate(self.database, "2026-01-01", 600)
        core.set_salary(self.database, "2026", 3000, 12)
        self.add(BUSINESS, "business", "Start", [(date(2026, 10, 13), 500.0, 50000.0)])
        self.add(PERSONAL, "personal", "Start", [(date(2026, 10, 13), 500.0, 3000.0)])
        for month in range(4, 10):
            self.add(BUSINESS, "business", "Shop", [(date(2026, month, 5), -200.0)], "Tools")
        self.add(BUSINESS, "business", "Pay", [(date(2026, 9, 30), -3000.0)], "Director remuneration")

    def end_year(self, year):
        # Written directly: the endpoint only accepts years counted from the real today.
        with core.connect(self.database) as db:
            _save(db, "forecast_end_year", year)

    def forecast(self, today=TODAY):
        return core.forecast(self.database, today)

    def plan(self, today=TODAY):
        return {int(row["year"]): row for row in self.forecast(today)["business"]["money_plan"]}

    def all_lines(self, entity, source=None, today=TODAY):
        return [(m["month"], line) for m in self.forecast(today)[entity]["months"] for line in m["lines"]
                if source is None or line["source"] == source]


class HorizonTests(MoneyPlanFixture):
    def test_the_forecast_runs_to_the_chosen_year_and_repeats_what_is_in_force(self):
        self.end_year(2029)
        result = self.forecast()["business"]
        self.assertEqual(result["months"][-1]["month"], "2029-12")
        self.assertEqual([y["year"] for y in result["year_ends"]], ["2026", "2027", "2028", "2029"])
        salary = {month: line["amount"] for month, line in self.all_lines("business", "salary")}
        self.assertEqual(salary["2029-12"], salary["2027-12"])
        consulting = [line["amount"] for month, line in self.all_lines("business", "consulting") if month.startswith("2029")]
        self.assertEqual(len(consulting), 12)
        text = "From 2028, salary, day rate, days off and baselines repeat the latest values."
        for entity in ("business", "personal"):
            self.assertIn(text, self.forecast()[entity]["assumptions"])

    def test_later_years_take_next_years_days_off_unless_they_have_their_own(self):
        self.end_year(2029)
        core.add_day_off(self.database, "2027-08-02", "2027-08-06")
        core.add_day_off(self.database, "2029-03-05", "2029-03-05")
        details = {month: line["detail"] for month, line in self.all_lines("business", "consulting")}
        # Work of August is paid at the end of September.
        self.assertTrue(details["2027-09"].startswith("Aug 2027: 17 days"))
        self.assertTrue(details["2028-09"].startswith("Aug 2028: 17 days"))  # 5 days off, as in 2027
        self.assertTrue(details["2029-09"].startswith("Aug 2029: 22 days"))  # 2029 has days off of its own

    def test_the_default_ends_next_year_without_the_assumption(self):
        result = self.forecast()["business"]
        self.assertEqual(result["months"][-1]["month"], "2027-12")
        self.assertFalse(any("repeat the latest" in line for line in result["assumptions"]))
        self.assertEqual(core.get_settings(self.database)["forecast_end_year"], 0)

    def test_an_end_year_before_next_year_stays_next_year(self):
        self.end_year(2026)
        self.assertEqual(self.forecast()["business"]["months"][-1]["month"], "2027-12")

    def test_the_end_year_is_validated(self):
        this_year = date.today().year
        self.assertEqual(self.settings(forecast_end_year=this_year + 3)["forecast_end_year"], this_year + 3)
        self.assertEqual(self.settings(forecast_end_year=0)["forecast_end_year"], 0)
        for bad in (this_year, this_year + 10, -1, 2.5):
            with self.assertRaises(ValueError, msg=bad):
                self.settings(forecast_end_year=bad)

    def test_the_director_loan_and_snapshots_follow_the_horizon(self):
        self.end_year(2029)
        years = [y["year"] for y in core.director_loan(self.database, TODAY)["years"]]
        self.assertEqual(years[-1], "2029")
        self.assertTrue(take_snapshot(self.database, TODAY))
        with core.connect(self.database) as db:
            months = {row[0] for row in db.execute("SELECT month FROM forecast_snapshot_lines")}
        self.assertIn("2029-12", months)


class LaterYearTaxTests(MoneyPlanFixture):
    def tax_lines(self, year):
        return [(m, line) for m, line in self.all_lines("business") if m.startswith(str(year))
                and line["category"] == "Corporate tax"]

    def test_later_years_get_four_estimated_prepayments(self):
        self.end_year(2029)
        for year in (2028, 2029):
            found = self.tax_lines(year)
            self.assertEqual([line["date"] for _, line in found],
                             [f"{year}-04-10", f"{year}-07-10", f"{year}-10-10", f"{year}-12-20"])
            self.assertTrue(all(line["category"] == "Corporate tax" and line["amount"].startswith("-") for _, line in found))
        self.assertEqual(self.tax_lines(2027), [])
        self.assertTrue(any("From 2028, corporate tax is estimated" in a for a in self.forecast()["business"]["assumptions"]))

    def test_a_year_with_a_planned_corporate_tax_item_gets_no_estimate(self):
        self.end_year(2029)
        core.add_planned(self.database, "business", "2028-12-20", -5000, self.cat["business", "Corporate tax"], "Guess")
        self.assertEqual([line["source"] for _, line in self.tax_lines(2028)], ["planned"])
        self.assertEqual(len(self.tax_lines(2029)), 4)

    def test_dividends_do_not_lower_the_profit(self):
        self.end_year(2029)
        before = [line["amount"] for _, line in self.tax_lines(2028)]
        core.add_planned(self.database, "personal", "2028-03-01", -9000, None, "Boat", paid_by_dividend=True)
        self.assertEqual([line["amount"] for _, line in self.tax_lines(2028)], before)

    def test_no_corporate_tax_role_adds_an_assumption_instead(self):
        self.end_year(2029)
        self.settings(roles={"corporate_tax": None})
        self.assertEqual(self.all_lines("business", "corporate_tax"), [])
        self.assertTrue(any("none is projected from 2028" in a for a in self.forecast()["business"]["assumptions"]))

    def test_the_corporate_tax_page_keeps_two_years(self):
        self.end_year(2029)
        self.assertEqual([y["year"] for y in core.corporate_tax(self.database, TODAY)["years"]], ["2026", "2027"])


class DividendTests(MoneyPlanFixture):
    def add_purchase(self, day="2027-03-10", amount=-1500):
        return core.add_planned(self.database, "personal", day, amount, self.cat["personal", "Groceries"], "Bicycle", paid_by_dividend=True)

    def amounts(self):
        return {e: sorted((p["date"], p["amount"], p["category"]) for p in core.list_planned(self.database, e))
                for e in ("personal", "business")}

    def test_a_net_purchase_creates_three_lines_with_the_gross_at_the_rate(self):
        created = self.add_purchase()
        self.assertEqual(len(created["created"]), 3)
        self.assertTrue(created["paid_by_dividend"])
        self.assertEqual(self.amounts(), {
            "personal": [("2027-03-10", "-1500.00", "Groceries"), ("2027-03-10", "1500.00", "Dividends")],
            "business": [("2027-03-10", "-2000.00", "Dividends")],
        })
        personal = {p["category"]: p for p in core.list_planned(self.database, "personal")}
        business = core.list_planned(self.database, "business")[0]
        self.assertEqual(personal["Dividends"]["linked"]["id"], business["id"])
        self.assertEqual(len({personal["Groceries"]["dividend_group"], personal["Dividends"]["dividend_group"], business["dividend_group"]}), 1)
        with core.connect(self.database) as db:
            flags = dict(db.execute("SELECT account_type, is_income FROM categories WHERE name = 'Dividends'").fetchall())
        self.assertEqual(flags, {"personal": 1, "business": 0})
        # The gross is stored: a later rate leaves it alone.
        self.settings(dividend_withholding_rate=50)
        self.assertEqual(self.amounts()["business"][0][1], "-2000.00")

    def test_the_gross_rounds_to_the_cent(self):
        self.add_purchase(amount="-100,01")
        self.assertEqual(self.amounts()["business"][0][1], "-133.35")

    def test_the_rate_in_force_is_used(self):
        self.settings(dividend_withholding_rate=20)
        self.add_purchase()
        self.assertEqual(self.amounts()["business"][0][1], "-1875.00")

    def test_editing_any_line_keeps_the_three_consistent(self):
        purchase = self.add_purchase()
        lines = {p["category"]: p for p in core.list_planned(self.database, "personal")}
        core.update_planned(self.database, "personal", purchase["id"], {"amount": -3000, "date": "2027-04-01", "note": "Better bike"})
        self.assertEqual(self.amounts(), {
            "personal": [("2027-04-01", "-3000.00", "Groceries"), ("2027-04-01", "3000.00", "Dividends")],
            "business": [("2027-04-01", "-4000.00", "Dividends")],
        })
        core.update_planned(self.database, "personal", lines["Dividends"]["id"], {"amount": 600})
        self.assertEqual(self.amounts()["business"], [("2027-04-01", "-800.00", "Dividends")])
        self.assertEqual(self.amounts()["personal"][0][1], "-600.00")
        business = core.list_planned(self.database, "business")[0]
        core.update_planned(self.database, "business", business["id"], {"amount": -2000, "date": "2027-05-02"})
        self.assertEqual(self.amounts(), {
            "personal": [("2027-05-02", "-1500.00", "Groceries"), ("2027-05-02", "1500.00", "Dividends")],
            "business": [("2027-05-02", "-2000.00", "Dividends")],
        })
        with self.assertRaises(ValueError):
            core.update_planned(self.database, "personal", purchase["id"], {"amount": 50})
        self.assertEqual(self.amounts()["personal"][0][1], "-1500.00")

    def test_deleting_one_line_deletes_all_three(self):
        self.add_purchase()
        other = core.add_planned(self.database, "personal", "2027-01-01", -10, None, "Other")
        business = core.list_planned(self.database, "business")[0]
        core.delete_planned(self.database, "business", business["id"])
        self.assertEqual([p["id"] for p in core.list_planned(self.database, "personal")], [other["id"]])
        self.assertEqual(core.list_planned(self.database, "business"), [])

    def test_it_is_refused_outside_a_personal_expense(self):
        for entity, amount in (("business", -100), ("personal", 100)):
            with self.assertRaises(ValueError):
                core.add_planned(self.database, entity, "2027-03-10", amount, None, "", paid_by_dividend=True)
        response = self.client.post("/api/planned", json={"account_type": "personal", "date": "2027-03-10", "amount": 5, "paid_by_dividend": True})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/planned", json={"account_type": "personal", "date": "2027-03-10", "amount": -5, "paid_by_dividend": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["created"]), 3)

    def test_an_existing_dividend_category_is_used(self):
        dividends = core.add_category(self.database, "personal", "Dividends")["id"]
        self.add_purchase()
        with core.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM categories WHERE name = 'Dividends'").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM planned_items WHERE category_id = ?", (dividends,)).fetchone()[0], 1)

    def test_the_dividend_lands_in_the_forecast_of_both_entities(self):
        self.add_purchase()
        personal = [line["amount"] for _, line in self.all_lines("personal", "planned")]
        business = [line["amount"] for _, line in self.all_lines("business", "planned")]
        self.assertEqual(sorted(personal), ["-1500.00", "1500.00"])
        self.assertEqual(business, ["-2000.00"])


class BuyerPlanTests(MoneyPlanFixture):
    def setUp(self):
        super().setUp()
        self.end_year(2028)

    def test_there_is_no_target_by_default(self):
        row = self.plan()[2027]
        self.assertEqual([row[k] for k in ("target_months", "target", "room", "room_net", "new_net")], [None] * 5)
        self.assertEqual(sorted(self.plan()), [2026, 2027, 2028])
        for entity in ("personal", "kids"):
            self.assertEqual(self.forecast()[entity]["money_plan"], [])

    def test_the_month_cost_counts_only_recurring_salary_transfer_and_baseline_outflows(self):
        core.add_planned(self.database, "business", "2027-06-01", -30000, self.cat["business", "Tools"], "Server")
        core.add_planned(self.database, "personal", "2027-03-10", -1500, None, "Bike", paid_by_dividend=True)
        result = self.forecast()["business"]
        cost = sum(
            -int(round(float(line["amount"]) * 100)) for month in result["months"] if month["month"].startswith("2027")
            for line in month["lines"]
            if line["source"] in ("recurring", "salary", "transfer", "baseline") and float(line["amount"]) < 0
            and line["category"] not in ("VAT", "Corporate tax", "Dividends")
        )
        row = self.plan()[2027]
        self.assertEqual(row["month_cost"], f"{round(cost / 12) / 100:.2f}")
        self.assertTrue(row["month_cost_derived"])
        self.assertGreater(cost, 0)
        # Three salary-free outflows alone would be lower: the salary is in it.
        self.assertGreater(float(row["month_cost"]), 3000)

    def test_transfers_that_pay_for_personal_purchases_dont_raise_the_month_cost(self):
        before = float(self.plan()[2027]["month_cost"])
        transfers_before = self.transfers_2027()
        core.add_planned(self.database, "personal", "2027-03-10", -20000, None, "Kitchen")
        # The purchase is paid by a bigger transfer, but the month of costs stays the same.
        self.assertGreater(self.transfers_2027(), transfers_before)
        self.assertEqual(float(self.plan()[2027]["month_cost"]), before)

    def transfers_2027(self):
        return -sum(float(line["amount"]) for month, line in self.all_lines("business", "transfer") if month.startswith("2027"))

    def test_the_current_year_with_few_months_left_uses_next_years_cost(self):
        late = self.plan(date(2026, 11, 20))
        self.assertEqual(late[2026]["month_cost"], late[2027]["month_cost"])

    def test_the_override_wins_and_room_follows_from_the_target(self):
        self.settings(buffer_months=[{"year": 2026, "months": 2}, {"year": 2028, "months": 3.5}], buffer_month_cost="1.000,00",
                      dividend_withholding_rate=20)
        plan = self.plan()
        self.assertEqual(core.get_settings(self.database)["buffer_month_cost"], "1000.00")
        previous = None
        for year, months in ((2026, 2), (2027, 2), (2028, 3.5)):
            row = plan[year]
            self.assertEqual((row["month_cost"], row["month_cost_derived"], row["target_months"]), ("1000.00", False, months))
            self.assertEqual(row["target"], f"{months * 1000:.2f}")
            room = float(row["closing"]) - months * 1000
            self.assertEqual(float(row["room"]), room)
            self.assertAlmostEqual(float(row["room_net"]), room * 0.8, places=2)
            if previous is None:
                self.assertIsNone(row["new_net"])
            else:
                self.assertAlmostEqual(float(row["new_net"]), float(row["room_net"]) - previous, places=2)
            previous = float(row["room_net"])

    def test_the_lowest_month_end_and_dividends_per_year(self):
        self.settings(buffer_month_cost=1000)
        core.add_planned(self.database, "personal", "2027-03-10", -1500, None, "Bike", paid_by_dividend=True)
        result = self.forecast()["business"]
        row = self.plan()[2027]
        closings = {m["month"]: float(m["closing"]) for m in result["months"] if m["month"].startswith("2027")}
        lowest = min(closings, key=lambda m: (closings[m], m))
        self.assertEqual((row["lowest_month"], row["lowest"]), (lowest, f"{closings[lowest]:.2f}"))
        self.assertEqual(row["lowest_months"], round(closings[lowest] / 1000, 1))
        self.assertEqual(row["dividends_net"], "1500.00")
        self.assertEqual(self.plan()[2028]["dividends_net"], "0.00")
        self.assertEqual(row["closing"], next(y for y in result["year_ends"] if y["year"] == "2027")["closing"])

    def test_the_buffer_settings_are_validated(self):
        for bad in ([{"year": 2027}], [{"year": 2027, "months": -1}], [{"year": 2027, "months": 2}, {"year": 2027, "months": 3}], "3"):
            with self.assertRaises(ValueError, msg=bad):
                self.settings(buffer_months=bad)
        with self.assertRaises(ValueError):
            self.settings(dividend_withholding_rate=100)
        response = self.client.put("/api/forecast/settings", json={"buffer_months": [{"year": 2027, "months": 4}], "forecast_end_year": 0})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["buffer_months"], [{"year": 2027, "months": 4}])
        self.assertEqual(self.client.put("/api/forecast/settings", json={"buffer_months": [{"year": 1}]}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
