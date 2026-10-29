import itertools
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.pfm import core
from backend.pfm.core.forecast_settings import (
    DEFAULT_CLOSED,
    day_rate_on,
    days_worked,
    easter,
    last_working_day,
    load_days_off,
    public_holidays,
    working_days,
)
from backend.pfm.server import create_app
from tests.test_core import make_csv, transaction

PERSONAL = "BE00000000000000000001"
BUSINESS = "BE00000000000000000002"
KIDS = "BE00000000000000000003"
TODAY = date(2026, 10, 25)


def month_start(year, month, back=0):
    index = year * 12 + month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


class CalendarTests(unittest.TestCase):
    def test_easter_and_belgian_holidays(self):
        self.assertEqual(easter(2026), date(2026, 4, 5))
        self.assertEqual(easter(2024), date(2024, 3, 31))
        holidays = public_holidays(2026)
        self.assertIn(date(2026, 4, 6), holidays)  # Easter Monday
        self.assertIn(date(2026, 5, 14), holidays)  # Ascension
        self.assertIn(date(2026, 5, 25), holidays)  # Whit Monday
        self.assertEqual(len(holidays), 10)

    def test_working_days_skip_weekends_and_holidays(self):
        self.assertEqual(len(working_days(2026, 10)), 22)
        self.assertEqual(len(working_days(2026, 5)), 18)
        self.assertEqual(last_working_day(2026, 10), date(2026, 10, 30))
        self.assertEqual(last_working_day(2026, 12), date(2026, 12, 31))

    def test_days_off_count_their_fraction_on_working_days_only(self):
        self.assertEqual(days_worked(2026, 10, []), 22)
        whole = [(date(2026, 10, 5), date(2026, 10, 6), 1.0)]
        self.assertEqual(days_worked(2026, 10, whole), 20)
        half = [(date(2026, 10, 12), date(2026, 10, 13), 0.5)]
        self.assertEqual(days_worked(2026, 10, half), 21)
        weekend = [(date(2026, 10, 3), date(2026, 10, 4), 1.0)]
        self.assertEqual(days_worked(2026, 10, weekend), 22)

    def test_day_rate_in_force(self):
        rates = [(date(2026, 1, 1), 80000), (date(2026, 9, 1), 85000)]
        self.assertEqual(day_rate_on(rates, date(2026, 8, 31)), 80000)
        self.assertEqual(day_rate_on(rates, date(2026, 9, 1)), 85000)
        self.assertEqual(day_rate_on(rates, date(2025, 1, 1)), 80000)
        self.assertIsNone(day_rate_on([], date(2026, 1, 1)))


