from datetime import date

from backend.pfm import core
from tests.test_forecast import BUSINESS, PERSONAL, TODAY, ForecastFixture


class BookingFixture(ForecastFixture):
    def plan(self, when, amount, category="Tools", note="", entity="business"):
        cid = self.cat[entity, category] if category else None
        return core.add_planned(self.database, entity, when, amount, cid, note)

    def tx(self, merchant):
        with core.connect(self.database) as db:
            return db.execute("SELECT id FROM transactions WHERE merchant = ?", (merchant,)).fetchone()["id"]

    def item(self, item_id, entity="business"):
        return next(i for i in core.list_planned(self.database, entity, TODAY) if i["id"] == item_id)

    def suggested(self, item_id, entity="business"):
        found = self.item(item_id, entity)["suggestion"]
        return found and found["transaction_id"]

    def planned_lines(self):
        return [(l["date"], l["amount"]) for m in self.run_forecast("business")["months"] for l in m["lines"]
                if l["source"] == "planned"]


class CandidateTests(BookingFixture):
    def test_window_tolerance_and_sign(self):
        item = self.plan("2026-11-10", -100)["id"]
        self.add(BUSINESS, "business", "Early", [(date(2026, 10, 11), -100.0)])  # 30 days before: inside
        self.assertEqual(self.suggested(item), self.tx("Early"))
        self.add(BUSINESS, "business", "Too early", [(date(2026, 10, 10), -100.0)])  # 31 days before
        self.add(BUSINESS, "business", "Too late", [(date(2026, 12, 26), -100.0)])  # 46 days after
        self.add(BUSINESS, "business", "Refund", [(date(2026, 11, 10), 100.0)])  # wrong sign
        self.add(BUSINESS, "business", "Far off", [(date(2026, 11, 10), -111.0)])  # beyond 10%
        self.assertEqual(self.suggested(item), self.tx("Early"))
        self.add(BUSINESS, "business", "Close", [(date(2026, 11, 10), -110.0)])  # exactly 10%
        self.assertEqual(self.suggested(item), self.tx("Early"))  # exact amount beats a near one
        found = self.item(item)["suggestion"]
        self.assertEqual((found["date"], found["amount"], found["merchant"]), ("2026-10-11", "-100.00", "Early"))

    def test_late_payment_within_45_days(self):
        item = self.plan("2026-09-20", -100)["id"]  # overdue
        self.add(BUSINESS, "business", "Late", [(date(2026, 10, 20), -100.0)])
        self.assertEqual(self.item(item)["status"], "overdue")
        self.assertEqual(self.suggested(item), self.tx("Late"))

    def test_category_rule_and_ranking(self):
        item = self.plan("2026-11-10", -100, "Tools")["id"]
        self.add(BUSINESS, "business", "Elsewhere", [(date(2026, 11, 10), -100.0)], "VAT")  # other category: no
        self.assertIsNone(self.suggested(item))
        self.add(BUSINESS, "business", "Unsorted", [(date(2026, 11, 10), -100.0)])  # Uncategorized: yes
        self.assertEqual(self.suggested(item), self.tx("Unsorted"))
        self.add(BUSINESS, "business", "Own category", [(date(2026, 11, 20), -105.0)], "Tools")  # own category first
        self.assertEqual(self.suggested(item), self.tx("Own category"))
        free = self.plan("2026-11-10", -100, None)["id"]  # no category: any category qualifies
        self.assertIsNotNone(self.suggested(free))

    def test_closest_amount_then_date(self):
        item = self.plan("2026-11-10", -100, None)["id"]
        self.add(BUSINESS, "business", "Near amount", [(date(2026, 11, 10), -104.0)])
        self.add(BUSINESS, "business", "Near date", [(date(2026, 11, 11), -102.0)])
        self.assertEqual(self.suggested(item), self.tx("Near date"))  # 2 off beats 4 off
        self.add(BUSINESS, "business", "Same amount later", [(date(2026, 11, 20), -102.0)])
        self.assertEqual(self.suggested(item), self.tx("Near date"))

    def test_dismissed_and_booked_transactions_are_not_suggested_again(self):
        first = self.plan("2026-11-10", -100, None)["id"]
        second = self.plan("2026-11-12", -100, None)["id"]
        self.add(BUSINESS, "business", "One", [(date(2026, 11, 11), -100.0)])
        self.add(BUSINESS, "business", "Two", [(date(2026, 11, 14), -100.0)])
        # Greedy in date order: the first item takes the nearest one, the second the other.
        self.assertEqual((self.suggested(first), self.suggested(second)), (self.tx("One"), self.tx("Two")))
        core.dismiss_match(self.database, "business", first, self.tx("One"))
        self.assertEqual(self.suggested(first), self.tx("Two"))
        self.assertEqual(self.suggested(second), self.tx("One"))
        core.dismiss_match(self.database, "business", first, self.tx("Two"))
        self.assertIsNone(self.suggested(first))
        core.book_planned(self.database, "business", second, self.tx("One"))
        self.assertIsNone(self.item(first)["suggestion"])  # One is booked, Two dismissed

    def test_other_entities_and_soft_deleted_rows_are_not_candidates(self):
        item = self.plan("2026-11-10", -100, None)["id"]
        self.add(PERSONAL, "personal", "Private", [(date(2026, 11, 10), -100.0)])
        self.add(BUSINESS, "business", "Gone", [(date(2026, 11, 10), -100.0)])
        with core.connect(self.database) as db:
            db.execute("UPDATE transactions SET deleted_at = '2026-11-11' WHERE merchant = 'Gone'")
        self.assertIsNone(self.suggested(item))

    def test_own_transfers_are_not_candidates(self):
        item = self.plan("2026-11-10", -100, None)["id"]
        self.add(BUSINESS, "business", "Spare", [(date(2026, 11, 10), -100.0)])
        self.add(PERSONAL, "personal", "Elsewhere", [(date(2026, 11, 1), 1.0)])
        with core.connect(self.database) as db:
            # Mark the payment as a transfer between two of the entity's own accounts.
            db.execute("INSERT INTO accounts(iban, account_type, bank, kind, created_at) VALUES ('BE00000000000000000009', 'business', 'KBC', 'bank', '2026-01-01')")
            db.execute("UPDATE transactions SET counterparty_iban = 'BE00000000000000000009' WHERE merchant = 'Spare'")
        self.assertIsNone(self.suggested(item))


