from datetime import date
from decimal import Decimal

from backend.pfm import core
from backend.pfm.core.database import parse_money
from tests.test_forecast import TODAY
from tests.test_money_plan import MoneyPlanFixture


class ReserveFixture(MoneyPlanFixture):
    def setUp(self):
        super().setUp()
        self.cat["business", "Liquidation reserve tax"] = core.add_category(self.database, "business", "Liquidation reserve tax")["id"]
        self.cat["business", "Liquidation reserve withholding"] = core.add_category(
            self.database, "business", "Liquidation reserve withholding")["id"]

    def reserve_lines(self):
        return self.all_lines("business", "reserve")

    def after_tax(self, year):
        row = next(y for y in core.corporate_tax(self.database, TODAY)["years"] if y["year"] == year)
        return parse_money(row["profit"]) - parse_money(row["tax"])


class FormingTests(ReserveFixture):
    def test_the_tax_line_comes_from_the_profit_after_tax_and_the_share(self):
        self.settings(reserve_share=50)
        after_tax = self.after_tax("2027")
        self.assertGreater(after_tax, 0)
        reserve = round(Decimal(after_tax) * Decimal("0.5") / Decimal("1.1"))
        tax_lines = [(m, line) for m, line in self.reserve_lines() if line["category"] == "Liquidation reserve tax"]
        december = [line for m, line in tax_lines if m == "2027-12"][0]
        self.assertEqual(december["date"], "2027-12-20")
        self.assertEqual(parse_money(december["amount"]), -round(Decimal(reserve) / 10))
        # The profit is unchanged by the new categories.
        self.assertEqual(self.after_tax("2027"), after_tax)

    def test_nothing_is_formed_at_share_zero(self):
        self.assertEqual(self.reserve_lines(), [])
        self.assertEqual(self.plan()[2027]["reserve"], "0.00")

    def test_nothing_is_formed_in_a_loss_year(self):
        self.settings(reserve_share=50)
        core.add_planned(self.database, "business", "2027-06-10", "-900000", self.cat["business", "Tools"], "Big cost")
        self.assertLess(self.after_tax("2027"), 0)
        self.assertFalse([m for m, line in self.reserve_lines() if m.startswith("2027") and line["category"] == "Liquidation reserve tax"])

    def test_without_the_role_categories_nothing_is_added_and_the_assumptions_say_so(self):
        self.settings(reserve_share=50, roles={"reserve_tax": None, "reserve_withholding": None})
        self.assertEqual(self.reserve_lines(), [])
        self.assertTrue(any("Liquidation reserve tax" in a for a in self.forecast()["business"]["assumptions"]))

    def test_the_tax_and_withholding_categories_are_left_out_of_the_profit(self):
        before = core.corporate_tax(self.database, TODAY)["years"][1]["profit"]
        self.settings(reserve_share=50)
        after = core.corporate_tax(self.database, TODAY)["years"][1]
        self.assertEqual(after["profit"], before)
        self.assertNotIn("Liquidation reserve tax", [b["category"] for b in after["breakdown"]])


class PayoutTests(ReserveFixture):
    def test_a_reserve_pays_out_on_its_payable_date_with_the_withholding(self):
        self.settings(liquidation_reserves=[{"label": "Old", "amount": "10.000,00", "payable": "2027-03-15", "rate": 5}])
        found = [(m, line) for m, line in self.reserve_lines()]
        self.assertEqual([(m, line["date"], line["amount"], line["category"]) for m, line in found],
                         [("2027-03", "2027-03-15", "-500.00", "Liquidation reserve withholding")])

    def test_an_existing_reserve_past_its_date_pays_out_in_the_current_month(self):
        self.settings(liquidation_reserves=[{"label": "Old", "amount": "10.000,00", "payable": "2026-01-01", "rate": 5}])
        self.assertEqual([m for m, _ in self.reserve_lines()], ["2026-10"])

    def test_a_reserve_payable_after_the_forecast_stays_in_the_reserve(self):
        self.settings(liquidation_reserves=[{"label": "Late", "amount": "10.000,00", "payable": "2030-01-01", "rate": 5}])
        self.assertEqual(self.reserve_lines(), [])
        self.assertEqual(self.plan()[2027]["reserve"], "10000.00")

    def test_a_new_reserve_is_payable_after_the_wait(self):
        self.end_year(2031)
        self.settings(reserve_share=50, reserve_wait_years=3)
        payouts = [line["date"] for m, line in self.reserve_lines() if line["category"] == "Liquidation reserve withholding"]
        # Formed on the accounts of 2026 to 2031: 2026 pays from 1 January 2030, 2027 from 2031.
        self.assertEqual(payouts, ["2030-01-01", "2031-01-01"])


