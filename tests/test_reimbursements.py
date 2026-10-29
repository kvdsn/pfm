import unittest
from datetime import date

from backend.pfm import core
from tests.test_forecast import BUSINESS, PERSONAL, TODAY, ForecastFixture


class ReimbursementFixture(ForecastFixture):
    def setUp(self):
        super().setUp()
        # Both accounts must exist for the transfers' counterparties to be recognized.
        self.add(BUSINESS, "business", "Start", [(date(2026, 1, 5), 1000.0, 1000.0)])
        self.add(PERSONAL, "personal", "Start", [(date(2026, 1, 5), 100.0, 100.0)])
        self.energy = self.cat["personal", "Groceries"]
        self.tools = self.cat["business", "Tools"]

    def tx_id(self, merchant):
        with core.connect(self.database) as db:
            return db.execute("SELECT id FROM transactions WHERE merchant = ?", (merchant,)).fetchone()[0]

    def expense(self, amount=-1000.0, when=date(2026, 9, 10), merchant="Energy Co"):
        self.add(PERSONAL, "personal", merchant, [(when, amount)], "Groceries")
        return self.tx_id(merchant)

    def repayment(self, amount, when=date(2026, 10, 5), merchant="Repay", iban=BUSINESS, category=None):
        self.add(PERSONAL, "personal", merchant, [(when, amount)], category)
        with core.connect(self.database) as db:
            db.execute("UPDATE transactions SET counterparty_iban = ? WHERE merchant = ?", (iban, merchant))
        return self.tx_id(merchant)

    def post(self, **body):
        return self.client.post("/api/reimbursements", json=body)