class BookingTests(BookingFixture):
    def test_booking_unbooking_and_validation(self):
        item = self.plan("2026-11-10", -100, "Tools", "Laptop")["id"]
        other = self.plan("2026-11-11", -100, "Tools")["id"]
        self.add(BUSINESS, "business", "Shop", [(date(2026, 12, 1), -250.0)])  # outside tolerance: still bookable
        self.add(BUSINESS, "business", "Refund", [(date(2026, 11, 10), 100.0)])
        self.add(PERSONAL, "personal", "Private", [(date(2026, 11, 10), -100.0)])
        shop = self.tx("Shop")
        for wrong in (self.tx("Private"), 9999):
            with self.assertRaises(LookupError):
                core.book_planned(self.database, "business", item, wrong)
        with self.assertRaises(ValueError):
            core.book_planned(self.database, "business", item, self.tx("Refund"))
        booked = core.book_planned(self.database, "business", item, shop)
        self.assertEqual(booked["status"], "booked")
        self.assertEqual(booked["booked"], {"transaction_id": shop, "date": "2026-12-01", "amount": "-250.00", "merchant": "Shop"})
        self.assertIsNone(booked["suggestion"])
        with self.assertRaises(ValueError):
            core.book_planned(self.database, "business", other, shop)
        with self.assertRaises(LookupError):
            core.book_planned(self.database, "personal", item, shop)
        unbooked = core.unbook_planned(self.database, "business", item)
        self.assertEqual((self.item(item)["status"], unbooked["booked"]), ("upcoming", None))
        self.assertEqual(core.book_planned(self.database, "business", other, shop)["status"], "booked")

    def test_nothing_books_automatically(self):
        item = self.plan("2026-11-10", -100)["id"]
        self.add(BUSINESS, "business", "Exact", [(date(2026, 11, 10), -100.0)])
        self.assertEqual(self.item(item)["status"], "upcoming")
        self.assertEqual(self.planned_lines(), [("2026-11-10", "-100.00")])

    def test_sorting_puts_unbooked_first_then_booked_by_date(self):
        early = self.plan("2026-11-01", -10, None)["id"]
        late = self.plan("2027-02-01", -10, None)["id"]
        past = self.plan("2026-09-01", -10, None)["id"]
        self.add(BUSINESS, "business", "Paid", [(date(2026, 11, 1), -10.0)])
        core.book_planned(self.database, "business", early, self.tx("Paid"))
        listed = core.list_planned(self.database, "business", TODAY)
        self.assertEqual([(i["id"], i["status"]) for i in listed], [(past, "overdue"), (late, "upcoming"), (early, "booked")])

    def test_soft_deleted_transaction_unbooks_the_item(self):
        item = self.plan("2026-11-10", -100)["id"]
        self.add(BUSINESS, "business", "Paid", [(date(2026, 11, 10), -100.0)])
        core.book_planned(self.database, "business", item, self.tx("Paid"))
        self.assertEqual(self.planned_lines(), [])
        with core.connect(self.database) as db:
            db.execute("UPDATE transactions SET deleted_at = '2026-11-11' WHERE merchant = 'Paid'")
        self.assertEqual(self.item(item)["status"], "upcoming")
        self.assertEqual(self.planned_lines(), [("2026-11-10", "-100.00")])

    def test_undo_import_unbooks_and_clears_dismissals(self):
        item = self.plan("2026-11-10", -100)["id"]
        self.add(BUSINESS, "business", "Paid", [(date(2026, 11, 10), -100.0)])
        self.add(BUSINESS, "business", "Skipped", [(date(2026, 11, 11), -100.0)])
        spare = self.plan("2026-11-10", -100)["id"]
        core.dismiss_match(self.database, "business", spare, self.tx("Paid"))
        core.dismiss_match(self.database, "business", spare, self.tx("Skipped"))
        core.book_planned(self.database, "business", item, self.tx("Paid"))
        with core.connect(self.database) as db:
            import_id = db.execute("SELECT import_id FROM transactions WHERE merchant = 'Paid'").fetchone()[0]
        core.undo_import(self.database, import_id)
        with core.connect(self.database) as db:
            self.assertIsNone(db.execute("SELECT transaction_id FROM planned_items").fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM planned_match_dismissals").fetchone()[0], 1)  # the Skipped one's: Paid and Skipped came in separate imports
        self.assertEqual(self.item(item)["status"], "upcoming")

    def test_deleting_an_item_removes_its_dismissals(self):
        item = self.plan("2026-11-10", -100)["id"]
        self.add(BUSINESS, "business", "Skipped", [(date(2026, 11, 11), -100.0)])
        core.dismiss_match(self.database, "business", item, self.tx("Skipped"))
        core.delete_planned(self.database, "business", item)
        with core.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM planned_match_dismissals").fetchone()[0], 0)

    def test_category_merge_keeps_bookings(self):
        item = self.plan("2026-11-10", -100, "Tools")["id"]
        self.add(BUSINESS, "business", "Paid", [(date(2026, 11, 10), -100.0)])
        core.book_planned(self.database, "business", item, self.tx("Paid"))
        core.merge_categories(self.database, "business", self.cat["business", "Tools"], self.cat["business", "VAT"])
        listed = self.item(item)
        self.assertEqual((listed["status"], listed["category"]), ("booked", "VAT"))


class ForecastTests(BookingFixture):
    def test_booked_items_leave_the_forecast_and_overdue_ones_count_today(self):
        booked = self.plan("2026-11-10", -100, None, "Booked")["id"]
        self.plan("2026-10-10", -50, None, "Overdue")  # 15 days ago
        self.plan("2026-08-26", -25, None, "Exactly 60 days")
        self.plan("2026-08-25", -12, None, "61 days")
        self.add(BUSINESS, "business", "Paid", [(date(2026, 10, 20), -100.0)])
        core.book_planned(self.database, "business", booked, self.tx("Paid"))
        self.assertEqual(sorted(self.planned_lines()), [("2026-10-25", "-25.00"), ("2026-10-25", "-50.00")])

    def test_endpoints(self):
        item = self.plan("2026-11-10", -100)["id"]
        self.add(BUSINESS, "business", "Paid", [(date(2026, 11, 10), -100.0)])
        paid = self.tx("Paid")
        listed = self.client.get("/api/planned", params={"account_type": "business"}).json()
        self.assertEqual(listed[0]["suggestion"]["transaction_id"], paid)
        url = f"/api/planned/{item}/booking"
        body = {"account_type": "business", "transaction_id": paid}
        self.assertEqual(self.client.put(url, json={**body, "transaction_id": 999}).status_code, 404)
        self.assertEqual(self.client.put(url, json=body).json()["status"], "booked")
        self.assertNotEqual(self.client.delete(url, params={"account_type": "business"}).json()["status"], "booked")
        self.assertEqual(self.client.post(f"/api/planned/{item}/dismissals", json=body).status_code, 200)
        self.assertIsNone(self.client.get("/api/planned", params={"account_type": "business"}).json()[0]["suggestion"])
        self.assertEqual(self.client.delete(f"/api/planned/999/booking", params={"account_type": "business"}).status_code, 404)

    def test_corporate_tax_counts_a_booked_payment_once(self):
        tax = self.cat["business", "Corporate tax"]
        core.add_planned(self.database, "business", "2026-12-20", -700, tax, "Guess")
        self.add(BUSINESS, "business", "Tax office", [(date(2026, 10, 20), -700.0)], "Corporate tax")
        before = core.corporate_tax(self.database, TODAY)["years"][0]
        self.assertEqual((before["prepaid_total"], before["planned_total"]), ("700.00", "700.00"))
        item = core.list_planned(self.database, "business", TODAY)[0]["id"]
        core.book_planned(self.database, "business", item, self.tx("Tax office"))
        after = core.corporate_tax(self.database, TODAY)["years"][0]
        self.assertEqual((after["prepaid_total"], after["planned_total"]), ("700.00", "0.00"))