class ForecastFixture(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        os.environ.pop("PFM_HOME", None)
        # The defaults name no closed category; give one so name resolution is tested.
        closed = patch.dict(DEFAULT_CLOSED, {"personal": ["Old loan"]})
        closed.start()
        self.addCleanup(closed.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "finance.sqlite3"
        core.initialize(self.database)
        self.client = TestClient(create_app(self.database, self.root / "dist"))
        self.addCleanup(self.client.close)
        self.staging = {}
        self.balance = itertools.count(1)
        self.cat = {}
        for entity, names in {
            "personal": ["Salary", "Old loan", "Groceries", "Streaming", "Entity transfer", "Rent"],
            "business": ["Consulting income", "VAT", "Corporate tax", "Director remuneration", "Entity transfer", "Tools"],
            "kids": ["Pocket money"],
        }.items():
            for name in names:
                self.cat[entity, name] = core.add_category(self.database, entity, name)["id"]

    def add(self, account, account_type, merchant, payments, category=None):
        """Import (date, amount[, balance]) payments of one merchant, optionally into a category."""
        rows = [
            transaction(
                account, when.strftime("%d/%m/%Y"), f"{amount:.2f}".replace(".", ","),
                f"{(rest[0] if rest else next(self.balance)):.2f}".replace(".", ","), merchant,
            )
            for when, amount, *rest in payments
        ]
        preview = core.stage_import(self.database, "export.csv", make_csv(rows).encode(), self.staging)
        core.commit_import(self.database, preview["token"], account_type, self.staging)
        if category:
            with core.connect(self.database) as db:
                db.execute(
                    "UPDATE transactions SET category_id = ? WHERE merchant = ?",
                    (self.cat[account_type, category], merchant),
                )

    def run_forecast(self, entity):
        return core.forecast(self.database, TODAY)[entity]

    def lines(self, entity, month, source=None):
        result = self.run_forecast(entity)
        found = next(m for m in result["months"] if m["month"] == month)["lines"]
        return [line for line in found if source is None or line["source"] == source]

    def month(self, entity, month):
        return next(m for m in self.run_forecast(entity)["months"] if m["month"] == month)

    def settings(self, **changes):
        return core.set_settings(self.database, changes)


class RecurringAndPlannedTests(ForecastFixture):
    def test_months_run_from_the_current_month_to_december_next_year(self):
        months = [m["month"] for m in self.run_forecast("personal")["months"]]
        self.assertEqual(months[0], "2026-10")
        self.assertEqual(months[-1], "2027-12")
        self.assertEqual(len(months), 15)
        self.assertTrue(self.month("personal", "2026-10")["partial"])
        self.assertEqual([y["year"] for y in self.run_forecast("personal")["year_ends"]], ["2026", "2027"])

    def test_recurring_steps_by_cadence_and_stops_at_the_end_date(self):
        core.add_recurring(self.database, "personal", "Rent", "monthly", -500, "2026-08-05",
                           category_id=self.cat["personal", "Rent"], end_date="2027-02-10", today=TODAY)
        dates = [l["date"] for m in self.run_forecast("personal")["months"] for l in m["lines"] if l["source"] == "recurring"]
        self.assertEqual(dates, ["2026-11-05", "2026-12-05", "2027-01-05", "2027-02-05"])
        line = self.lines("personal", "2026-11")[0]
        self.assertEqual((line["amount"], line["category"], line["detail"]), ("-500.00", "Rent", "Rent"))

    def test_quarterly_item_and_current_month_only_counts_future_occurrences(self):
        core.add_recurring(self.database, "personal", "Insurance", "quarterly", -90, "2026-07-28", today=TODAY)
        core.add_recurring(self.database, "personal", "Gym", "monthly", -30, "2026-08-10", today=TODAY)
        dates = {l["detail"]: [] for l in []}
        for m in self.run_forecast("personal")["months"]:
            for l in m["lines"]:
                dates.setdefault(l["detail"], []).append(l["date"])
        self.assertEqual(dates["Insurance"][:3], ["2026-10-28", "2027-01-28", "2027-04-28"])
        self.assertEqual(dates["Gym"][0], "2026-11-10")  # the 10th of this month is past

    def test_a_payment_due_this_month_but_not_yet_imported_is_still_expected(self):
        # Booked until 18 September; the 18 October one falls after the latest import, before today (the 25th).
        self.add(PERSONAL, "personal", "Loan", [(date(2026, 8, 18), -450.0), (date(2026, 9, 18), -450.0)])
        core.add_recurring(self.database, "personal", "Loan", "monthly", -450, "2026-08-18", day_of_month=18, today=TODAY)
        october = next(m for m in self.run_forecast("personal")["months"] if m["month"] == "2026-10")
        self.assertEqual([(l["date"], l["amount"]) for l in october["lines"] if l["detail"] == "Loan"], [("2026-10-18", "-450.00")])

    def test_schedule_change_applies_from_its_effective_date(self):
        item = core.add_recurring(self.database, "personal", "Rent", "monthly", -500, "2026-08-05", today=TODAY)
        with core.connect(self.database) as db:
            db.execute("INSERT INTO recurring_amounts(item_id, effective_date, amount_cents) VALUES (?, '2027-01-01', -55000)", (item["id"],))
        amounts = {l["date"]: l["amount"] for m in self.run_forecast("personal")["months"] for l in m["lines"]}
        self.assertEqual((amounts["2026-12-05"], amounts["2027-01-05"]), ("-500.00", "-550.00"))

    def test_planned_items_from_today_on(self):
        category = self.cat["business", "Corporate tax"]
        core.add_planned(self.database, "business", "2027-03-31", "-5.000,00", category, "Tax")
        core.add_planned(self.database, "business", "2026-10-25", -100, None, "Today")
        core.add_planned(self.database, "business", "2026-10-01", -999, None, "Past")
        core.add_planned(self.database, "personal", "2027-03-31", -1, None, "Other entity")
        planned = [l for m in self.run_forecast("business")["months"] for l in m["lines"] if l["source"] == "planned"]
        self.assertEqual([(l["date"], l["amount"], l["detail"]) for l in planned],
                         [("2026-10-25", "-100.00", "Today"), ("2027-03-31", "-5000.00", "Tax")])
        self.assertEqual(planned[0]["category"], "Uncategorized")
        self.assertEqual(planned[1]["category"], "Corporate tax")


class ConsultingTests(ForecastFixture):
    def setUp(self):
        super().setUp()
        core.set_day_rate(self.database, "2026-01-01", 800)
        # These tests were written for a two-month delay; the default is one (see test_defaults_are_resolved_at_read_time).
        core.set_settings(self.database, {"payment_delay_months": 2})

    def pay(self, when, amount=12100.0):
        self.add(BUSINESS, "business", f"Client {when}", [(when, amount)], "Consulting income")

    def consulting(self):
        return [(m["month"], l) for m in self.run_forecast("business")["months"] for l in m["lines"] if l["source"] == "consulting"]

    def test_projects_months_after_the_last_paid_one(self):
        self.pay(date(2026, 9, 30))  # pays July
        found = self.consulting()
        month, first = found[0]
        self.assertEqual((month, first["date"]), ("2026-10", "2026-10-30"))  # August, paid on October's last working day
        self.assertEqual(first["detail"], "Aug 2026: 21 days × €800")
        self.assertEqual(first["amount"], "20328.00")  # 21 × 800 × 1.21
        self.assertEqual(found[1][1]["date"], "2026-11-30")
        self.assertEqual(found[-1][1]["date"], "2027-12-31")  # the last work month whose payment is in the horizon

    def test_early_month_payment_counts_for_the_previous_month(self):
        self.pay(date(2026, 10, 2))
        self.assertEqual(self.consulting()[0][1]["detail"][:8], "Aug 2026")
        self.pay(date(2026, 10, 8))
        self.assertEqual(self.consulting()[0][1]["detail"][:8], "Sep 2026")

    def test_a_payment_already_due_falls_on_today(self):
        self.pay(date(2026, 8, 31))  # pays June; July's payment was due 30 September
        month, first = self.consulting()[0]
        self.assertEqual((month, first["date"], first["detail"][:8]), ("2026-10", "2026-10-25", "Jul 2026"))

    def test_days_off_and_rate_changes_shape_the_invoice(self):
        self.pay(date(2026, 9, 30))
        core.add_day_off(self.database, "2026-08-03", "2026-08-04", 1, "Trip")
        core.set_day_rate(self.database, "2026-09-01", 900)
        found = {l["detail"][:8]: l for _, l in self.consulting()}
        self.assertEqual(found["Aug 2026"]["detail"], "Aug 2026: 19 days × €800")
        self.assertEqual(found["Sep 2026"]["detail"], "Sep 2026: 22 days × €900")
        self.assertEqual(found["Sep 2026"]["amount"], "23958.00")

    def test_without_a_day_rate_nothing_is_projected(self):
        core.delete_day_rate(self.database, "2026-01-01")
        self.assertEqual(self.consulting(), [])
        self.assertIn("No day rate is set, so no consulting income is projected.", self.run_forecast("business")["assumptions"])

    def test_vat_follows_each_projected_payment(self):
        self.pay(date(2026, 9, 30))
        self.add(BUSINESS, "business", "Tax office", [(date(2026, 10, 13), -2100.0)], "VAT")
        vat = [l for m in self.run_forecast("business")["months"] for l in m["lines"] if l["source"] == "vat"]
        self.assertEqual(vat[0]["date"], "2026-11-15")
        self.assertEqual(vat[0]["amount"], "-3528.00")  # 20328 × 21 / 121
        self.assertEqual(vat[0]["detail"], "VAT on the 2026-10-30 payment")
        self.assertEqual(vat[0]["category"], "VAT")

    def test_vat_of_the_latest_actual_payment_when_not_yet_paid(self):
        self.pay(date(2026, 9, 30), 12100.0)
        vat = self.lines("business", "2026-10", "vat")
        self.assertEqual([(l["date"], l["amount"], l["detail"]) for l in vat],
                         [("2026-10-15", "-2100.00", "VAT on the 2026-09-30 payment")])

    def test_consulting_check_infers_days_from_the_payment(self):
        self.pay(date(2026, 9, 30), 12100.0 * 1.6)  # 16 days... of July at 800: 19360 / 1.21 / 800 = 20
        self.pay(date(2026, 8, 31), 12100.0 * 5)  # 62.5 days: more than June has
        checks = core.backtest(self.database, TODAY)["business"]["consulting_check"]
        self.assertEqual([c["month_of_work"] for c in checks], ["2026-06", "2026-07"])
        self.assertEqual((checks[1]["days_inferred"], checks[1]["working_days"], checks[1]["plausible"]), (20.0, 22, True))
        self.assertFalse(checks[0]["plausible"])


class ConsultingPaymentCheckTests(ForecastFixture):
    """August 2026 has 21 working days: 21 x 800 x 1.21 = 20328.00, due 30 September, VAT of 3528.00 due 15 October."""

    def setUp(self):
        super().setUp()
        core.set_day_rate(self.database, "2026-01-01", 800)

    def pay(self, when, amount=20328.0):
        self.add(BUSINESS, "business", f"Client {when}", [(when, amount)], "Consulting income")

    def vat(self, when, amount):
        self.add(BUSINESS, "business", f"Tax office {when}", [(when, -amount)], "VAT")

    def check(self, today=TODAY):
        return core.consulting_payments(self.database, today)

    def month(self, key, today=TODAY):
        return next(m for m in self.check(today)["months"] if m["month"] == key)

    def test_months_run_from_twelve_months_back_to_the_month_before_the_current(self):
        keys = [m["month"] for m in self.check()["months"]]
        self.assertEqual((keys[0], keys[-1], len(keys)), ("2025-10", "2026-09", 12))

    def test_a_payment_of_the_expected_amount_is_paid_and_its_vat_too(self):
        self.pay(date(2026, 9, 30))
        self.vat(date(2026, 10, 15), 3528.0)
        august = self.month("2026-08")
        self.assertEqual((august["working_days"], august["days_off"], august["days_expected"]), (21, 0, 21))
        self.assertEqual((august["expected"], august["due"], august["status"]), ("20328.00", "2026-09-30", "paid"))
        self.assertEqual(august["days_paid"], 21.0)
        self.assertEqual([(r["date"], r["amount"]) for r in august["received"]], [("2026-09-30", "20328.00")])
        self.assertEqual((august["vat"]["expected"], august["vat"]["due"], august["vat"]["status"]),
                         ("3528.00", "2026-10-15", "paid"))
        self.assertEqual([(p["date"], p["amount"]) for p in august["vat"]["paid"]], [("2026-10-15", "3528.00")])

    def test_days_off_lower_the_expected_days(self):
        core.add_day_off(self.database, "2026-08-03", "2026-08-04", 1.0, "Holiday")
        august = self.month("2026-08")
        self.assertEqual((august["days_off"], august["days_expected"], august["expected"]), (2, 19, "18392.00"))

    def test_repeating_half_days_off_lower_the_expected_days(self):
        # Every other Wednesday afternoon from 5 August: 5 and 19 August, then 2 September.
        core.add_day_off(self.database, "2026-08-05", "2026-08-05", 0.5, "Wednesday", 2)
        self.assertEqual(self.month("2026-08")["days_expected"], 20)
        # Overlapping a whole day off, a half day off counts once: the day is simply off.
        core.add_day_off(self.database, "2026-08-19", "2026-08-19", 1.0, "Holiday")
        self.assertEqual(self.month("2026-08")["days_expected"], 19.5)

    def test_fewer_whole_days_than_expected_are_likely_days_off(self):
        self.pay(date(2026, 9, 30), 18392.0)  # 19 days at 800 with VAT
        august = self.month("2026-08")
        self.assertEqual((august["status"], august["days_paid"]), ("fewer_days", 19.0))
        self.assertEqual((self.check()["summary"]["fewer_days"], self.check()["summary"]["differs"]), (1, 0))

    def test_an_amount_that_does_not_fit_the_rate_or_exceeds_it_differs(self):
        self.pay(date(2026, 9, 30), 18000.0)  # 18.6 days: no whole or half number of days
        self.assertEqual(self.month("2026-08")["status"], "differs")
        self.pay(date(2026, 8, 31), 23232.0)  # 24 days for July, which has 22 working days
        self.assertEqual(self.month("2026-07")["status"], "differs")
        self.assertEqual(self.check()["summary"]["differs"], 2)

    def test_a_vat_payment_of_exactly_a_months_vat_goes_to_that_month(self):
        # June's payment on 31 July and July's on 31 August: June's VAT window (due 15 August, +45 days) also covers
        # 15 September, but the 15 September payment is exactly July's VAT, so it is July's.
        self.pay(date(2026, 7, 31), 16940.0)  # 14 days: VAT 2940
        self.pay(date(2026, 8, 31))  # 21 days: VAT 3528
        self.vat(date(2026, 9, 15), 3528.0)
        self.assertEqual(self.month("2026-07")["vat"]["status"], "paid")
        self.assertEqual(self.month("2026-06")["vat"]["status"], "missing")

    def test_nothing_received_is_late_after_a_week_and_due_before(self):
        result = self.check()
        self.assertEqual(self.month("2026-08")["status"], "late")
        self.assertEqual(self.month("2026-09")["status"], "due")  # due 30 October
        self.assertIsNone(self.month("2026-08")["vat"])
        self.assertEqual(result["summary"]["late"], 11)  # every month from October 2025 to August 2026
        self.assertEqual(self.month("2026-08", date(2026, 10, 8))["status"], "late")
        self.assertEqual(self.month("2026-08", date(2026, 10, 7))["status"], "due")  # a week of grace

    def test_two_payments_in_one_month_of_work_are_both_listed(self):
        self.pay(date(2026, 9, 30), 10000.0)
        self.pay(date(2026, 9, 29), 10328.0)
        august = self.month("2026-08")
        self.assertEqual([r["date"] for r in august["received"]], ["2026-09-29", "2026-09-30"])
        self.assertEqual(august["status"], "paid")

    def test_an_early_month_payment_belongs_to_the_month_before(self):
        self.pay(date(2026, 10, 2))
        self.assertEqual(self.month("2026-08")["status"], "paid")
        self.assertEqual(self.month("2026-09")["received"], [])

    def test_vat_differs_when_a_different_amount_was_paid(self):
        self.pay(date(2026, 9, 30))
        self.vat(date(2026, 10, 15), 3000.0)
        vat = self.month("2026-08")["vat"]
        self.assertEqual((vat["status"], vat["paid"][0]["amount"]), ("differs", "3000.00"))

    def test_vat_is_missing_after_its_due_date_and_due_before(self):
        self.pay(date(2026, 9, 30))
        self.assertEqual(self.month("2026-08")["vat"]["status"], "missing")
        self.assertEqual(self.check()["summary"]["vat_missing"], 1)
        early = date(2026, 10, 10)
        self.assertEqual(self.month("2026-08", early)["vat"]["status"], "due")
        self.assertEqual(self.check(early)["summary"]["vat_missing"], 0)

    def test_one_vat_payment_serves_one_month_only(self):
        self.pay(date(2026, 8, 31))  # July: VAT due 15 September
        self.pay(date(2026, 9, 30))  # August: VAT due 15 October
        self.vat(date(2026, 10, 15), 3528.0)
        self.assertEqual(self.month("2026-07")["vat"]["status"], "paid")
        self.assertEqual(self.month("2026-08")["vat"]["status"], "missing")

    def test_a_vat_payment_before_the_consulting_payment_does_not_count(self):
        self.pay(date(2026, 9, 30))
        self.vat(date(2026, 9, 15), 3528.0)
        self.assertEqual(self.month("2026-08")["vat"]["status"], "missing")

    def test_no_day_rate_or_no_role_gives_an_empty_check(self):
        empty = {"months": [], "summary": {"late": 0, "differs": 0, "fewer_days": 0, "vat_missing": 0}}
        core.delete_day_rate(self.database, "2026-01-01")
        self.assertEqual(self.check(), empty)
        core.set_day_rate(self.database, "2026-01-01", 800)
        core.set_settings(self.database, {"roles": {"consulting_income": None}})
        self.assertEqual(self.check(), empty)

    def test_endpoint(self):
        self.pay(date(2026, 9, 30))
        body = self.client.get("/api/forecast/consulting").json()
        self.assertEqual(len(body["months"]), 12)
        self.assertEqual(set(body["summary"]), {"late", "differs", "fewer_days", "vat_missing"})


class SalaryTests(ForecastFixture):
    def test_salary_is_an_expense_in_business_and_income_in_personal(self):
        core.set_salary(self.database, "2026", "5.000", 12)
        core.set_salary(self.database, "2027", 5500, 6)
        business = self.lines("business", "2026-10", "salary")
        personal = self.lines("personal", "2026-10", "salary")
        self.assertEqual((business[0]["amount"], business[0]["date"], business[0]["category"]),
                         ("-5000.00", "2026-10-31", "Director remuneration"))
        self.assertEqual((personal[0]["amount"], personal[0]["category"]), ("5000.00", "Salary"))
        self.assertEqual(self.lines("business", "2027-06", "salary")[0]["amount"], "-5500.00")
        self.assertEqual(self.lines("business", "2027-07", "salary"), [])

    def test_a_year_without_a_schedule_repeats_the_latest_earlier_one(self):
        core.set_salary(self.database, "2026", 5000, 12)
        result = self.run_forecast("business")
        self.assertIn("Salary 2027 repeats the 2026 schedule.", result["assumptions"])
        self.assertEqual(len([l for m in result["months"] if m["month"].startswith("2027") for l in m["lines"] if l["source"] == "salary"]), 12)

    def test_no_schedule_no_salary(self):
        self.assertEqual(self.lines("business", "2026-10", "salary"), [])

    def test_current_month_is_skipped_when_already_booked(self):
        core.set_salary(self.database, "2026", 5000, 12)
        self.add(BUSINESS, "business", "Me", [(date(2026, 10, 20), -5000.0)], "Director remuneration")
        self.assertEqual(self.lines("business", "2026-10", "salary"), [])
        self.assertEqual(len(self.lines("business", "2026-11", "salary")), 1)


    def test_the_minimum_is_paid_after_the_last_month_in_full(self):
        core.set_salary(self.database, "2027", 10000, 6, "250,00")
        core.set_settings(self.database, {"salary_net_percent": 70})
        self.assertEqual(self.lines("personal", "2027-06", "salary")[0]["amount"], "7000.00")
        business, personal = self.lines("business", "2027-07", "salary"), self.lines("personal", "2027-07", "salary")
        self.assertEqual((business[0]["amount"], business[0]["detail"]), ("-250.00", "Monthly minimum salary"))
        self.assertEqual(personal[0]["amount"], "250.00")  # what reaches Personal: no net share
        # 2026 has no schedule of its own and 2027's is not earlier, so 2026 pays nothing.
        self.assertEqual(self.lines("business", "2026-11", "salary"), [])

    def test_personal_receives_the_net_share_seen_in_history(self):
        core.set_salary(self.database, "2026", 10000, 12)
        for month in (7, 8, 9):
            self.add(BUSINESS, "business", "Me", [(date(2026, month, 28), -10000.0)], "Director remuneration")
            self.add(PERSONAL, "personal", "Company", [(date(2026, month, 28), 7000.0)], "Salary")
        result = self.run_forecast("personal")
        self.assertIn("Personal receives 70% of the salary, as over the last 12 months.", result["assumptions"])
        self.assertEqual(self.lines("personal", "2026-11", "salary")[0]["amount"], "7000.00")
        self.assertEqual(self.lines("business", "2026-11", "salary")[0]["amount"], "-10000.00")
        core.set_settings(self.database, {"salary_net_percent": 65})
        self.assertEqual(self.lines("personal", "2026-11", "salary")[0]["amount"], "6500.00")
        self.assertNotIn("Personal receives 70% of the salary, as over the last 12 months.", self.run_forecast("personal")["assumptions"])


class BaselineTests(ForecastFixture):
    GROCERIES = [-300, -300, -300, -400, -300, -350, -450, -400, -380]  # January to September

    def history(self, extra=()):
        payments = [(date(2026, month, 10), amount) for month, amount in enumerate(self.GROCERIES, 1)]
        self.add(PERSONAL, "personal", "Delhaize", payments + list(extra), "Groceries")

    def test_baseline_is_the_median_of_the_complete_months_without_one_offs(self):
        self.history([(date(2026, 6, 20), -1500.0)])
        lines = self.lines("personal", "2026-11", "baseline")
        self.assertEqual([(l["category"], l["amount"], l["date"]) for l in lines], [("Groceries", "-390.00", None)])
        self.assertEqual([o["amount"] for o in self.run_forecast("personal")["one_offs"]], ["-1500.00"])

    def test_current_month_projects_only_what_is_not_booked_yet(self):
        self.history([(date(2026, 10, 3), -100.0)])
        self.assertEqual(self.lines("personal", "2026-10", "baseline")[0]["amount"], "-290.00")

    def test_current_month_remainder_is_floored_at_zero(self):
        self.history([(date(2026, 10, 3), -600.0)])
        self.assertEqual(self.lines("personal", "2026-10", "baseline"), [])

    def test_weighted_baseline_uses_twelve_months(self):
        self.history()
        self.settings(baseline_weighted=True)
        self.assertEqual(self.lines("personal", "2026-11", "baseline")[0]["detail"], "Weighted median of the last 8 months")

    def test_closed_role_and_recurring_categories_get_no_baseline(self):
        self.history()
        payments = [(date(2026, month, 12), -200.0) for month in range(1, 10)]
        self.add(PERSONAL, "personal", "Lawyer", payments, "Old loan")
        self.add(PERSONAL, "personal", "Employer", [(date(2026, m, 28), 3000.0) for m in range(1, 10)], "Salary")
        self.add(PERSONAL, "personal", "Netflix", [(date(2026, m, 5), -15.0) for m in range(1, 10)], "Streaming")
        core.add_recurring(self.database, "personal", "Netflix", "monthly", -15, "2026-01-05", today=TODAY)
        categories = {l["category"] for l in self.lines("personal", "2026-11", "baseline")}
        self.assertEqual(categories, {"Groceries"})

    def test_months_before_the_first_transaction_are_not_zero_months(self):
        self.add(PERSONAL, "personal", "Delhaize", [(date(2026, m, 10), -100.0) for m in (7, 8, 9)], "Groceries")
        self.add(PERSONAL, "personal", "Other", [(date(2026, 6, 1), -1.0)])
        self.assertEqual(self.lines("personal", "2026-11", "baseline")[0]["amount"], "-100.00")


class BalanceAndTransferTests(ForecastFixture):
    def test_opening_and_current_month_closing_respect_the_as_of_date(self):
        self.add(BUSINESS, "business", "Start", [(date(2026, 10, 25), 100.0, 1000.0)])
        core.add_planned(self.database, "business", "2026-10-25", -100, None, "In the balance already")
        core.add_planned(self.database, "business", "2026-10-26", -50, None, "Not yet")
        core.add_planned(self.database, "business", "2026-11-03", -20, None, "Next month")
        result = self.run_forecast("business")
        self.assertEqual(result["opening"], "1000.00")
        self.assertEqual(result["accounts"], [{"name": "•••• 0002", "balance": "1000.00", "as_of": "2026-10-25"}])
        self.assertEqual(self.month("business", "2026-10")["closing"], "950.00")
        self.assertEqual(self.month("business", "2026-11")["closing"], "930.00")
        self.assertEqual(self.month("business", "2026-10")["net"], "-50.00")  # the whole month: 100 booked, 150 planned
        self.assertEqual(result["year_ends"][0]["closing"], "930.00")

    def test_actual_so_far_is_reported_with_the_month(self):
        self.add(PERSONAL, "personal", "Shop", [(date(2026, 10, 3), -40.0, 500.0), (date(2026, 10, 4), 10.0, 510.0)])
        actual = self.month("personal", "2026-10")["actual"]
        self.assertEqual(actual, {"income": "10.00", "expense": "40.00"})

    def rich_business(self, balance=10000.0):
        self.add(PERSONAL, "personal", "Start", [(date(2026, 10, 13), 500.0, 500.0)])
        self.add(BUSINESS, "business", "Start2", [(date(2026, 10, 13), 500.0, balance)])

    def test_transfer_tops_personal_up_to_its_minimum(self):
        self.rich_business()
        business, personal = self.lines("business", "2026-10", "transfer"), self.lines("personal", "2026-10", "transfer")
        self.assertEqual((business[0]["amount"], business[0]["category"], business[0]["date"]), ("-1500.00", "Entity transfer", "2026-10-31"))
        self.assertEqual(personal[0]["amount"], "1500.00")
        self.assertEqual(self.month("personal", "2026-10")["closing"], "2000.00")
        self.assertEqual(self.month("business", "2026-10")["closing"], "8500.00")
        self.assertEqual(self.lines("personal", "2026-11", "transfer"), [])  # personal stays at its minimum
        self.assertEqual(self.month("business", "2026-10")["flags"], [])

    def test_transfer_is_limited_by_the_business_buffer_and_flags_a_shortfall(self):
        self.rich_business(2500.0)
        self.assertEqual(self.lines("business", "2026-10", "transfer")[0]["amount"], "-500.00")
        self.assertEqual(self.month("business", "2026-10")["flags"], ["transfer_short"])
        self.assertEqual(self.month("personal", "2026-10")["flags"], ["transfer_short"])
        self.assertEqual(self.month("business", "2026-11")["closing"], "2000.00")

    def test_transfers_fill_what_the_salary_leaves_short(self):
        self.rich_business()
        core.set_salary(self.database, "2026", 100, 12)
        core.set_salary(self.database, "2027", 100, 3)
        # Personal starts at 500: in a salary month, the transfer tops up what the salary of 100 leaves short.
        self.assertEqual(self.lines("personal", "2026-10", "salary")[0]["amount"], "100.00")
        self.assertEqual(self.lines("personal", "2026-10", "transfer")[0]["amount"], "1400.00")
        self.assertEqual(self.month("personal", "2026-10")["closing"], "2000.00")
        # After that the salary keeps Personal above its minimum, during the schedule and after it.
        for month in ("2026-11", "2027-03", "2027-04", "2027-12"):
            self.assertEqual(self.lines("personal", month, "transfer"), [])
    def test_no_transfer_in_the_current_month_when_one_is_booked(self):
        self.rich_business()
        self.add(BUSINESS, "business", "Move", [(date(2026, 10, 14), -100.0, 9900.0)], "Entity transfer")
        self.assertEqual(self.lines("business", "2026-10", "transfer"), [])
        self.assertEqual(self.lines("business", "2026-11", "transfer")[0]["amount"], "-1500.00")

    def test_missing_transfer_category_skips_transfers_with_an_assumption(self):
        self.rich_business()
        core.set_settings(self.database, {"roles": {"transfer_personal": None}})
        self.assertEqual(self.lines("business", "2026-10", "transfer"), [])
        self.assertTrue(any("Entity transfers" in a for a in self.run_forecast("business")["assumptions"]))


class RangeTests(ForecastFixture):
    def history(self, account, account_type, merchant, category, last=-400.0, start=(2025, 12)):
        """A monthly -400 from `start`, except September 2026 which is `last`."""
        payments = []
        year, month = start
        while (year, month) <= (2026, 9):
            payments.append((date(year, month, 10), last if (year, month) == (2026, 9) else -400.0))
            year, month = (year, month + 1) if month < 12 else (year + 1, 1)
        self.add(account, account_type, merchant, payments, category)

    def test_range_comes_from_the_backtest_percentiles(self):
        self.history(PERSONAL, "personal", "Delhaize", "Groceries", last=-500.0)
        result = self.run_forecast("personal")
        backtest = result["backtest"]
        self.assertEqual(backtest["origins"], 3)
        first = backtest["horizons"][0]
        self.assertEqual((first["months"], first["p10"], first["p90"], first["mae"]), (1, "-80.00", "0.00", "33.33"))
        october = self.month("personal", "2026-10")
        low, high = float(october["low"]) - float(october["closing"]), float(october["high"]) - float(october["closing"])
        self.assertEqual((round(low, 2), round(high, 2)), (-80.0, 0.0))
        # Beyond the longest horizon (3) the width grows with the square root of horizon / longest.
        january = self.month("personal", "2027-01")
        self.assertEqual(round(float(january["low"]) - float(january["closing"]), 2), -115.47)
        self.assertIn(backtest["recommended"]["baseline_months"], (6, 12))

    def test_steady_history_gives_a_zero_width_range(self):
        self.history(PERSONAL, "personal", "Delhaize", "Groceries", last=-400.0)
        for month in self.run_forecast("personal")["months"]:
            self.assertEqual((month["low"], month["high"]), (month["closing"], month["closing"]))

    def test_no_history_means_no_range(self):
        self.add(PERSONAL, "personal", "Shop", [(date(2026, 10, 3), -40.0, 500.0)])
        result = self.run_forecast("personal")
        self.assertEqual(result["backtest"]["origins"], 0)
        self.assertEqual((result["months"][3]["low"], result["months"][3]["high"]), (result["months"][3]["closing"],) * 2)

    def test_kids_need_three_origins_for_a_range(self):
        self.history(KIDS, "kids", "Toys", "Pocket money", last=-500.0, start=(2026, 1))  # 2 origins
        kids = self.run_forecast("kids")
        self.assertEqual(kids["backtest"]["origins"], 2)
        self.assertEqual(kids["backtest"]["horizons"], [])
        self.assertTrue(all(m["low"] == m["closing"] == m["high"] for m in kids["months"]))
        self.history(KIDS, "kids", "More toys", "Pocket money", last=-500.0, start=(2025, 12))
        self.assertNotEqual(self.run_forecast("kids")["months"][0]["low"], self.run_forecast("kids")["months"][0]["closing"])


class SettingsEndpointTests(ForecastFixture):
    def test_defaults_are_resolved_at_read_time(self):
        settings = self.client.get("/api/forecast/settings").json()
        self.assertEqual((settings["personal_min_balance"], settings["business_buffer"]), ("2000.00", "2000.00"))
        self.assertEqual((settings["payment_delay_months"], settings["vat_rate"], settings["vat_day"]), (1, 21, 15))
        self.assertEqual(settings["roles"]["consulting_income"], self.cat["business", "Consulting income"])
        self.assertEqual(settings["roles"]["transfer_personal"], self.cat["personal", "Entity transfer"])
        self.assertEqual(settings["closed_categories"], {"personal": [self.cat["personal", "Old loan"]], "business": [], "kids": []})

    def test_put_stores_a_subset_and_returns_everything(self):
        response = self.client.put("/api/forecast/settings", json={
            "business_buffer": "2.500,50", "vat_rate": 6, "baseline_weighted": True,
            "roles": {"vat": None}, "closed_categories": {"kids": [self.cat["kids", "Pocket money"]]},
        })
        self.assertEqual(response.status_code, 200)
        settings = response.json()
        self.assertEqual((settings["business_buffer"], settings["vat_rate"], settings["baseline_weighted"]), ("2500.50", 6, True))
        self.assertIsNone(settings["roles"]["vat"])
        self.assertEqual(settings["roles"]["salary_personal"], self.cat["personal", "Salary"])
        self.assertEqual(settings["closed_categories"]["kids"], [self.cat["kids", "Pocket money"]])
        self.assertEqual(settings["closed_categories"]["personal"], [self.cat["personal", "Old loan"]])
        self.assertEqual(self.client.get("/api/forecast/settings").json(), settings)

    def test_validation(self):
        for body in (
            {"business_buffer": "-5"}, {"business_buffer": "abc"}, {"payment_delay_months": 7}, {"vat_delay_months": -1},
            {"vat_day": 29}, {"vat_day": 0}, {"baseline_months": 2}, {"baseline_months": 13}, {"one_off_factor": 0.5},
            {"vat_rate": 101}, {"roles": {"vat": self.cat["personal", "Salary"]}}, {"roles": {"nonsense": None}},
            {"closed_categories": {"kids": [self.cat["personal", "Salary"]]}},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.client.put("/api/forecast/settings", json=body).status_code, 400)
        self.assertEqual(self.client.put("/api/forecast/settings", json={"business_buffer": 0}).json()["business_buffer"], "0.00")

    def test_merging_and_removing_categories_follow_the_settings_and_planned_items(self):
        old_loan, salary = self.cat["personal", "Old loan"], self.cat["personal", "Salary"]
        other = core.add_category(self.database, "personal", "Pay")["id"]
        planned = core.add_planned(self.database, "personal", "2027-01-01", 100, salary, "Bonus")
        core.merge_categories(self.database, "personal", salary, other)
        settings = core.get_settings(self.database)
        self.assertEqual(settings["roles"]["salary_personal"], other)
        self.assertEqual(core.list_planned(self.database, "personal")[0]["category_id"], other)
        core.merge_categories(self.database, "personal", old_loan, other)
        self.assertEqual(core.get_settings(self.database)["closed_categories"]["personal"], [other])
        core.remove_category(self.database, other)
        settings = core.get_settings(self.database)
        self.assertIsNone(settings["roles"]["salary_personal"])
        self.assertEqual(settings["closed_categories"]["personal"], [])
        self.assertIsNone(core.list_planned(self.database, "personal")[0]["category_id"])
        self.assertEqual(planned["id"], core.list_planned(self.database, "personal")[0]["id"])


class InputEndpointTests(ForecastFixture):
    def test_every_setting_can_be_saved_through_the_endpoint(self):
        # A setting missing from the request model is silently dropped by the endpoint.
        from backend.pfm.core.forecast_settings import DEFAULTS
        from backend.pfm.server import ForecastSettingsUpdate
        self.assertLessEqual(set(DEFAULTS), set(ForecastSettingsUpdate.model_fields))
        response = self.client.put("/api/forecast/settings", json={"salary_net_percent": 82.5})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["salary_net_percent"], 82.5)

    def test_salary_schedules(self):
        self.assertEqual(self.client.get("/api/forecast/salary").json(), [])
        response = self.client.put("/api/forecast/salary/2027", json={"monthly_amount": "5.500,00", "last_month": 6})
        self.assertEqual(response.json(), {"year": "2027", "monthly_amount": "5500.00", "last_month": 6, "minimum_amount": "0.00"})
        minimum = self.client.put("/api/forecast/salary/2028", json={"monthly_amount": 5000, "last_month": 6, "minimum_amount": "250,00"})
        self.assertEqual(minimum.json()["minimum_amount"], "250.00")
        self.assertEqual(self.client.put("/api/forecast/salary/2028", json={"monthly_amount": 5000, "last_month": 6, "minimum_amount": "-1"}).status_code, 400)
        self.client.delete("/api/forecast/salary/2028")
        self.client.put("/api/forecast/salary/2026", json={"monthly_amount": 5000, "last_month": 12})
        self.assertEqual([s["year"] for s in self.client.get("/api/forecast/salary").json()], ["2026", "2027"])
        self.assertEqual(self.client.put("/api/forecast/salary/2027", json={"monthly_amount": 6000, "last_month": 7}).json()["last_month"], 7)
        for year, body in (("27", {"monthly_amount": 1, "last_month": 1}), ("2027", {"monthly_amount": 0, "last_month": 1}),
                           ("2027", {"monthly_amount": -5, "last_month": 1}), ("2027", {"monthly_amount": 5, "last_month": 13}),
                           ("2027", {"monthly_amount": 5, "last_month": 0})):
            self.assertEqual(self.client.put(f"/api/forecast/salary/{year}", json=body).status_code, 400)
        self.assertEqual(self.client.delete("/api/forecast/salary/2027").status_code, 200)
        self.assertEqual(self.client.delete("/api/forecast/salary/2027").status_code, 404)

    def test_day_rates(self):
        self.client.put("/api/forecast/day-rates/2026-09-01", json={"rate": 850})
        self.client.put("/api/forecast/day-rates/2026-01-01", json={"rate": "800,50"})
        self.assertEqual(self.client.get("/api/forecast/day-rates").json(), [
            {"effective_date": "2026-01-01", "rate": "800.50"}, {"effective_date": "2026-09-01", "rate": "850.00"}])
        self.assertEqual(self.client.put("/api/forecast/day-rates/not-a-date", json={"rate": 1}).status_code, 400)
        self.assertEqual(self.client.put("/api/forecast/day-rates/2026-02-01", json={"rate": 0}).status_code, 400)
        self.assertEqual(self.client.delete("/api/forecast/day-rates/2026-09-01").status_code, 200)
        self.assertEqual(self.client.delete("/api/forecast/day-rates/2026-09-01").status_code, 404)

    def test_days_off(self):
        created = self.client.post("/api/forecast/days-off", json={
            "start_date": "2026-12-28", "end_date": "2026-12-30", "fraction": 0.5, "note": "Holiday"}).json()
        self.assertEqual((created["fraction"], created["note"]), (0.5, "Holiday"))
        self.assertEqual(self.client.post("/api/forecast/days-off", json={"start_date": "2026-12-28", "end_date": "2026-12-28"}).json()["fraction"], 1)
        self.assertEqual(len(self.client.get("/api/forecast/days-off").json()), 2)
        for body in ({"start_date": "2026-12-30", "end_date": "2026-12-28"}, {"start_date": "x", "end_date": "2026-12-28"},
                     {"start_date": "2026-12-28", "end_date": "2026-12-28", "fraction": 0.3}):
            self.assertEqual(self.client.post("/api/forecast/days-off", json=body).status_code, 400)
        for body in ({"start_date": "2026-12-28", "end_date": "2027-01-11", "repeat_weeks": 2},
                     {"start_date": "2026-12-28", "end_date": "2026-12-28", "repeat_until": "2027-06-30"},
                     {"start_date": "2026-12-28", "end_date": "2026-12-28", "repeat_weeks": 2, "repeat_until": "2026-12-01"},
                     {"start_date": "2026-12-28", "end_date": "2026-12-28", "repeat_weeks": 53}):
            self.assertEqual(self.client.post("/api/forecast/days-off", json=body).status_code, 400)
        repeating = self.client.post("/api/forecast/days-off", json={
            "start_date": "2026-11-04", "end_date": "2026-11-04", "fraction": 0.5, "repeat_weeks": 2, "repeat_until": "2026-12-02"}).json()
        self.assertEqual((repeating["repeat_weeks"], repeating["repeat_until"]), (2, "2026-12-02"))
        with core.connect(self.database) as db:
            spans = [s for s in load_days_off(db, date(2027, 12, 31)) if date(2026, 11, 1) <= s[0] < date(2026, 12, 28)]
        self.assertEqual([s[0] for s in spans], [date(2026, 11, 4), date(2026, 11, 18), date(2026, 12, 2)])
        self.assertEqual(self.client.delete(f"/api/forecast/days-off/{created['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/forecast/days-off/{created['id']}").status_code, 404)

    def test_planned_items(self):
        category = self.cat["business", "Corporate tax"]
        first = self.client.post("/api/planned", json={
            "account_type": "business", "date": "2027-03-31", "amount": "-5.000,00", "category_id": category, "note": "Tax"}).json()
        second = self.client.post("/api/planned", json={"account_type": "business", "date": "2027-06-01", "amount": 100}).json()
        self.assertEqual((first["amount"], first["category"], first["linked"]), ("-5000.00", "Corporate tax", None))
        listed = self.client.get("/api/planned", params={"account_type": "business"}).json()
        self.assertEqual([item["id"] for item in listed], [second["id"], first["id"]])  # newest date first
        patched = self.client.patch(f"/api/planned/{first['id']}", json={"account_type": "business", "amount": -6000, "category_id": None}).json()
        self.assertEqual((patched["amount"], patched["category_id"], patched["note"]), ("-6000.00", None, "Tax"))
        wrong_entity = self.client.patch(f"/api/planned/{first['id']}", json={"account_type": "personal", "note": "x"})
        self.assertEqual(wrong_entity.status_code, 404)
        self.assertEqual(self.client.patch(f"/api/planned/{first['id']}", json={"account_type": "business", "date": "oops"}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/planned/{first['id']}", json={
            "account_type": "business", "category_id": self.cat["personal", "Salary"]}).status_code, 400)
        self.assertEqual(self.client.post("/api/planned", json={"account_type": "business", "date": "2027-06-01", "amount": 0}).status_code, 400)

    def test_planned_links_run_both_ways_between_entities(self):
        a = self.client.post("/api/planned", json={"account_type": "business", "date": "2027-03-31", "amount": -100}).json()
        b = self.client.post("/api/planned", json={"account_type": "personal", "date": "2027-03-31", "amount": 100}).json()
        same = self.client.post("/api/planned", json={"account_type": "business", "date": "2027-03-31", "amount": 1}).json()
        linked = self.client.put(f"/api/planned/{a['id']}/link", json={"account_type": "business", "linked_item_id": b["id"]}).json()
        self.assertEqual(linked["linked"]["id"], b["id"])
        back = self.client.get("/api/planned", params={"account_type": "personal"}).json()[0]
        self.assertEqual(back["linked"]["id"], a["id"])
        self.assertEqual(self.client.put(f"/api/planned/{a['id']}/link", json={"account_type": "business", "linked_item_id": same["id"]}).status_code, 400)
        self.assertEqual(self.client.put(f"/api/planned/{a['id']}/link", json={"account_type": "business", "linked_item_id": 999}).status_code, 404)
        self.client.put(f"/api/planned/{a['id']}/link", json={"account_type": "business", "linked_item_id": None})
        self.assertIsNone(self.client.get("/api/planned", params={"account_type": "personal"}).json()[0]["linked"])
        self.client.put(f"/api/planned/{a['id']}/link", json={"account_type": "business", "linked_item_id": b["id"]})
        self.assertEqual(self.client.delete(f"/api/planned/{a['id']}", params={"account_type": "business"}).status_code, 200)
        self.assertIsNone(self.client.get("/api/planned", params={"account_type": "personal"}).json()[0]["linked"])

    def test_forecast_endpoint_returns_one_entity(self):
        self.add(BUSINESS, "business", "Start", [(date(2026, 10, 13), 500.0, 1000.0)])
        response = self.client.get("/api/forecast", params={"account_type": "business"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["account_type"], body["opening"]), ("business", "1000.00"))
        self.assertEqual(set(body), {"account_type", "today", "accounts", "opening", "months", "year_ends", "one_offs", "assumptions", "backtest"})
        self.assertEqual(set(body["months"][0]), {"month", "partial", "lines", "actual", "income", "expense", "net", "closing", "low", "high", "flags"})
        self.assertEqual(self.client.get("/api/forecast", params={"account_type": "nobody"}).status_code, 422)
        self.assertEqual(self.client.put("/api/forecast/settings", json={"closed_categories": {"nowhere": []}}).status_code, 422)