class LoanTests(ReserveFixture):
    def test_the_net_payout_lowers_the_loan_below_zero_without_interest(self):
        self.settings(liquidation_reserves=[{"label": "Old", "amount": "10.000,00", "payable": "2027-03-15", "rate": 5}])
        loan = core.director_loan(self.database, TODAY)
        payout = [m for m in loan["movements"] if m["reserve"]]
        self.assertEqual([(m["date"], m["amount"]) for m in payout], [("2027-03-15", "-9500.00")])
        row = next(y for y in loan["years"] if y["year"] == "2027")
        self.assertEqual(row["repaid"], "9500.00")
        self.assertLess(parse_money(row["closing"]), 0)
        self.assertEqual(row["interest"], "0.00")

    def test_the_payout_lowers_the_running_balance_by_the_net(self):
        self.settings(liquidation_reserves=[{"label": "Old", "amount": "10.000,00", "payable": "2026-11-15", "rate": 5}])
        loan = core.director_loan(self.database, TODAY)
        self.assertEqual(next(y for y in loan["years"] if y["year"] == "2026")["repaid"], "9500.00")
        running = [parse_money(m["balance"]) for m in loan["movements"]]
        at = next(i for i, m in enumerate(loan["movements"]) if m["reserve"])
        before = running[at - 1] if at else 0
        self.assertEqual(running[at], before - 950000)


class MoneyPlanTests(ReserveFixture):
    def test_the_reserve_and_loan_are_in_the_plan_and_the_room_is_split(self):
        self.settings(
            buffer_months=[{"year": 2026, "months": 1}], dividend_withholding_rate=25,
            liquidation_reserves=[{"label": "A", "amount": "1.000,00", "payable": "2030-01-01", "rate": 5},
                                  {"label": "B", "amount": "3.000,00", "payable": "2030-01-01", "rate": 10}],
        )
        row = self.plan()[2026]
        self.assertEqual(row["reserve"], "4000.00")
        room = parse_money(row["room"])
        self.assertGreater(room, 4000_00)
        # Weighted rate (1000 x 5% + 3000 x 10%) / 4000 = 8.75%.
        expected = round(Decimal(400000) * Decimal("0.9125") + Decimal(room - 400000) * Decimal("0.75"))
        self.assertEqual(parse_money(row["room_net"]), expected)
        self.assertEqual(row["loan"], core.director_loan(self.database, TODAY)["years"][0]["closing"])

    def test_a_negative_room_has_no_reserve_part(self):
        self.settings(buffer_months=[{"year": 2026, "months": 120}],
                      liquidation_reserves=[{"label": "A", "amount": "1.000,00", "payable": "2030-01-01", "rate": 5}])
        row = self.plan()[2026]
        room = parse_money(row["room"])
        self.assertLess(room, 0)
        self.assertEqual(parse_money(row["room_net"]), round(Decimal(room) * Decimal("0.75")))

    def test_a_paid_out_reserve_leaves_the_reserve_figure(self):
        self.settings(liquidation_reserves=[{"label": "A", "amount": "1.000,00", "payable": "2027-03-15", "rate": 5}])
        plan = self.plan()
        self.assertEqual((plan[2026]["reserve"], plan[2027]["reserve"]), ("1000.00", "0.00"))


class SettingsTests(ReserveFixture):
    def test_validation_and_formatting(self):
        saved = self.settings(liquidation_reserves=[{"label": " X ", "amount": "1.234,50", "payable": "2028-01-01", "rate": 6.5}])
        self.assertEqual(saved["liquidation_reserves"], [{"label": "X", "amount": "1234.50", "payable": "2028-01-01", "rate": 6.5}])
        for bad in (
            {"liquidation_reserves": [{"label": "", "amount": "-1", "payable": "2028-01-01", "rate": 5}]},
            {"liquidation_reserves": [{"label": "", "amount": "1", "payable": "soon", "rate": 5}]},
            {"liquidation_reserves": [{"label": "", "amount": "1", "payable": "2028-01-01", "rate": 101}]},
            {"liquidation_reserves": "x"}, {"reserve_share": 101}, {"reserve_wait_years": 11}, {"reserve_payout_rate": -1},
        ):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.settings(**bad)
        self.assertEqual(self.client.put("/api/forecast/settings", json={"reserve_share": 20, "reserve_wait_years": 5}).json()["reserve_wait_years"], 5)
