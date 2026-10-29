import calendar
import itertools
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.pfm import core
from backend.pfm.core.recurring import _add_months, _detect, _next_date
from backend.pfm.server import create_app
from tests.test_core import make_csv, transaction

ACCOUNT = "BE00000000000000000001"
SECOND = "BE00000000000000000002"
KIDS = "BE00000000000000000009"
TODAY = date.today()


def monthly_dates(count, day=3, step=1):
    """`count` dates `step` months apart on `day`, the latest being the most recent one not after today."""
    latest = TODAY.replace(day=1)
    if _add_months(latest, 0, day) > TODAY:
        latest = _add_months(latest, -1)
    return [_add_months(latest, -step * back, day) for back in range(count - 1, -1, -1)]


class RecurringFixture(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        os.environ.pop("PFM_HOME", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "finance.sqlite3"
        core.initialize(self.database)
        self.client = TestClient(create_app(self.database, self.root / "dist"))
        self.addCleanup(self.client.close)
        self.staging = {}
        self.balance = itertools.count(1)
        self.insurance = core.add_category(self.database, "personal", "Insurance")["id"]
        self.other = core.add_category(self.database, "personal", "Other")["id"]
        self.business_category = core.add_category(self.database, "business", "Tools")["id"]

    def rows(self, account, merchant, payments, description=""):
        """CSV rows for (date, amount in euro) payments."""
        return [
            transaction(
                account, day.strftime("%d/%m/%Y"), f"{amount:.2f}".replace(".", ","),
                f"{next(self.balance)},00", merchant, description,
            )
            for day, amount in payments
        ]

    def import_rows(self, rows, account_type="personal"):
        preview = core.stage_import(self.database, "export.csv", make_csv(rows).encode(), self.staging)
        core.commit_import(self.database, preview["token"], account_type, self.staging)

    def add(self, merchant, payments, account=ACCOUNT, account_type="personal"):
        self.import_rows(self.rows(account, merchant, payments), account_type)

    def add_series(self, merchant, amounts, day=3, step=1, **kwargs):
        dates = monthly_dates(len(amounts), day, step)
        self.add(merchant, list(zip(dates, amounts)), **kwargs)
        return dates

    def listing(self, account_type="personal"):
        return core.list_recurring(self.database, account_type, TODAY)

    def candidate(self, merchant, account_type="personal"):
        found = [c for c in self.listing(account_type)["candidates"] if c["merchant"] == merchant]
        return found[0] if found else None

    def confirm(self, merchant, status="confirmed", account_type="personal", **kwargs):
        return core.confirm_recurring(self.database, account_type, merchant, "", status, today=TODAY, **kwargs)

    def categorize(self, merchant, category_id):
        with core.connect(self.database) as db:
            db.execute("UPDATE transactions SET category_id = ? WHERE merchant = ?", (category_id, merchant))


class DetectionTests(RecurringFixture):
    def test_stable_monthly_payment_is_a_candidate(self):
        dates = self.add_series("Verzekeraar", [-150.0] * 13)
        self.categorize("Verzekeraar", self.insurance)
        item = self.candidate("Verzekeraar")
        self.assertIsNone(item["id"])
        self.assertEqual(item["status"], "candidate")
        self.assertEqual((item["cadence"], item["day_of_month"], item["kind"]), ("monthly", 3, ""))
        self.assertEqual((item["amount"], item["monthly_amount"]), ("-150.00", "-150.00"))
        self.assertEqual(item["amounts"], [{"effective_date": dates[0].isoformat(), "amount": "-150.00"}])
        self.assertEqual((item["category_id"], item["category"]), (self.insurance, "Insurance"))
        self.assertEqual(item["start_date"], dates[0].isoformat())
        self.assertEqual(item["last_date"], dates[-1].isoformat())
        self.assertEqual(item["next_date"], _add_months(dates[-1], 1).isoformat())
        self.assertEqual(item["occurrences"], 13)
        self.assertEqual(item["flags"], [])
        self.assertIsNone(item["linked"])
        history = item["history"]
        self.assertEqual(len(history), 13)
        self.assertEqual(history[-1]["period"], TODAY.strftime("%Y-%m"))

    def test_next_date_is_clamped_to_the_month_end(self):
        dates = self.add_series("Rent", [-500.0] * 6, day=31)
        item = self.candidate("Rent")
        following = date.fromisoformat(item["next_date"])
        self.assertEqual(following.month, _add_months(dates[-1].replace(day=1), 1).month)
        self.assertEqual(following.day, min(item["day_of_month"], calendar.monthrange(following.year, following.month)[1]))

    def test_payment_slipping_across_a_month_boundary_stays_monthly(self):
        dates = monthly_dates(7, day=1)
        dates[3] = dates[3] - timedelta(days=2)  # booked on the last day of the previous month
        self.add("Gym", [(day, -30.0) for day in dates])
        item = self.candidate("Gym")
        self.assertEqual((item["cadence"], item["occurrences"], item["amount"]), ("monthly", 7, "-30.00"))
        self.assertEqual(item["day_of_month"], 1)

    def test_next_date_follows_the_due_day_after_a_slipped_payment(self):
        # Due on the 30th: a payment that slipped to 1 October is September's, so the next is 30 October.
        self.assertEqual(_next_date("monthly", date(2026, 10, 1), 30, date(2026, 1, 30), None), date(2026, 10, 30))
        # Paid early on 28 September for 1 October: the next is 1 November.
        self.assertEqual(_next_date("monthly", date(2026, 9, 28), 1, date(2026, 1, 1), None), date(2026, 11, 1))
        self.assertEqual(_next_date("monthly", date(2026, 10, 3), 3, date(2026, 1, 3), None), date(2026, 11, 3))
        self.assertEqual(_next_date("quarterly", date(2026, 10, 1), 30, date(2026, 1, 30), None), date(2026, 12, 30))
        self.assertEqual(_next_date("yearly", date(2026, 3, 2), 28, date(2025, 2, 28), None), date(2027, 2, 28))
        self.assertIsNone(_next_date("monthly", date(2026, 10, 3), 3, date(2026, 1, 3), date(2026, 10, 31)))

    def test_same_day_parts_are_one_occurrence(self):
        dates = monthly_dates(6, day=5)
        payments = [(day, amount) for day in dates for amount in (-300.0, -200.0)]
        self.add("Bank loan", payments)
        item = self.candidate("Bank loan")
        self.assertEqual((item["amount"], item["occurrences"]), ("-500.00", 6))

    def test_step_change_gives_two_schedule_entries_and_a_price_change_flag(self):
        dates = self.add_series("Streaming", [-50.0] * 7 + [-60.0] * 4)
        item = self.candidate("Streaming")
        self.assertEqual(item["amounts"], [
            {"effective_date": dates[0].isoformat(), "amount": "-50.00"},
            {"effective_date": dates[7].isoformat(), "amount": "-60.00"},
        ])
        self.assertEqual(item["amount"], "-60.00")
        self.assertIn("price_change", item["flags"])

    def test_old_step_change_has_no_price_change_flag(self):
        self.add_series("Streaming", [-50.0] * 4 + [-60.0] * 10)
        item = self.candidate("Streaming")
        self.assertEqual(len(item["amounts"]), 2)
        self.assertNotIn("price_change", item["flags"])

    def test_new_flag_for_a_recent_first_occurrence(self):
        self.add_series("Fresh", [-9.0] * 5)
        self.add_series("Old", [-9.0] * 13)
        self.assertIn("new", self.candidate("Fresh")["flags"])
        self.assertNotIn("new", self.candidate("Old")["flags"])

    def test_frequent_varying_purchases_are_not_candidates(self):
        start = TODAY - timedelta(days=200)
        amounts = [-23.4, -61.9, -12.3, -88.0, -35.5, -47.2, -9.9, -72.1]
        payments = [(start + timedelta(days=5 * i), amounts[i % len(amounts)]) for i in range(40)]
        self.add("Groceries", payments)
        self.assertIsNone(self.candidate("Groceries"))

    def test_a_stopped_payment_is_not_suggested(self):
        dates = monthly_dates(14)
        self.add("Old gym", [(day, -30.0) for day in dates[:6]])
        self.add("Running", [(day, -30.0) for day in dates[3:]])
        self.assertIsNone(self.candidate("Old gym"))
        self.assertIsNotNone(self.candidate("Running"))

    def test_quarterly_and_yearly_detection(self):
        quarterly = self.add_series("Water", [-120.0] * 4, step=3)
        item = self.candidate("Water")
        self.assertEqual((item["cadence"], item["amount"], item["monthly_amount"]), ("quarterly", "-120.00", "-40.00"))
        self.assertEqual(item["next_date"], _add_months(quarterly[-1], 3).isoformat())
        yearly = self.add_series("Club", [-90.0, -96.0], step=12, day=15)
        item = self.candidate("Club")
        self.assertEqual((item["cadence"], item["amount"], item["monthly_amount"]), ("yearly", "-96.00", "-8.00"))
        self.assertEqual(len(item["amounts"]), 2)
        self.assertEqual(item["next_date"], _add_months(yearly[-1], 12).isoformat())

    def test_a_one_off_between_regular_amounts_is_skipped(self):
        dates = self.add_series("Accountant", [-350.0] * 5 + [-930.0] + [-350.0] * 4)
        item = self.candidate("Accountant")
        self.assertEqual(item["amounts"], [{"effective_date": dates[0].isoformat(), "amount": "-350.00"}])

    def test_a_payment_in_two_parts_a_week_apart_is_one_occurrence(self):
        dates = monthly_dates(6, day=1)
        payments = [(day, -2100.0) for day in dates]
        payments[2] = (dates[2], -1450.0)
        payments.append((dates[2] + timedelta(days=6), -650.0))
        self.add("Business loan", payments)
        item = self.candidate("Business loan")
        self.assertEqual((item["amount"], item["occurrences"]), ("-2100.00", 6))

    def test_a_yearly_payment_may_move_a_few_weeks(self):
        # Paid on 25 January, then on 30 December: 339 days apart.
        days = [TODAY - timedelta(days=339 + 30), TODAY - timedelta(days=30)]
        occurrences = [{"date": day, "last": day, "cents": -222383, "categories": [None]} for day in days]
        self.assertEqual(_detect(occurrences, TODAY, "Transfer")["cadence"], "yearly")
        early = [{"date": day, "last": day, "cents": -222383, "categories": [None]} for day in (TODAY - timedelta(days=300 + 30), TODAY - timedelta(days=30))]
        self.assertIsNone(_detect(early, TODAY, "Transfer"))

    def test_yearly_needs_steady_amounts_unless_it_is_a_direct_debit(self):
        def occurrences(*amounts):
            days = [TODAY - timedelta(days=365 * back + 10) for back in range(len(amounts) - 1, -1, -1)]
            return [{"date": day, "last": day, "cents": cents, "categories": [None]} for day, cents in zip(days, amounts)]

        self.assertIsNone(_detect(occurrences(-2364, -1590), TODAY, "Card"))  # a holiday shop
        self.assertEqual(_detect(occurrences(-6400, -10000), TODAY, "Direct debit")["cadence"], "yearly")
        steady = _detect(occurrences(-12027, -12312), TODAY, "Transfer")
        self.assertEqual([run[0]["cents"] for run in steady["runs"]], [-12027, -12312])
        same = _detect(occurrences(-47850, -47850), TODAY, "Transfer")
        self.assertEqual(len(same["runs"]), 1)

    def test_a_single_refund_is_a_one_off_but_mixed_signs_are_rejected(self):
        dates = monthly_dates(8)
        self.add("Refunded", [(day, -10.0 if i != 5 else 10.0) for i, day in enumerate(dates)])
        self.assertEqual(self.candidate("Refunded")["amount"], "-10.00")
        self.add("Odd", [(day, -10.0 if i % 2 else 10.0) for i, day in enumerate(dates)])
        self.assertIsNone(self.candidate("Odd"))

    def test_own_transfers_and_replaced_settlements_are_excluded(self):
        dates = monthly_dates(8)
        self.import_rows(self.rows(SECOND, "Seed", [(dates[0], -1.0)]))
        rows = self.rows(ACCOUNT, "Savings", [(day, -100.0) for day in dates])
        for row in rows:
            row["rekeningnummer tegenpartij"] = SECOND
        self.import_rows(rows)
        self.add("Card bill", [(day, -80.0) for day in dates])
        with core.connect(self.database) as db:
            account = db.execute("SELECT id FROM accounts WHERE iban = ?", (ACCOUNT,)).fetchone()["id"]
            statement = db.execute(
                "INSERT INTO card_statements(account_id, statement_key, debit_date) VALUES (?, 'k', '2026-01-01')",
                (account,),
            ).lastrowid
            db.execute("UPDATE transactions SET replaced_by_statement_id = ? WHERE merchant = 'Card bill'", (statement,))
        self.assertIsNone(self.candidate("Savings"))
        self.assertIsNone(self.candidate("Card bill"))

    def test_deleted_transactions_and_other_entities_are_ignored(self):
        self.add_series("Tool", [-20.0] * 6, account=KIDS, account_type="kids")
        self.assertIsNone(self.candidate("Tool"))
        self.assertIsNotNone(self.candidate("Tool", "kids"))
        with core.connect(self.database) as db:
            db.execute("UPDATE transactions SET deleted_at = '2026-01-01T00:00:00'")
        self.assertIsNone(self.candidate("Tool", "kids"))


class DecisionTests(RecurringFixture):
    def test_confirm_ignore_and_delete(self):
        dates = self.add_series("Verzekeraar", [-150.0] * 13)
        self.add_series("Netflix", [-15.0] * 8)
        item = self.confirm("Verzekeraar")
        self.assertIsInstance(item["id"], int)
        self.assertEqual((item["status"], item["cadence"], item["amount"]), ("confirmed", "monthly", "-150.00"))
        self.assertEqual(item["start_date"], dates[0].isoformat())
        listing = self.listing()
        self.assertEqual([i["merchant"] for i in listing["items"]], ["Verzekeraar"])
        self.assertEqual([i["merchant"] for i in listing["candidates"]], ["Netflix"])
        ignored = self.confirm("Netflix", "ignored")
        listing = self.listing()
        self.assertEqual(listing["candidates"], [])
        self.assertEqual([i["merchant"] for i in listing["ignored"]], ["Netflix"])
        self.assertEqual(listing["ignored"][0]["status"], "ignored")
        self.assertEqual(listing["ignored"][0]["flags"], [])
        core.delete_recurring(self.database, "personal", item["id"])
        core.delete_recurring(self.database, "personal", ignored["id"])
        listing = self.listing()
        self.assertEqual(sorted(i["merchant"] for i in listing["candidates"]), ["Netflix", "Verzekeraar"])
        self.assertEqual((listing["items"], listing["ignored"]), ([], []))
        with core.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recurring_amounts").fetchone()[0], 0)

    def test_confirm_with_overrides(self):
        self.add_series("Verzekeraar", [-150.0] * 6)
        end = (TODAY + timedelta(days=100)).isoformat()
        item = self.confirm(
            "verzekeraar", cadence="quarterly", category_id=self.other, amount="-170,50",
            day_of_month=9, end_date=end, note="check",
        )
        self.assertEqual(item["merchant"], "Verzekeraar")
        self.assertEqual((item["cadence"], item["category_id"], item["day_of_month"]), ("quarterly", self.other, 9))
        self.assertEqual((item["amount"], item["end_date"], item["note"]), ("-170.50", end, "check"))
        self.assertEqual(len(item["amounts"]), 1)

    def test_errors(self):
        self.add_series("Verzekeraar", [-150.0] * 6)
        self.confirm("Verzekeraar")
        with self.assertRaises(ValueError):
            self.confirm("Verzekeraar")
        with self.assertRaises(LookupError):
            self.confirm("Nobody")
        self.add_series("Netflix", [-15.0] * 6)
        with self.assertRaises(ValueError):
            self.confirm("Netflix", category_id=self.business_category)
        with self.assertRaises(ValueError):
            self.confirm("Netflix", status="maybe")
        with self.assertRaises(ValueError):
            self.confirm("Netflix", end_date="2000-01-01")

    def test_update_replaces_the_amount_schedule_and_validates(self):
        self.add_series("Verzekeraar", [-150.0] * 6)
        item = self.confirm("Verzekeraar")
        update = lambda **changes: core.update_recurring(self.database, "personal", item["id"], changes, TODAY)
        old, new = (TODAY - timedelta(days=400)).isoformat(), (TODAY - timedelta(days=30)).isoformat()
        updated = update(
            amounts=[{"effective_date": new, "amount": "-180"}, {"effective_date": old, "amount": "-170"}],
            category_id=self.insurance, cadence="yearly", day_of_month=12, note="n", start_date=old,
        )
        self.assertEqual([a["amount"] for a in updated["amounts"]], ["-170.00", "-180.00"])
        self.assertEqual((updated["amount"], updated["monthly_amount"]), ("-180.00", "-15.00"))
        self.assertEqual((updated["category"], updated["cadence"], updated["day_of_month"]), ("Insurance", "yearly", 12))
        self.assertEqual(update(category_id=None)["category_id"], None)
        self.assertEqual(update(end_date=TODAY.isoformat())["end_date"], TODAY.isoformat())
        self.assertIsNone(update(end_date=None)["end_date"])
        for bad in (
            {"amounts": []},
            {"amounts": [{"effective_date": old, "amount": "-1"}, {"effective_date": old, "amount": "-2"}]},
            {"amounts": [{"effective_date": old, "amount": "0"}]},
            {"amounts": [{"effective_date": "tomorrow", "amount": "-1"}]},
            {"category_id": self.business_category},
            {"end_date": "1999-01-01"},
            {"cadence": "weekly"},
            {"day_of_month": 32},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                update(**bad)
        with self.assertRaises(LookupError):
            core.update_recurring(self.database, "business", item["id"], {"note": "x"}, TODAY)
        self.assertEqual(update()["note"], "n")

    def test_stopped_flag_on_a_confirmed_item(self):
        dates = monthly_dates(14)
        self.add("Running", [(day, -30.0) for day in dates[3:]])
        item = self.confirm("Running")
        self.assertEqual(item["flags"], [])
        # Six monthly payments, the last one 200 days ago: confirmed earlier, stopped since.
        last = TODAY - timedelta(days=200)
        paid = [_add_months(last, -back) for back in range(5, -1, -1)]
        self.add("Gone", [(day, -10.0) for day in paid])
        with core.connect(self.database) as db:
            db.execute(
                """
                INSERT INTO recurring_items(account_type, merchant, merchant_key, kind, status, cadence,
                    day_of_month, start_date, created_at, updated_at)
                VALUES ('personal', 'Gone', 'gone', '', 'confirmed', 'monthly', ?, ?, 'x', 'x')
                """,
                (last.day, paid[0].isoformat()),
            )
            row = db.execute("SELECT id FROM recurring_items WHERE merchant = 'Gone'").fetchone()
            db.execute(
                "INSERT INTO recurring_amounts(item_id, effective_date, amount_cents) VALUES (?, ?, -1000)",
                (row["id"], paid[0].isoformat()),
            )
        gone = [i for i in self.listing()["items"] if i["merchant"] == "Gone"][0]
        self.assertEqual(gone["next_date"], _add_months(last, 1).isoformat())
        self.assertIn("stopped", gone["flags"])
        self.assertEqual(gone["last_date"], last.isoformat())
        ended = core.update_recurring(self.database, "personal", gone["id"], {"end_date": TODAY.isoformat()}, TODAY)
        self.assertNotIn("stopped", ended["flags"])
        self.assertEqual(ended["next_date"], gone["next_date"])  # still before the end date
        self.assertEqual(self.listing()["monthly"]["expense"], "-40.00")

    def test_price_change_flag_on_a_confirmed_item(self):
        self.add_series("Streaming", [-50.0] * 6)
        item = self.confirm("Streaming")
        self.assertEqual(item["flags"], [])
        # The scheduled amount drifts from what was actually paid.
        scheduled = lambda cents: core.update_recurring(
            self.database, "personal", item["id"], {"amounts": [{"effective_date": item["start_date"], "amount": cents}]}, TODAY
        )
        self.assertIn("price_change", scheduled("-40")["flags"])
        self.assertIn("price_change", scheduled("-60")["flags"])
        self.assertNotIn("price_change", scheduled("-52")["flags"])


class TotalsTests(RecurringFixture):
    def test_monthly_totals_and_order(self):
        self.add_series("Salary", [2000.0] * 6, day=1)
        self.add_series("Verzekeraar", [-150.0] * 6)
        self.add_series("Water", [-120.0] * 4, step=3)
        self.add_series("Club", [-96.0, -96.0], step=12, day=15)
        self.add_series("Old", [-1000.0] * 6, day=2)
        for merchant in ("Salary", "Verzekeraar", "Water", "Club", "Old"):
            self.confirm(merchant)
        old = self.listing()["items"]
        old_id = [i["id"] for i in old if i["merchant"] == "Old"][0]
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        core.update_recurring(self.database, "personal", old_id, {"end_date": yesterday}, TODAY)
        listing = self.listing()
        self.assertEqual(listing["monthly"], {"income": "2000.00", "expense": "-198.00", "net": "1802.00"})
        # Largest monthly amount first; the quarterly and yearly ones count a third and a twelfth.
        self.assertEqual([i["merchant"] for i in listing["items"]], ["Salary", "Old", "Verzekeraar", "Water", "Club"])
        self.assertEqual([i["monthly_amount"] for i in listing["items"]][2:], ["-150.00", "-40.00", "-8.00"])

    def test_history_has_thirteen_months_with_nulls(self):
        self.add_series("Rent", [-500.0] * 4, step=3)
        item = self.candidate("Rent")
        history = item["history"]
        self.assertEqual(len(history), 13)
        self.assertEqual(len({h["period"] for h in history}), 13)
        self.assertEqual(sum(h["amount"] is not None for h in history), 4)
        self.assertEqual([h["amount"] for h in history if h["amount"]], ["-500.00"] * 4)


class LinkTests(RecurringFixture):
    def setUp(self):
        super().setUp()
        self.add_series("Pocket money", [-50.0] * 6)
        self.add_series("Top-up", [50.0] * 6, account=KIDS, account_type="kids")
        self.add_series("Top-up 2", [20.0] * 6, account=KIDS, account_type="kids")
        self.add_series("Tools", [-30.0] * 6, account=SECOND, account_type="business")
        self.mine = self.confirm("Pocket money")
        self.kids = self.confirm("Top-up", account_type="kids")
        self.kids2 = self.confirm("Top-up 2", account_type="kids")
        self.biz = self.confirm("Tools", account_type="business")

    def link(self, item, other, account_type="personal"):
        return core.link_recurring(self.database, account_type, item["id"], other["id"] if other else None, TODAY)

    def linked(self, account_type, item):
        return [i for i in self.listing(account_type)["items"] if i["id"] == item["id"]][0]["linked"]

    def test_link_is_symmetric(self):
        result = self.link(self.mine, self.kids)
        self.assertEqual(result["linked"], {"id": self.kids["id"], "account_type": "kids", "merchant": "Top-up", "kind": ""})
        self.assertEqual(self.linked("kids", self.kids)["id"], self.mine["id"])
        self.assertEqual(self.linked("kids", self.kids)["account_type"], "personal")

    def test_new_link_replaces_old_ones_on_both_sides(self):
        self.link(self.mine, self.kids)
        self.link(self.mine, self.kids2)
        self.assertIsNone(self.linked("kids", self.kids))
        self.assertEqual(self.linked("personal", self.mine)["id"], self.kids2["id"])
        self.link(self.biz, self.kids2, "business")
        self.assertIsNone(self.linked("personal", self.mine))
        self.assertEqual(self.linked("kids", self.kids2)["id"], self.biz["id"])

    def test_unlink_clears_both_sides(self):
        self.link(self.mine, self.kids)
        self.assertIsNone(self.link(self.mine, None)["linked"])
        self.assertIsNone(self.linked("kids", self.kids))

    def test_rejections(self):
        with self.assertRaises(ValueError):
            self.link(self.kids, self.kids2, "kids")
        self.add_series("Netflix", [-15.0] * 6)
        netflix = self.confirm("Netflix", "ignored")
        with self.assertRaises(ValueError):
            self.link(netflix, self.kids)
        with self.assertRaises(ValueError):
            self.link(self.mine, netflix)
        with self.assertRaises(LookupError):
            core.link_recurring(self.database, "personal", self.mine["id"], 9999, TODAY)
        with self.assertRaises(LookupError):
            core.link_recurring(self.database, "kids", self.mine["id"], self.kids["id"], TODAY)

    def test_delete_clears_the_counterparts_link(self):
        self.link(self.mine, self.kids)
        core.delete_recurring(self.database, "personal", self.mine["id"])
        self.assertIsNone(self.linked("kids", self.kids))

    def test_ignoring_an_item_drops_its_link(self):
        self.link(self.mine, self.kids)
        core.update_recurring(self.database, "personal", self.mine["id"], {"status": "ignored"}, TODAY)
        self.assertIsNone(self.linked("kids", self.kids))

    def test_linkable_lists_only_other_entities_confirmed_items(self):
        self.add_series("Netflix", [-15.0] * 6, account=KIDS, account_type="kids")
        self.confirm("Netflix", "ignored", account_type="kids")
        self.add_series("Spotify", [-10.0] * 6, account=KIDS, account_type="kids")  # stays a candidate
        found = core.linkable_recurring(self.database, "personal", TODAY)
        self.assertEqual(sorted((i["account_type"], i["merchant"]) for i in found), [
            ("business", "Tools"), ("kids", "Top-up"), ("kids", "Top-up 2"),
        ])
        self.assertEqual(set(found[0]), {"id", "account_type", "merchant", "kind", "amount", "cadence"})
        self.assertEqual(
            sorted(i["merchant"] for i in core.linkable_recurring(self.database, "kids", TODAY)),
            ["Pocket money", "Tools"],
        )


class CategoryTests(RecurringFixture):
    def test_merge_moves_items_to_the_target(self):
        self.add_series("Verzekeraar", [-150.0] * 6)
        item = self.confirm("Verzekeraar", category_id=self.insurance)
        core.merge_categories(self.database, "personal", self.insurance, self.other)
        found = self.listing()["items"][0]
        self.assertEqual((found["id"], found["category_id"], found["category"]), (item["id"], self.other, "Other"))

    def test_removing_a_category_clears_it_on_items(self):
        self.add_series("Verzekeraar", [-150.0] * 6)
        self.confirm("Verzekeraar", category_id=self.insurance)
        core.remove_category(self.database, self.insurance)
        found = self.listing()["items"][0]
        self.assertEqual((found["category_id"], found["category"]), (None, None))


class EndpointTests(RecurringFixture):
    def setUp(self):
        super().setUp()
        self.add_series("Verzekeraar", [-150.0] * 8)
        self.add_series("Top-up", [50.0] * 6, account=KIDS, account_type="kids")

    def test_list_confirm_patch_link_delete(self):
        listing = self.client.get("/api/recurring", params={"account_type": "personal"})
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(set(listing.json()), {"monthly", "items", "candidates", "ignored"})
        self.assertEqual(listing.json()["candidates"][0]["merchant"], "Verzekeraar")
        body = {"account_type": "personal", "merchant": "Verzekeraar", "kind": "", "status": "confirmed"}
        created = self.client.post("/api/recurring", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()
        self.assertEqual(item["status"], "confirmed")
        self.assertNotIn("_cents", item)
        self.assertNotIn("_detected", item)
        self.assertEqual(self.client.post("/api/recurring", json=body).status_code, 400)
        missing = self.client.post("/api/recurring", json={**body, "merchant": "Nobody"})
        self.assertEqual(missing.status_code, 404)
        patched = self.client.patch(
            f"/api/recurring/{item['id']}",
            json={
                "account_type": "personal", "note": "hello", "end_date": None, "category_id": self.insurance,
                "amounts": [{"effective_date": item["start_date"], "amount": "-171,50"}],
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual((patched.json()["note"], patched.json()["amount"]), ("hello", "-171.50"))
        self.assertEqual(patched.json()["category"], "Insurance")
        cleared = self.client.patch(f"/api/recurring/{item['id']}", json={"account_type": "personal", "category_id": None})
        self.assertIsNone(cleared.json()["category_id"])
        self.assertEqual(cleared.json()["note"], "hello")
        bad = self.client.patch(f"/api/recurring/{item['id']}", json={"account_type": "personal", "amounts": []})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(
            self.client.patch("/api/recurring/999", json={"account_type": "personal", "note": "x"}).status_code, 404
        )
        kids = self.client.post(
            "/api/recurring", json={"account_type": "kids", "merchant": "Top-up", "kind": "", "status": "confirmed"}
        ).json()
        linkable = self.client.get("/api/recurring/linkable", params={"account_type": "personal"})
        self.assertEqual([i["id"] for i in linkable.json()], [kids["id"]])
        linked = self.client.put(
            f"/api/recurring/{item['id']}/link", json={"account_type": "personal", "linked_item_id": kids["id"]}
        )
        self.assertEqual(linked.json()["linked"]["merchant"], "Top-up")
        same = self.client.put(
            f"/api/recurring/{kids['id']}/link", json={"account_type": "kids", "linked_item_id": kids["id"]}
        )
        self.assertEqual(same.status_code, 400)
        unlinked = self.client.put(
            f"/api/recurring/{item['id']}/link", json={"account_type": "personal", "linked_item_id": None}
        )
        self.assertIsNone(unlinked.json()["linked"])
        wrong = self.client.delete(f"/api/recurring/{item['id']}", params={"account_type": "kids"})
        self.assertEqual(wrong.status_code, 404)
        deleted = self.client.delete(f"/api/recurring/{item['id']}", params={"account_type": "personal"})
        self.assertEqual((deleted.status_code, deleted.json()), (200, {"status": "deleted"}))
        again = self.client.get("/api/recurring", params={"account_type": "personal"}).json()
        self.assertEqual([c["merchant"] for c in again["candidates"]], ["Verzekeraar"])

    def test_invalid_account_type_is_rejected(self):
        self.assertEqual(self.client.get("/api/recurring", params={"account_type": "x"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()


class ManualTests(RecurringFixture):
    def add_manual(self, name="Kids top-up", **kwargs):
        values = {"cadence": "monthly", "amount": "-400", "start_date": (TODAY - timedelta(days=40)).isoformat()}
        values.update(kwargs)
        return core.add_recurring(
            self.database, "personal", name, values.pop("cadence"), values.pop("amount"), values.pop("start_date"),
            today=TODAY, **values,
        )

    def test_a_payment_added_by_hand_is_confirmed_and_counted(self):
        start = TODAY - timedelta(days=40)
        item = self.add_manual("  Kids   top-up ", category_id=self.other, note="Revolut")
        self.assertEqual((item["status"], item["merchant"], item["kind"]), ("confirmed", "Kids top-up", ""))
        self.assertEqual((item["amount"], item["day_of_month"], item["category"], item["note"]), ("-400.00", start.day, "Other", "Revolut"))
        self.assertEqual(item["amounts"], [{"effective_date": start.isoformat(), "amount": "-400.00"}])
        self.assertIsNone(item["last_date"])
        self.assertEqual(item["flags"], [])
        listing = self.listing()
        self.assertEqual([i["merchant"] for i in listing["items"]], ["Kids top-up"])
        self.assertEqual(listing["monthly"]["expense"], "-400.00")

    def test_without_payments_the_next_date_is_the_first_due_day_from_today(self):
        item = self.add_manual(start_date=(TODAY - timedelta(days=70)).isoformat(), day_of_month=1)
        following = date.fromisoformat(item["next_date"])
        self.assertGreaterEqual(following, TODAY)
        self.assertEqual(following.day, 1)
        self.assertLessEqual((following - TODAY).days, 31)
        future = self.add_manual("Holiday fund", cadence="quarterly", start_date=(TODAY + timedelta(days=10)).isoformat())
        self.assertEqual(future["next_date"], (TODAY + timedelta(days=10)).isoformat())

    def test_adding_validates_its_values(self):
        self.add_manual()
        with self.assertRaises(ValueError):
            self.add_manual("kids TOP-UP")  # the same name, ignoring case
        for bad in (
            {"amount": "0"}, {"amount": "lots"}, {"cadence": "weekly"}, {"start_date": "soon"},
            {"end_date": (TODAY - timedelta(days=50)).isoformat()}, {"day_of_month": 32},
            {"category_id": self.business_category},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.add_manual("Other payment", **bad)
        with self.assertRaises(ValueError):
            self.add_manual("   ")

    def test_the_endpoint_adds_a_payment(self):
        response = self.client.post("/api/recurring/manual", json={
            "account_type": "personal", "merchant": "Kids top-up", "cadence": "monthly",
            "amount": "-400,00", "start_date": TODAY.isoformat(),
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["amount"], "-400.00")
        again = self.client.post("/api/recurring/manual", json={
            "account_type": "personal", "merchant": "Kids top-up", "cadence": "monthly",
            "amount": "-400", "start_date": TODAY.isoformat(),
        })
        self.assertEqual(again.status_code, 400)


class ManualGroupTests(RecurringFixture):
    def test_a_payment_added_for_a_group_follows_its_transactions(self):
        # Too new and irregular to be suggested: a partial first payment, then two monthly ones.
        first = TODAY - timedelta(days=80)
        regular = [_add_months(TODAY.replace(day=7), -back) for back in (2, 1)]
        regular = [day if day <= TODAY else _add_months(day, -1) for day in regular]
        self.add("Lease Co", [(first, -151.80)] + [(day, -455.50) for day in regular])
        self.assertIsNone(self.candidate("Lease Co"))
        groups = core.recurring_groups(self.database, "personal", today=TODAY)
        lease = next(group for group in groups if group["merchant"] == "Lease Co")
        self.assertEqual((lease["kind"], lease["count"], lease["first_date"], lease["last_amount"]), ("", 3, first.isoformat(), "-455.50"))
        end = _add_months(TODAY, 40).isoformat()
        item = core.add_recurring(
            self.database, "personal", "Lease Co", "monthly", "-455,50", first.isoformat(),
            day_of_month=7, end_date=end, kind="", today=TODAY,
        )
        self.assertEqual((item["last_date"], item["next_date"]), (regular[-1].isoformat(), _add_months(regular[-1], 1).isoformat()))
        self.assertEqual(item["end_date"], end)
        # The group now has its payment, so it is offered no more.
        self.assertNotIn("Lease Co", [g["merchant"] for g in core.recurring_groups(self.database, "personal", today=TODAY)])

    def test_a_kind_must_name_an_existing_group(self):
        self.add("Lease Co", [(TODAY - timedelta(days=10), -455.50)])
        with self.assertRaises(LookupError):
            core.add_recurring(self.database, "personal", "Lease Co", "monthly", -455.50, TODAY.isoformat(), kind="Lease payment", today=TODAY)

    def test_the_groups_endpoint_and_kind_on_the_manual_endpoint(self):
        self.add("Lease Co", [(TODAY - timedelta(days=10), -455.50)])
        listed = self.client.get("/api/recurring/groups", params={"account_type": "personal"})
        self.assertEqual(listed.status_code, 200)
        self.assertIn("Lease Co", [g["merchant"] for g in listed.json()])
        missing = self.client.post("/api/recurring/manual", json={
            "account_type": "personal", "merchant": "Lease Co", "kind": "Card", "cadence": "monthly",
            "amount": "-455,50", "start_date": TODAY.isoformat(),
        })
        self.assertEqual(missing.status_code, 404)