if __name__ == "__main__":
    unittest.main()


class DaysOffRepeatMigrationTests(unittest.TestCase):
    def test_an_older_database_gains_the_repeat_columns_after_a_backup(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ):
            os.environ.pop("PFM_BACKUP_DIR", None)
            os.environ.pop("PFM_HOME", None)
            database, backups = Path(folder) / "finance.sqlite3", Path(folder) / "backups"
            core.initialize(database, backups)
            core.add_day_off(database, "2026-12-21", "2026-12-24", 1, "Christmas")
            with core.connect(database) as db:
                db.execute("ALTER TABLE days_off DROP COLUMN repeat_until")
                db.execute("ALTER TABLE days_off DROP COLUMN repeat_weeks")
            self.assertIsNotNone(core.initialize(database, backups))
            [row] = core.list_days_off(database)
            self.assertEqual((row["repeat_weeks"], row["repeat_until"]), (0, None))


class SalaryMinimumMigrationTests(unittest.TestCase):
    def test_an_older_database_gains_the_minimum_column_after_a_backup(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ):
            os.environ.pop("PFM_BACKUP_DIR", None)
            os.environ.pop("PFM_HOME", None)
            database, backups = Path(folder) / "finance.sqlite3", Path(folder) / "backups"
            core.initialize(database, backups)
            core.set_salary(database, "2027", 9000, 6)
            with core.connect(database) as db:
                db.execute("ALTER TABLE salary_schedules DROP COLUMN minimum_cents")
            backup = core.initialize(database, backups)
            self.assertIsNotNone(backup)
            self.assertEqual(core.list_salary(database), [
                {"year": "2027", "monthly_amount": "9000.00", "last_month": 6, "minimum_amount": "0.00"},
            ])