class ClaimTests(ReimbursementFixture):
    def test_claim_from_a_share_of_an_expense_rounds_and_defaults(self):
        source = self.expense(-800.33)
        claim = self.post(source_transaction_id=source, share=60, business_category_id=self.tools).json()
        self.assertEqual(claim["amount"], "480.20")  # 80033 * 0.60 = 48019.80
        self.assertEqual((claim["date"], claim["description"], claim["share"]), ("2026-09-10", "Energy Co", 60))
        self.assertEqual((claim["personal_category_id"], claim["business_category"]), (self.energy, "Tools"))
        self.assertEqual(claim["source"]["amount"], "-800.33")
        half = self.post(source_transaction_id=self.expense(-0.05, merchant="Tiny"), share=50)
        self.assertEqual(half.json()["amount"], "0.03")  # 2.5 cents round half up

    def test_claim_from_a_direct_amount(self):
        claim = self.post(date="2026-09-30", description="EV charging 2026 Q3", amount="75,00",
                          personal_category_id=self.energy, expected_date="2026-11-01").json()
        self.assertEqual((claim["amount"], claim["share"], claim["source"]), ("75.00", None, None))
        self.assertEqual((claim["expected_date"], claim["expected_on"]), ("2026-11-01", "2026-11-01"))
        self.assertEqual(claim["expected_on"], "2026-11-01")
        other = self.post(date="2026-09-30", description="x", amount=10).json()
        self.assertEqual(other["expected_on"], "2026-10-30")  # date + 30 days

    def test_validation(self):
        source = self.expense()
        bad = (
            {"source_transaction_id": source, "share": 0}, {"source_transaction_id": source, "share": 101},
            {"source_transaction_id": source}, {"source_transaction_id": 99999, "share": 50},
            {"date": "2026-09-30", "description": "x", "amount": 0}, {"date": "2026-09-30", "description": "x", "amount": -5},
            {"date": "2026-09-30", "description": "", "amount": 5}, {"date": "nope", "description": "x", "amount": 5},
            {"date": "2026-09-30", "description": "x", "amount": 5, "business_category_id": self.energy},
            {"date": "2026-09-30", "description": "x", "amount": 5, "personal_category_id": self.tools},
            {"date": "2026-09-30", "description": "x", "amount": 5, "expected_date": "nope"},
        )
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.post(**body).status_code, 400)
        business_tx = self.tx_id("Start")  # the personal Start is fine; a business one is not
        with core.connect(self.database) as db:
            business_tx = db.execute(
                "SELECT t.id FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE a.account_type = 'business'"
            ).fetchone()[0]
        self.assertEqual(self.post(source_transaction_id=business_tx, share=50).status_code, 400)

    def test_totals_candidates_paying_and_reopening(self):
        first = self.post(date="2026-09-01", description="A", amount=100, personal_category_id=self.energy).json()
        second = self.post(date="2026-09-02", description="B", amount=50).json()
        good = self.repayment(100.0, when=date(2026, 10, 5))
        self.repayment(70.0, merchant="Friend", iban=PERSONAL)  # not from a business account
        self.repayment(60.0, merchant="Old", when=date(2026, 3, 1))  # beyond 180 days
        self.repayment(40.0, merchant="Pay", category="Salary")  # a salary role category
        self.repayment(30.0, merchant="Xfer", category="Entity transfer")
        body = self.client.get("/api/reimbursements").json()
        self.assertEqual([c["id"] for c in body["claims"]], [second["id"], first["id"]])  # newest first
        self.assertEqual((body["open_total"], body["paid_total_this_year"]), ("150.00", "0.00"))
        self.assertEqual([c["id"] for c in body["candidates"]], [good])
        paid = self.client.patch(f"/api/reimbursements/{first['id']}", json={"paid_transaction_id": good}).json()
        self.assertEqual((paid["paid"]["id"], paid["paid"]["date"]), (good, "2026-10-05"))
        body = self.client.get("/api/reimbursements").json()
        self.assertEqual((body["open_total"], body["paid_total_this_year"], body["candidates"]), ("50.00", "100.00", []))
        # Several claims may share one repayment.
        shared = self.client.patch(f"/api/reimbursements/{second['id']}", json={"paid_transaction_id": good})
        self.assertEqual(shared.status_code, 200)
        reopened = self.client.patch(f"/api/reimbursements/{first['id']}", json={"paid_transaction_id": None}).json()
        self.assertIsNone(reopened["paid"])
        self.assertEqual(self.client.patch(f"/api/reimbursements/{first['id']}", json={"paid_transaction_id": 99999}).status_code, 400)
        self.assertEqual(self.client.patch("/api/reimbursements/999", json={"note": "x"}).status_code, 404)

    def test_patch_amount_clears_share_and_delete(self):
        claim = self.post(source_transaction_id=self.expense(), share=60).json()
        patched = self.client.patch(f"/api/reimbursements/{claim['id']}", json={
            "amount": "100", "business_category_id": self.tools, "expected_date": "2026-12-01", "note": "n"}).json()
        self.assertEqual((patched["amount"], patched["share"], patched["expected_on"]), ("100.00", None, "2026-12-01"))
        cleared = self.client.patch(f"/api/reimbursements/{claim['id']}", json={"expected_date": None, "business_category_id": None}).json()
        self.assertEqual((cleared["expected_date"], cleared["business_category_id"], cleared["note"]), (None, None, "n"))
        self.assertEqual(self.client.patch(f"/api/reimbursements/{claim['id']}", json={"amount": 0}).status_code, 400)
        self.assertEqual(self.client.delete(f"/api/reimbursements/{claim['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/reimbursements/{claim['id']}").status_code, 404)

    def test_delay_setting(self):
        self.assertEqual(self.client.get("/api/forecast/settings").json()["reimbursement_delay_days"], 30)
        for value in (-1, 181):
            self.assertEqual(self.client.put("/api/forecast/settings", json={"reimbursement_delay_days": value}).status_code, 400)
        self.assertEqual(self.client.put("/api/forecast/settings", json={"reimbursement_delay_days": 0}).json()["reimbursement_delay_days"], 0)

    def test_merging_and_removing_categories_follow_claims(self):
        other = core.add_category(self.database, "business", "Other")["id"]
        claim = self.post(date="2026-09-01", description="A", amount=10, personal_category_id=self.energy,
                          business_category_id=self.tools).json()
        core.merge_categories(self.database, "business", self.tools, other)
        self.assertEqual(core.list_claims(self.database, TODAY)["claims"][0]["business_category_id"], other)
        core.remove_category(self.database, self.energy)
        listed = core.list_claims(self.database, TODAY)["claims"][0]
        self.assertEqual((listed["id"], listed["personal_category_id"]), (claim["id"], None))

    def test_undo_import_clears_the_transaction_references(self):
        source = self.expense()
        paid = self.repayment(100.0)
        claim = self.post(source_transaction_id=source, share=10).json()
        self.client.patch(f"/api/reimbursements/{claim['id']}", json={"paid_transaction_id": paid})
        with core.connect(self.database) as db:
            import_id = db.execute("SELECT import_id FROM transactions WHERE id = ?", (paid,)).fetchone()[0]
        core.undo_import(self.database, import_id)
        listed = core.list_claims(self.database, TODAY)["claims"][0]
        self.assertEqual((listed["paid_transaction_id"], listed["paid"]), (None, None))
        with core.connect(self.database) as db:
            import_id = db.execute("SELECT import_id FROM transactions WHERE id = ?", (source,)).fetchone()[0]
        core.undo_import(self.database, import_id)
        listed = core.list_claims(self.database, TODAY)["claims"][0]
        self.assertEqual((listed["source_transaction_id"], listed["source"]), (None, None))


