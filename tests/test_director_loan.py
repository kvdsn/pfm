from datetime import date

from backend.pfm import core
from tests.test_forecast import BUSINESS, PERSONAL, TODAY, ForecastFixture


class LoanFixture(ForecastFixture):
    def loan(self):
        return core.director_loan(self.database, TODAY)

    def year(self, year):
        return next(y for y in self.loan()["years"] if y["year"] == year)

    def transfer(self, when, amount, balance=5000.0):
        self.add(PERSONAL, "personal", f"Transfer {when}", [(when, amount, balance)], "Entity transfer")

    def interest_categories(self):
        for entity in ("personal", "business"):
            self.cat[entity, "Director loan interest"] = core.add_category(self.database, entity, "Director loan interest")["id"]


class BalanceTests(LoanFixture):
    def test_transfers_from_the_start_add_up_with_simple_interest_per_year(self):
        # Personal stays above its minimum and there is no Business account, so nothing is projected.
        self.transfer(date(2025, 12, 1), 1000.0)  # before the loan start: left out
        self.transfer(date(2026, 8, 2), 7000.0)
        result = self.loan()
        self.assertEqual(result["balance"], "7000.00")
        self.assertEqual([m["amount"] for m in result["movements"]], ["7000.00"])
        # 7,000 × 3.5% × 152/365 (2 August to 31 December), then a whole year.
        self.assertEqual(self.year("2026")["interest"], "102.03")
        self.assertEqual(self.year("2027")["interest"], "245.00")
        self.assertEqual(self.year("2027")["opening"], "7000.00")
        self.assertEqual(self.year("2027")["interest_unpaid"], "347.03")
        # To today (25 October): 85 days.
        self.assertEqual(result["interest_to_date"], "57.05")

    def test_money_back_to_business_lowers_the_balance_and_a_negative_one_earns_nothing(self):
        self.transfer(date(2026, 3, 1), -1000.0)
        self.transfer(date(2026, 7, 1), 1000.0)
        self.assertEqual(self.year("2026")["closing"], "0.00")
        self.assertEqual(self.year("2026")["interest"], "0.00")

    def test_rate_and_start_follow_the_settings(self):
        self.transfer(date(2026, 1, 15), 1000.0)
        self.settings(loan_start="2026-02-01", loan_interest_rate=10)
        self.assertEqual(self.loan()["balance"], "0.00")
        self.settings(loan_start="2026-01-01")
        self.assertEqual(self.year("2027")["interest"], "100.00")

    def test_projected_transfers_add_to_the_balance(self):
        self.add(PERSONAL, "personal", "Start", [(date(2026, 10, 13), 500.0, 500.0)])
        self.add(BUSINESS, "business", "Start2", [(date(2026, 10, 13), 500.0, 10000.0)])
        row = self.year("2026")
        self.assertEqual((row["transfers"], row["transfers_projected"], row["closing"]), ("0.00", "1500.00", "1500.00"))
        self.assertEqual(self.loan()["movements"][0], {"date": "2026-10-31", "amount": "1500.00", "projected": True, "balance": "1500.00"})


class InterestTests(LoanFixture):
    def test_planning_interest_adds_a_linked_pair_counted_as_planned(self):
        self.interest_categories()
        self.transfer(date(2026, 1, 1), 10000.0)
        result = core.plan_loan_interest(self.database, "2026-12-31", "350", None, TODAY)
        row = next(y for y in result["years"] if y["year"] == "2026")
        self.assertEqual((row["interest"], row["interest_planned"], row["interest_unpaid"]), ("350.00", "350.00", "0.00"))
        business = core.list_planned(self.database, "business")[0]
        personal = core.list_planned(self.database, "personal")[0]
        self.assertEqual((business["amount"], business["category"], business["note"]), ("350.00", "Director loan interest", "Director loan interest"))
        self.assertEqual(personal["amount"], "-350.00")
        self.assertEqual(business["linked"]["id"], personal["id"])
        # Business counts it as income in its taxable profit.
        tax = core.corporate_tax(self.database, TODAY)["years"][0]
        self.assertIn({"category": "Director loan interest", "amount": "350.00"}, tax["breakdown"])

    def test_booked_interest_counts_as_paid(self):
        self.interest_categories()
        self.transfer(date(2026, 1, 1), 10000.0)
        self.add(BUSINESS, "business", "Interest", [(date(2026, 6, 30), 100.0)], "Director loan interest")
        row = self.year("2026")
        self.assertEqual((row["interest_paid"], row["interest_unpaid"]), ("100.00", "250.00"))
        self.assertEqual(self.loan()["interest_paid"], "100.00")

    def test_planning_needs_the_interest_categories(self):
        with self.assertRaisesRegex(ValueError, "Director loan interest"):
            core.plan_loan_interest(self.database, "2026-12-31", "100", None, TODAY)
        self.assertTrue(any("interest" in note for note in self.loan()["notes"]))

    def test_endpoints(self):
        self.interest_categories()
        self.assertEqual(self.client.get("/api/forecast/director-loan").status_code, 200)
        response = self.client.post("/api/forecast/director-loan/interest", json={"date": "2027-06-30", "amount": "120"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/api/forecast/director-loan/interest", json={"date": "x", "amount": "1"}).status_code, 400)
        self.assertEqual(self.client.put("/api/forecast/settings", json={"loan_start": "soon"}).status_code, 400)
        saved = self.client.put("/api/forecast/settings", json={"loan_start": "2026-03-01", "loan_interest_rate": 4}).json()
        self.assertEqual((saved["loan_start"], saved["loan_interest_rate"]), ("2026-03-01", 4))
