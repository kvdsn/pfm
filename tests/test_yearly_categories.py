from datetime import date

from backend.pfm import core
from tests.test_forecast import PERSONAL, TODAY, ForecastFixture


class YearlyCategoryTests(ForecastFixture):
    """A lumpy category, such as holidays: big payments in a few months, nothing in most."""

    def setUp(self):
        super().setUp()
        self.cat["personal", "Holidays"] = core.add_category(self.database, "personal", "Holidays")["id"]
        payments = [
            (date(2026, 8, 3), -40.0), (date(2026, 7, 12), -1200.0), (date(2026, 7, 2), -300.0),
            (date(2026, 2, 10), -900.0), (date(2025, 12, 20), 100.0), (date(2025, 10, 5), -500.0),
        ]
        self.add(PERSONAL, "personal", "Trips", payments, "Holidays")
        # Older history, so the window starts after the entity's first month.
        self.add(PERSONAL, "personal", "Shop", [(date(2025, 5, 4), -10.0)], "Groceries")

    def yearly(self, ids):
        return core.set_settings(self.database, {"yearly_categories": {"personal": ids}})

    def holidays(self, month):
        return sum(float(line["amount"]) for line in self.lines("personal", month, "baseline")
                   if line["category"] == "Holidays")

    def test_a_lumpy_category_gets_no_baseline_from_the_median(self):
        self.assertEqual(self.holidays("2027-07"), 0)

    def test_a_yearly_category_repeats_each_month_of_the_last_twelve(self):
        self.yearly([self.cat["personal", "Holidays"]])
        self.assertEqual(self.holidays("2027-07"), -1500)
        self.assertEqual(self.holidays("2027-02"), -900)
        self.assertEqual(self.holidays("2027-08"), -40)
        self.assertEqual(self.holidays("2027-03"), 0)
        # A refund doesn't turn into income; the window is October 2025 to September 2026.
        self.assertEqual(self.holidays("2026-12"), 0)
        self.assertEqual(self.holidays("2027-10"), -500)
        self.assertEqual(self.holidays("2026-10"), -500)  # nothing booked yet this month
        line = next(line for line in self.lines("personal", "2027-07", "baseline") if line["category"] == "Holidays")
        self.assertEqual(line["detail"], "Same month a year earlier (yearly category)")

    def test_yearly_categories_are_a_setting_per_entity(self):
        settings = self.yearly([self.cat["personal", "Holidays"]])
        self.assertEqual(settings["yearly_categories"]["personal"], [self.cat["personal", "Holidays"]])
        self.assertEqual(settings["yearly_categories"]["business"], [])
        with self.assertRaises(ValueError):
            self.yearly([self.cat["business", "Tools"]])

    def test_removing_the_category_drops_it_from_the_setting(self):
        self.yearly([self.cat["personal", "Holidays"]])
        core.remove_category(self.database, self.cat["personal", "Holidays"])
        self.assertEqual(core.get_settings(self.database)["yearly_categories"]["personal"], [])

    def test_the_backtest_and_comparison_still_run(self):
        self.yearly([self.cat["personal", "Holidays"]])
        core.backtest(self.database, TODAY)
        core.forecast_vs_actual(self.database, "personal", TODAY)