class ForecastTests(ReimbursementFixture):
    def pair(self, month):
        return (self.lines("business", month, "reimbursement"), self.lines("personal", month, "reimbursement"))

    def test_open_claim_is_a_linked_pair_in_the_expected_month(self):
        self.post(date="2026-10-20", description="Energy invoice", amount=100, personal_category_id=self.energy,
                  business_category_id=self.tools)
        business, personal = self.pair("2026-11")  # 2026-10-20 + 30 days = 2026-11-19
        self.assertEqual([(l["amount"], l["category"], l["date"], l["detail"]) for l in business],
                         [("-100.00", "Tools", "2026-11-19", "Energy invoice")])
        self.assertEqual([(l["amount"], l["category"], l["date"]) for l in personal], [("100.00", "Groceries", "2026-11-19")])

    def test_explicit_date_delay_setting_and_past_date_moving_to_today(self):
        self.post(date="2026-01-01", description="Old", amount=10)
        self.post(date="2026-10-01", description="Dated", amount=20, expected_date="2027-02-10")
        business, personal = self.pair("2026-10")
        self.assertEqual([(l["date"], l["amount"], l["category"]) for l in business], [("2026-10-25", "-10.00", "Uncategorized")])
        self.assertEqual(personal[0]["category"], "Uncategorized")
        self.assertEqual([l["date"] for l in self.pair("2027-02")[0]], ["2027-02-10"])
        self.settings(reimbursement_delay_days=60)
        self.post(date="2026-10-01", description="Slow", amount=5)
        self.assertEqual([l["date"] for l in self.pair("2026-11")[0] if l["detail"] == "Slow"], ["2026-11-30"])

    def test_paid_claims_leave_the_forecast(self):
        paid = self.repayment(100.0)
        claim = self.post(date="2026-10-20", description="Energy", amount=100).json()
        self.assertEqual(len(self.pair("2026-11")[0]), 1)
        self.client.patch(f"/api/reimbursements/{claim['id']}", json={"paid_transaction_id": paid})
        self.assertEqual(self.pair("2026-11"), ([], []))

    def test_linked_repayments_leave_both_baselines(self):
        for entity, account in (("personal", PERSONAL), ("business", BUSINESS)):
            self.add(account, entity, f"Steady {entity}", [(date(2026, m, 3), -10.0) for m in range(4, 10)],
                     "Groceries" if entity == "personal" else "Tools")
        payments = [(date(2026, m, 12), 100.0) for m in range(4, 10)]
        self.add(PERSONAL, "personal", "Repaid", payments, "Groceries")
        self.add(BUSINESS, "business", "Paid out", [(when, -amount) for when, amount in payments], "Tools")
        before = {e: {l["category"]: l["amount"] for l in self.lines(e, "2026-11", "baseline")} for e in ("personal", "business")}
        self.assertEqual((before["personal"]["Groceries"], before["business"]["Tools"]), ("90.00", "-110.00"))
        with core.connect(self.database) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM transactions WHERE merchant = 'Repaid'")]
        for index, paid in enumerate(ids):
            claim = self.post(date="2026-09-01", description=f"C{index}", amount=100, business_category_id=self.tools).json()
            self.client.patch(f"/api/reimbursements/{claim['id']}", json={"paid_transaction_id": paid})
        after = {e: {l["category"]: l["amount"] for l in self.lines(e, "2026-11", "baseline")} for e in ("personal", "business")}
        self.assertEqual((after["personal"]["Groceries"], after["business"]["Tools"]), ("-10.00", "-10.00"))


if __name__ == "__main__":
    unittest.main()
