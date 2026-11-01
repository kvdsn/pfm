from datetime import date
from unittest.mock import patch

from backend.pfm import core
from backend.pfm.core.forecast_history import take_snapshot
from tests.test_forecast import BUSINESS, PERSONAL, ForecastFixture


def months_back(count, year=2026, month=9):
    """The first day of each of `count` months ending with the given one, oldest first."""
    found = []
    for back in range(count - 1, -1, -1):
        index = year * 12 + month - 1 - back
        found.append(date(index // 12, index % 12 + 1, 1))
    return found


class HistoryFixture(ForecastFixture):
    def set_imports(self, imported_at):
        with core.connect(self.database) as db:
            db.execute("UPDATE imports SET imported_at = ?", (imported_at,))

    def rows(self, table, where="1 = 1", params=()):
        with core.connect(self.database) as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE {where}", params)]

    def snap(self, month, entries, closing=0, low=None, high=None, entity="personal"):
        """Store a snapshot of `month` by hand; entries are (category, cents, source, day, recurring id)."""
        with core.connect(self.database) as db:
            snapshot_id = db.execute(
                "INSERT INTO forecast_snapshots(month, taken_at) VALUES (?, ?)", (month, f"{month}-01T09:00:00")
            ).lastrowid
            db.execute(
                "INSERT INTO forecast_snapshot_balances VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_id, entity, month, closing, closing if low is None else low, closing if high is None else high),
            )
            for category, cents, source, day, recurring_id in entries:
                category_id = self.cat[entity, category]
                db.execute(
                    "INSERT INTO forecast_snapshot_lines(snapshot_id, account_type, month, category_id, category_name, "
                    "is_income, amount_cents, source, day, recurring_id) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                    (snapshot_id, entity, month, category_id, category, cents, source, day, recurring_id),
                )

    def compare(self, today=date(2026, 12, 15)):
        return core.forecast_vs_actual(self.database, "personal", today)

    def category_row(self, result, month, name):
        month_row = next(m for m in result["months"] if m["month"] == month)
        return next(c for c in month_row["categories"] if c["category"] == name)


class SnapshotTests(HistoryFixture):
    def setUp(self):
        super().setUp()
        self.cat["personal", "Shopping"] = core.add_category(self.database, "personal", "Shopping")["id"]
        for when in months_back(8, 2026, 8):
            self.add(PERSONAL, "personal", "Market", [(when.replace(day=5), -200.0)], "Groceries")
        self.add(PERSONAL, "personal", "Landlord", [(date(2026, 8, 5), -500.0)], "Rent")
        core.add_recurring(self.database, "personal", "Landlord", "monthly", -500, "2026-08-05",
                           category_id=self.cat["personal", "Rent"], today=date(2026, 9, 1))

    def test_one_snapshot_per_month_and_never_replaced(self):
        self.assertTrue(take_snapshot(self.database, date(2026, 9, 1)))
        before = self.rows("forecast_snapshot_lines")
        taken_at = self.rows("forecast_snapshots")[0]["taken_at"]
        self.add(PERSONAL, "personal", "Market", [(date(2026, 9, 5), -999.0)], "Groceries")
        self.assertFalse(core.ensure_snapshot(self.database, date(2026, 9, 20)))
        self.assertFalse(take_snapshot(self.database, date(2026, 9, 21)))
        self.assertEqual(self.rows("forecast_snapshot_lines"), before)
        self.assertEqual(self.rows("forecast_snapshots")[0]["taken_at"], taken_at)
        self.assertTrue(core.ensure_snapshot(self.database, date(2026, 10, 1)))
        self.assertEqual([r["month"] for r in self.rows("forecast_snapshots")], ["2026-09", "2026-10"])

    def test_covers_personal_and_business_from_the_snapshot_month_to_the_end_of_the_forecast(self):
        take_snapshot(self.database, date(2026, 9, 1))
        balances = self.rows("forecast_snapshot_balances")
        self.assertEqual({b["account_type"] for b in balances}, {"personal", "business"})
        self.assertEqual({b["month"] for b in balances if b["account_type"] == "personal"},
                         {f"2026-{m:02d}" for m in range(9, 13)} | {f"2027-{m:02d}" for m in range(1, 13)})
        self.assertFalse(self.rows("forecast_snapshot_lines", "account_type = 'kids'"))
        recurring = self.rows("forecast_snapshot_lines", "source = 'recurring'")
        self.assertEqual({r["recurring_id"] for r in recurring}, {core.list_recurring(self.database, "personal")["items"][0]["id"]})

    def test_the_snapshot_month_is_booked_plus_projected(self):
        self.add(PERSONAL, "personal", "Market", [(date(2026, 9, 3), -80.0)], "Groceries")
        today = date(2026, 9, 10)
        take_snapshot(self.database, today)
        booked = self.rows("forecast_snapshot_lines", "source = 'booked' AND account_type = 'personal'")
        self.assertEqual([(b["category_name"], b["amount_cents"], b["day"]) for b in booked],
                         [("Groceries", -8000, "2026-09-03")])
        september = core.forecast(self.database, today)["personal"]["months"][0]
        self.assertEqual(september["expense"], "700.00")  # 200 groceries (80 booked) + 500 rent
        self.set_imports("2026-10-02T10:00:00+00:00")
        result = core.forecast_vs_actual(self.database, "personal", date(2026, 10, 3))
        month = result["months"][0]
        self.assertEqual((month["month"], month["snapshot_date"]), ("2026-09", "2026-09-10"))
        self.assertEqual(month["expense"]["forecast"], september["expense"])
        groceries = self.category_row(result, "2026-09", "Groceries")
        self.assertEqual((groceries["forecast"], groceries["source"]), ("200.00", "baseline"))

    def test_actuals_match_the_yearly_report_and_balance_history(self):
        take_snapshot(self.database, date(2026, 9, 1))
        self.add(PERSONAL, "personal", "Market", [(date(2026, 9, 5), -260.0)], "Groceries")
        self.add(PERSONAL, "personal", "Landlord", [(date(2026, 9, 5), -550.0)], "Rent")
        self.add(PERSONAL, "personal", "Other", [(date(2026, 9, 7), 40.0)])
        self.set_imports("2026-10-02T10:00:00+00:00")
        today = date(2026, 10, 25)
        result = core.forecast_vs_actual(self.database, "personal", today)
        month = result["months"][0]
        summary = next(s for s in core.report(self.database, "personal", today)["summaries"] if s["month"] == "2026-09")
        self.assertEqual(month["status"], "complete")
        self.assertEqual(month["expense"]["actual"], summary["expenses"])
        self.assertEqual(month["income"]["actual"], summary["income"])
        for row in summary["categories"]:
            shown = self.category_row(result, "2026-09", row["name"])
            self.assertEqual((shown["flow"], shown["actual"]), (row["flow_type"], row["amount"]))
            self.assertEqual(shown["category_id"], row["category_id"])
        history = core.balance_history(self.database, "personal", None, "2026", today)["points"]
        self.assertEqual(month["balance"]["actual"], next(p for p in history if p["date"] == "2026-09")["balance"])
        self.assertEqual(self.category_row(result, "2026-09", "Rent")["difference"], "50.00")
        self.assertEqual(result["summary"]["months_tracked"], 1)

    def test_a_late_import_fills_in_a_month_afterwards(self):
        take_snapshot(self.database, date(2026, 9, 1))
        self.set_imports("2026-09-20T10:00:00+00:00")
        result = core.forecast_vs_actual(self.database, "personal", date(2026, 10, 25))
        month = result["months"][0]
        self.assertEqual(month["status"], "waiting")
        self.assertFalse(any(c["miss"] for c in month["categories"]) or month["balance"]["miss"])
        self.assertEqual((result["summary"], result["misses"]), ({"months_tracked": 0, "typical_balance_miss": None}, []))
        self.set_imports("2026-10-02T10:00:00+00:00")
        self.assertEqual(core.forecast_vs_actual(self.database, "personal", date(2026, 10, 25))["months"][0]["status"], "complete")

    def test_a_card_account_does_not_hold_a_month_back(self):
        take_snapshot(self.database, date(2026, 9, 1))
        self.set_imports("2026-10-02T10:00:00+00:00")
        with core.connect(self.database) as db:
            account = db.execute("INSERT INTO accounts(iban, account_type, created_at, kind) VALUES ('BE00000000000000009', 'personal', '2026-01-01', 'card')").lastrowid
            db.execute("INSERT INTO imports(account_id, filename, imported_at, row_count, inserted_count, duplicate_count) VALUES (?, 'card.csv', '2026-09-15T10:00:00+00:00', 1, 1, 0)", (account,)).lastrowid
        self.assertEqual(core.forecast_vs_actual(self.database, "personal", date(2026, 10, 25))["months"][0]["status"], "complete")

    def test_balance_miss_follows_the_low_to_high_range(self):
        self.snap("2026-09", [], closing=100000, low=90000, high=110000)
        self.set_imports("2026-10-02T10:00:00+00:00")
        today = date(2026, 10, 25)
        balance = core.forecast_vs_actual(self.database, "personal", today)["months"][0]["balance"]
        actual = int(float(balance["actual"]) * 100)
        self.assertTrue(balance["miss"])  # the real balance is far from 1000.00
        with core.connect(self.database) as db:
            db.execute("UPDATE forecast_snapshot_balances SET closing_cents = ?, low_cents = ?, high_cents = ?",
                       (actual + 500, actual - 1000, actual + 1000))
        result = core.forecast_vs_actual(self.database, "personal", today)
        self.assertFalse(result["months"][0]["balance"]["miss"])
        self.assertEqual(result["months"][0]["balance"]["difference"], "-5.00")
        self.assertEqual(result["summary"]["typical_balance_miss"], "5.00")
        with core.connect(self.database) as db:
            db.execute("UPDATE forecast_snapshot_balances SET low_cents = ?, high_cents = ?", (actual + 100, actual + 1000))
        self.assertTrue(core.forecast_vs_actual(self.database, "personal", today)["months"][0]["balance"]["miss"])

    def test_pace_prorates_baselines_and_counts_dated_lines_after_their_date(self):
        self.snap("2026-10", [
            ("Groceries", -20000, "baseline", None, None),
            ("Rent", -50000, "recurring", "2026-10-05", None),
            ("Streaming", -30000, "planned", "2026-10-20", None),
            ("Shopping", -4000, "booked", "2026-10-02", None),
        ])
        self.add(PERSONAL, "personal", "Market", [(date(2026, 10, 4), -150.0)], "Groceries")
        self.add(PERSONAL, "personal", "Landlord", [(date(2026, 10, 5), -500.0)], "Rent")
        self.add(PERSONAL, "personal", "Shop", [(date(2026, 10, 2), -40.0)], "Shopping")
        pace = self.compare(date(2026, 10, 10))["pace"]
        self.assertEqual((pace["month"], pace["through"], pace["elapsed"]), ("2026-10", "2026-10-10", 0.323))
        rows = {c["category"]: c for c in pace["categories"]}
        self.assertEqual((rows["Groceries"]["expected"], rows["Groceries"]["actual"], rows["Groceries"]["ahead"]),
                         ("64.52", "150.00", True))
        self.assertEqual((rows["Rent"]["expected"], rows["Rent"]["ahead"]), ("500.00", False))
        self.assertEqual(rows["Shopping"]["expected"], "40.00")
        self.assertNotIn("Streaming", rows)  # planned for the 20th
        self.assertEqual(pace["expense"], {"expected": "604.52", "actual": "690.00", "difference": "85.48"})
        self.assertEqual(pace["income"]["expected"], "0.00")
        current = self.compare(date(2026, 10, 10))["months"][0]
        self.assertEqual(current["status"], "current")
        self.assertFalse(any(c["miss"] for c in current["categories"]))

    def test_no_pace_without_a_snapshot_this_month(self):
        self.snap("2026-09", [])
        self.assertIsNone(self.compare(date(2026, 10, 10))["pace"])
        self.assertIsNone(core.forecast_vs_actual(self.database, "business", date(2026, 10, 10))["pace"])

    def test_baseline_categories_are_judged_by_their_own_range(self):
        # Twelve steady months of groceries leave a range of zero: any difference is a miss.
        for when in months_back(6, 2026, 7):
            self.add(PERSONAL, "personal", "Market", [(when.replace(day=9), -200.0)], "Groceries")
        self.snap("2026-09", [("Groceries", -20000, "baseline", None, None), ("Rent", -50000, "recurring", "2026-09-05", None)])
        self.add(PERSONAL, "personal", "Market", [(date(2026, 9, 12), -3.0)], "Groceries")
        self.set_imports("2026-10-02T10:00:00+00:00")
        rows = {c["category"]: c for c in core.forecast_vs_actual(self.database, "personal", date(2026, 10, 25))["months"][0]["categories"]}
        self.assertEqual(rows["Groceries"]["difference"], "-197.00")
        self.assertTrue(rows["Groceries"]["miss"])


class HintTests(HistoryFixture):
    def setUp(self):
        super().setUp()
        self.add(PERSONAL, "personal", "Anchor", [(date(2026, 8, 1), -1.0)])
        self.set_imports("2026-12-14T10:00:00+00:00")

    def misses(self, month="2026-09"):
        return [m for m in self.compare()["misses"] if m["month"] == month]

    def test_amount_changed_names_the_recurring_item(self):
        item = core.add_recurring(self.database, "personal", "Gym", "monthly", -25, "2026-07-10",
                                  category_id=self.cat["personal", "Streaming"], today=date(2026, 9, 1))
        self.add(PERSONAL, "personal", "Gym", [(date(2026, 9, 10), -40.0)], "Streaming")
        self.snap("2026-09", [("Streaming", -2500, "recurring", "2026-09-10", item["id"])])
        miss = self.misses()[0]
        self.assertEqual((miss["category"], miss["difference"], miss["hint"], miss["recurring_id"]),
                         ("Streaming", "15.00", "amount_changed", item["id"]))

    def test_one_off_is_a_single_large_transaction(self):
        self.add(PERSONAL, "personal", "Shop", [(date(2026, 9, 3), -400.0), (date(2026, 9, 4), -50.0)], "Groceries")
        self.snap("2026-09", [("Groceries", -5000, "baseline", None, None)])
        self.assertEqual([(m["difference"], m["hint"]) for m in self.misses()], [("400.00", "one_off")])

    def test_a_single_transaction_below_the_forecast_factor_is_no_one_off(self):
        self.add(PERSONAL, "personal", "Shop", [(date(2026, 9, 3), -75.0), (date(2026, 9, 4), -75.0)], "Groceries")
        self.snap("2026-09", [("Groceries", -10000, "baseline", None, None)])
        self.assertEqual([(m["difference"], m["hint"]) for m in self.misses()], [("50.00", None)])

    def test_make_recurring_when_a_similar_amount_came_in_most_previous_months(self):
        for month in (6, 7, 8, 9):
            self.add(PERSONAL, "personal", f"Club {month}",
                     [(date(2026, month, day), -20.0) for day in (3, 11, 19)], "Streaming")
        self.snap("2026-09", [])
        self.assertEqual([(m["category"], m["forecast"], m["hint"]) for m in self.misses()],
                         [("Streaming", "0.00", "make_recurring")])

    def test_a_single_unforecast_monthly_payment_is_make_recurring_not_one_off(self):
        for month in (6, 7, 8, 9):
            self.add(PERSONAL, "personal", f"Club {month}", [(date(2026, month, 5), -30.0)], "Streaming")
        self.snap("2026-09", [])
        self.assertEqual([(m["category"], m["hint"]) for m in self.misses()], [("Streaming", "make_recurring")])

    def test_make_recurring_needs_two_similar_months(self):
        for month, amount in ((6, 5.0), (7, 90.0), (8, 20.0), (9, 20.0)):
            self.add(PERSONAL, "personal", f"Club {month}",
                     [(date(2026, month, day), -amount) for day in (3, 11, 19)], "Streaming")
        self.snap("2026-09", [])
        self.assertIsNone(self.misses()[0]["hint"])

    def test_baseline_low_and_high_need_three_months_in_a_row(self):
        for month in (9, 10, 11):
            self.add(PERSONAL, "personal", f"Shop {month}",
                     [(date(2026, month, 3), -75.0), (date(2026, month, 4), -75.0)], "Groceries")
            self.add(PERSONAL, "personal", f"Bill {month}",
                     [(date(2026, month, 3), -25.0), (date(2026, month, 4), -25.0)], "Rent")
            self.snap(f"2026-{month:02d}", [("Groceries", -10000, "baseline", None, None), ("Rent", -10000, "baseline", None, None)])
        result = self.compare()
        by_month = {(m["month"], m["category"]): m["hint"] for m in result["misses"]}
        self.assertEqual(by_month["2026-09", "Groceries"], None)
        self.assertEqual(by_month["2026-11", "Groceries"], "baseline_low")
        self.assertEqual(by_month["2026-11", "Rent"], "baseline_high")

    def test_misses_are_largest_first_and_limited_to_twenty(self):
        self.add(PERSONAL, "personal", "Shop", [(date(2026, 9, 3), -100.0), (date(2026, 9, 4), -200.0)], "Groceries")
        self.add(PERSONAL, "personal", "Landlord", [(date(2026, 9, 3), -30.0), (date(2026, 9, 4), -40.0)], "Rent")
        self.snap("2026-09", [])
        self.assertEqual([m["category"] for m in self.compare()["misses"]], ["Groceries", "Rent"])


class CategoryChangeTests(HistoryFixture):
    def test_merge_and_remove_repoint_snapshot_lines(self):
        self.snap("2026-09", [("Groceries", -100, "baseline", None, None), ("Rent", -200, "baseline", None, None)])
        groceries, rent = self.cat["personal", "Groceries"], self.cat["personal", "Rent"]
        target = self.cat["personal", "Streaming"]
        core.merge_categories(self.database, "personal", groceries, target)
        self.assertEqual({r["category_name"] for r in self.rows("forecast_snapshot_lines", "category_id = ?", (target,))}, {"Streaming"})
        core.remove_category(self.database, rent)
        with core.connect(self.database) as db:
            uncategorized = core.get_category_id(db, "personal", core.UNCATEGORIZED)
        self.assertEqual(sorted(r["category_id"] for r in self.rows("forecast_snapshot_lines")), sorted([target, uncategorized]))


class EndpointTests(HistoryFixture):
    def test_the_forecast_request_takes_one_snapshot_and_still_works_with_one(self):
        first = self.client.get("/api/forecast", params={"account_type": "personal"})
        second = self.client.get("/api/forecast", params={"account_type": "business"})
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(len(self.rows("forecast_snapshots")), 1)
        self.assertEqual(first.json()["months"][0]["lines"], core.forecast(self.database)["personal"]["months"][0]["lines"])

    def test_a_failing_snapshot_never_fails_the_forecast(self):
        with patch("backend.pfm.core.forecast_history.take_snapshot", side_effect=RuntimeError("boom")), \
                self.assertLogs("pfm", "ERROR"):
            response = self.client.get("/api/forecast", params={"account_type": "personal"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.rows("forecast_snapshots"))

    def test_vs_actual_endpoint_and_kids(self):
        self.client.get("/api/forecast", params={"account_type": "business"})
        body = self.client.get("/api/forecast/vs-actual", params={"account_type": "business"}).json()
        self.assertEqual(body["account_type"], "business")
        self.assertEqual(len(body["months"]), 1)
        self.assertIsNotNone(body["pace"])
        self.assertEqual(self.client.get("/api/forecast/vs-actual", params={"account_type": "kids"}).status_code, 400)
        with self.assertRaises(ValueError):
            core.forecast_vs_actual(self.database, "kids")

    def test_business_is_compared_too(self):
        self.add(BUSINESS, "business", "Supplier", [(date(2026, 8, 5), -100.0)], "Tools")
        take_snapshot(self.database, date(2026, 9, 1))
        result = core.forecast_vs_actual(self.database, "business", date(2026, 9, 2))
        self.assertEqual(result["months"][0]["status"], "current")
