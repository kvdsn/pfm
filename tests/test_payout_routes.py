import unittest

from backend.pfm import core
from backend.pfm.core.forecast_settings import load_settings
from backend.pfm.core.payout_routes import compute_routes
from tests.test_forecast import ForecastFixture

MADE_UP = {
    "social_contribution_rate": 20, "personal_tax_rate": 50, "municipal_tax_rate": 0, "professional_costs_rate": 0,
    "dividend_withholding_rate": 25, "reserve_tax_rate": 10, "reserve_early_rate": 20, "reserve_payout_rate": 6.5,
    "reserve_wait_years": 3, "loan_interest_rate": 4, "credit_interest_rate": 6, "interest_withholding_rate": 30, "tax_reduced_rate": 20, "tax_standard_rate": 25,
    "tax_reduced_threshold": 5100000, "tax_reduced_band": 10000000, "liquidation_reserves": [],
}


def routes(profit=5000000, eligible=False, **changes):
    result = compute_routes({**MADE_UP, **changes}, profit, eligible)
    return result, {row["key"]: row for row in result["routes"]}


class FormulaTests(unittest.TestCase):
    def test_each_route(self):
        _, by_key = routes()
        self.assertEqual(by_key["salary"]["profit_per_1000"], "2500.00")
        self.assertEqual(by_key["salary"]["tax_share"], 60.0)
        self.assertEqual(by_key["dividend"]["profit_per_1000"], "1777.78")
        self.assertEqual(by_key["reserve_early"]["profit_per_1000"], "1833.33")
        self.assertEqual(by_key["reserve"]["profit_per_1000"], "1568.63")
        self.assertEqual(by_key["winding_up"]["profit_per_1000"], "1466.67")
        self.assertEqual(by_key["dividend"]["tax_share"], 43.8)

    def test_current_account_interest_uses_only_the_withholding(self):
        result, by_key = routes()
        self.assertEqual(by_key["current_account_interest"]["profit_per_1000"], "1428.57")
        self.assertEqual(by_key["current_account_interest"]["tax_share"], 30.0)
        self.assertEqual(routes(eligible=True)[1]["current_account_interest"]["profit_per_1000"], "1428.57")
        self.assertEqual(result["credit_interest_rate"], 6)

    def test_salary_with_municipal_tax_and_professional_costs(self):
        _, by_key = routes(municipal_tax_rate=10, professional_costs_rate=10)
        k = 1 - 0.2 - 0.8 * 0.9 * 0.5 * 1.1
        self.assertEqual(by_key["salary"]["profit_per_1000"], f"{1000 / k:.2f}")

    def test_loan_adds_its_interest_cost_while_waiting(self):
        _, by_key = routes()
        extra = 1000 * 0.04 * 0.25 * 3
        self.assertEqual(by_key["loan_then_reserve"]["profit_per_1000"], f"{1000 / 0.6375 + extra:.2f}")

    def test_ordering(self):
        result, _ = routes(liquidation_reserves=[{"label": "A", "amount": 100000, "payable": "2027-01-01", "rate": 5}])
        self.assertEqual([row["key"] for row in result["routes"]], [
            "salary", "dividend", "current_account_interest", "reserve_early", "reserve", "existing_reserves", "loan_then_reserve", "winding_up",
        ])

    def test_corporate_rate_choice(self):
        self.assertEqual(routes(eligible=True)[0]["corporate_rate"], 20.0)
        self.assertEqual(routes(eligible=False)[0]["corporate_rate"], 25.0)
        self.assertEqual(routes(profit=20000000, eligible=True)[0]["corporate_rate"], 25.0)
        _, by_key = routes(eligible=True)
        self.assertEqual(by_key["dividend"]["profit_per_1000"], f"{1000 / (0.8 * 0.75):.2f}")

    def test_existing_reserves_are_weighted_by_amount(self):
        reserves = [
            {"label": "A", "amount": 100000, "payable": "2028-01-01", "rate": 5},
            {"label": "B", "amount": 300000, "payable": "2027-01-01", "rate": 9},
        ]
        result, by_key = routes(liquidation_reserves=reserves)
        self.assertEqual(result["existing_reserves"], {"gross": "4000.00", "earliest": "2027-01-01", "latest": "2028-01-01"})
        self.assertEqual(by_key["existing_reserves"]["profit_per_1000"], f"{1000 / (0.75 / 1.1 * (1 - 0.08)):.2f}")

    def test_no_existing_reserves_row_without_reserves(self):
        result, by_key = routes()
        self.assertNotIn("existing_reserves", by_key)
        self.assertIsNone(result["existing_reserves"])

    def test_threshold_saving(self):
        result, _ = routes(profit=5000000)
        self.assertEqual(result["threshold"], {"salary": "51000.00", "saving": "2500.00"})
        self.assertEqual(routes(profit=20000000)[0]["threshold"]["saving"], "5000.00")
        self.assertEqual(routes(profit=-100)[0]["threshold"]["saving"], "0.00")
        self.assertIsNone(routes(tax_reduced_threshold=0)[0]["threshold"])


class EndpointTests(ForecastFixture):
    def test_endpoint_and_settings(self):
        body = self.client.get("/api/forecast/payout-routes").json()
        self.assertEqual(body["routes"][0]["key"], "salary")
        self.assertEqual(body["routes"][-1]["key"], "winding_up")
        self.assertIn("corporate_rate", body)
        self.assertIsNone(body["existing_reserves"])
        self.client.put("/api/forecast/settings", json={"social_contribution_rate": 30})
        with core.connect(self.database) as db:
            self.assertEqual(load_settings(db)["social_contribution_rate"], 30)
        self.assertEqual(self.client.put("/api/forecast/settings", json={"reserve_early_rate": 120}).status_code, 400)
        self.client.put("/api/forecast/settings", json={"credit_interest_rate": 5.5, "interest_withholding_rate": 25})
        with core.connect(self.database) as db:
            saved = load_settings(db)
        self.assertEqual((saved["credit_interest_rate"], saved["interest_withholding_rate"]), (5.5, 25))
        self.assertEqual(self.client.put("/api/forecast/settings", json={"credit_interest_rate": 25}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
