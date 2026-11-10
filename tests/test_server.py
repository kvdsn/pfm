import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.pfm import core
from backend.pfm.server import create_app
from tests.test_core import make_csv, transaction

ACCOUNT = "BE00000000000000000001"


class ServerTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PFM_BACKUP_DIR", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.database = root / "finance.sqlite3"
        self.dist = root / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text("<html>app</html>")
        (self.dist / "assets" / "app.js").write_text("console.log('app')")
        (root / "secret.txt").write_text("outside the build")
        self.client = TestClient(create_app(self.database, self.dist))

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def import_csv(self, rows, account_type="personal"):
        preview = self.client.post(
            "/api/import/preview",
            params={"filename": "export.csv"},
            content=make_csv(rows).encode(),
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        commit = self.client.post(
            f"/api/import/{preview.json()['token']}/commit", json={"account_type": account_type}
        )
        self.assertEqual(commit.status_code, 200, commit.text)
        return preview.json(), commit.json()

    def test_import_report_and_category_change_round_trip(self):
        preview, commit = self.import_csv([
            transaction(ACCOUNT, "01/05/2026", "-12,50", "87,50", "Fresh Market", memo="Weekly shop"),
            transaction(ACCOUNT, "02/05/2026", "1000,00", "1087,50", "Employer"),
        ])
        self.assertTrue(preview["account_type_required"])
        self.assertEqual(preview["preview"][0]["memo"], "Weekly shop")
        self.assertEqual(commit, {
            "imported": 2, "duplicates_skipped": 0, "account_type": "personal",
            "source_file": "personal/KBC account 0001/2026-05-01 to 2026-05-02.csv",
        })

        report = self.client.get("/api/report", params={"account_type": "personal"}).json()
        self.assertEqual(report["months"], ["2026-05"])
        self.assertEqual(report["summaries"][0]["expenses"], "12.50")

        category = self.client.post("/api/categories", json={"account_type": "personal", "name": "Groceries"})
        self.assertEqual(category.status_code, 201)
        listed = self.client.get(
            "/api/transactions", params={"account_type": "personal", "year": "2026", "q": "weekly"}
        ).json()
        self.assertEqual(listed["total"], 1)
        transaction_id = listed["items"][0]["id"]
        changed = self.client.patch(f"/api/transactions/{transaction_id}", json={"category_id": category.json()["id"]})
        self.assertEqual(changed.status_code, 200)
        filtered = self.client.get(
            "/api/transactions",
            params={"account_type": "personal", "category_id": category.json()["id"], "flow_type": "expense"},
        ).json()
        self.assertEqual([item["merchant"] for item in filtered["items"]], ["Fresh Market"])

    def test_accounts_overview_endpoint(self):
        self.import_csv([transaction(ACCOUNT, "01/05/2026", "-12,50", "87,50", "Fresh Market")])
        [item] = self.client.get("/api/accounts/overview").json()
        self.assertEqual(item["transaction_count"], 1)
        self.assertEqual(item["latest_balance"], "87.50")
        self.assertEqual(item["first_date"], "2026-05-01")
        self.assertEqual(item["kind"], "bank")

    def test_errors_map_to_status_codes(self):
        cases = [
            (self.client.post("/api/category-rules/999/run", json={"account_type": "personal"}), 404, "Rule not found"),
            (self.client.patch("/api/categories/999", json={"name": "Other"}), 404, "Category not found"),
            (
                self.client.post("/api/categories/merge", json={"account_type": "personal", "source_id": 1, "target_id": 1}),
                400,
                "Source and target categories must be different",
            ),
            (self.client.get("/api/transactions", params={"account_type": "personal", "month": "2026-1"}), 400, "Month must use YYYY-MM format"),
            (self.client.post("/api/import/expired/commit", json={"account_type": "personal"}), 404, "Import preview expired; please select the file again"),
            (self.client.post("/api/import/preview", content=b"not a bank export"), 400, None),
            (self.client.patch("/api/transactions/1", json={"amount": "1.00"}), 400, "No transaction changes provided"),
            (self.client.patch("/api/transactions/999", json={"category_id": None}), 404, "Transaction not found"),
        ]
        for response, status, detail in cases:
            with self.subTest(url=str(response.request.url)):
                self.assertEqual(response.status_code, status, response.text)
                if detail:
                    self.assertEqual(response.json()["detail"], detail)

    def test_rejects_imports_over_the_size_limit(self):
        response = self.client.post("/api/import/preview", content=b"x" * (core.MAX_IMPORT_BYTES + 1))
        self.assertEqual(response.status_code, 413)

    def test_serves_the_frontend_without_leaving_the_build_folder(self):
        self.assertEqual(self.client.get("/assets/app.js").text, "console.log('app')")
        self.assertEqual(self.client.get("/some/client/route").text, "<html>app</html>")
        self.assertEqual(self.client.get("/../secret.txt").text, "<html>app</html>")
        self.assertEqual(self.client.get("/%2e%2e/secret.txt").text, "<html>app</html>")

    def test_backups_go_to_the_configured_folder(self):
        target = Path(self.temp_dir.name) / "synced"
        os.environ["PFM_BACKUP_DIR"] = str(target)
        with TestClient(create_app(self.database, self.dist)) as client:
            created = client.post("/api/backups", json={"label": "x"})
            self.assertEqual(created.status_code, 201)
            self.assertTrue((target / created.json()["name"]).is_file())
            self.assertEqual(client.get("/api/backups").json(), [created.json()])
        self.assertEqual(list(self.database.parent.glob("finance-backup-*")), [])

    def test_a_failed_backup_check_answers_500_with_a_readable_detail(self):
        with patch("backend.pfm.core.database._quick_check", return_value="malformed"):
            response = self.client.post("/api/backups", json={"label": "x"})
        self.assertEqual(response.status_code, 500)
        self.assertIn("integrity check", response.json()["detail"])
        self.assertEqual(self.client.get("/api/backups").json(), [])

    def test_backups_can_be_created_and_listed(self):
        self.assertEqual(self.client.get("/api/backups").json(), [])
        self.import_csv([transaction(ACCOUNT, "01/05/2026", "-10,00", "90,00", "Shop")])
        created = self.client.post("/api/backups", json={"label": "Before rule edits!"})
        self.assertEqual(created.status_code, 201, created.text)
        entry = created.json()
        self.assertEqual(set(entry), {"name", "created_at", "label", "automatic", "size_bytes"})
        self.assertEqual(entry["label"], "before rule edits")
        self.assertEqual(self.client.get("/api/backups").json(), [entry])
        with closing(sqlite3.connect(self.database.with_name(entry["name"]))) as copy:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        self.assertEqual(self.client.post("/api/backups", json={}).status_code, 201)
        self.assertEqual(self.client.post("/api/backups", json={"label": "x" * 41}).status_code, 422)

    def test_automatic_flag_prunes_to_the_latest_twenty_and_keeps_manual_backups(self):
        for index in range(3):
            self.assertEqual(self.client.post("/api/backups", json={"label": f"mine {index}"}).status_code, 201)
        for _ in range(23):
            response = self.client.post("/api/backups", json={"label": "before import", "automatic": True})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertTrue(response.json()["automatic"])
        listed = self.client.get("/api/backups").json()
        self.assertEqual(sum(item["automatic"] for item in listed), core.KEEP_AUTOMATIC_BACKUPS)
        self.assertEqual(sum(not item["automatic"] for item in listed), 3)

    def test_restoring_the_oldest_kept_automatic_backup_is_not_pruned_away_first(self):
        for _ in range(core.KEEP_AUTOMATIC_BACKUPS):
            self.assertEqual(self.client.post("/api/backups", json={"label": "before import", "automatic": True}).status_code, 201)
        oldest = self.client.get("/api/backups").json()[-1]["name"]
        response = self.client.post(f"/api/backups/{oldest}/restore")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["restored"]["name"], oldest)
        self.assertEqual(sum(item["automatic"] for item in self.client.get("/api/backups").json()), core.KEEP_AUTOMATIC_BACKUPS)

    def test_automatic_flag_needs_an_automatic_label(self):
        for label in ("", "my own note", "before"):
            response = self.client.post("/api/backups", json={"label": label, "automatic": True})
            self.assertEqual(response.status_code, 400, label)
        self.assertEqual(self.backup_names(), [])

    def test_manual_backups_cannot_use_automatic_notes(self):
        for label in ("before import", "Before Restoring!", "before upgrade"):
            response = self.client.post("/api/backups", json={"label": label})
            self.assertEqual(response.status_code, 400, label)
            self.assertEqual(response.json()["detail"], "That note is reserved for automatic backups")
        self.assertEqual(self.backup_names(), [])
        manual = self.client.post("/api/backups", json={"label": "before import of 2024"})
        self.assertEqual(manual.status_code, 201)
        self.assertFalse(manual.json()["automatic"])

    def test_a_pruning_error_does_not_fail_the_action(self):
        self.import_csv([transaction(ACCOUNT, "01/05/2026", "-12,50", "87,50", "Fresh Market")])
        import_id = self.client.get("/api/imports", params={"account_type": "personal"}).json()[0]["id"]
        with patch("backend.pfm.core.prune_automatic_backups", side_effect=OSError("locked")):
            self.assertEqual(self.client.delete(f"/api/imports/{import_id}").status_code, 200)
            response = self.client.post("/api/backups", json={"label": "before import", "automatic": True})
            self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.backup_names()), 2)

    def test_safety_backups_prune_old_automatic_backups(self):
        for index in range(25):
            (self.database.parent / f"finance-backup-202601{index + 1:02d}-000000-before-import.sqlite3").write_text("x")
        self.import_csv([transaction(ACCOUNT, "01/05/2026", "-12,50", "87,50", "Fresh Market")])
        import_id = self.client.get("/api/imports", params={"account_type": "personal"}).json()[0]["id"]
        self.assertEqual(self.client.delete(f"/api/imports/{import_id}").status_code, 200)
        listed = self.client.get("/api/backups").json()
        self.assertEqual(len(listed), core.KEEP_AUTOMATIC_BACKUPS)
        self.assertIn("before undoing import", [item["label"] for item in listed])

    def category_names(self):
        return {item["name"] for item in self.client.get("/api/categories?account_type=personal").json()}

    def test_restore_replaces_current_data_after_a_safety_backup(self):
        self.category("Hobbies")
        backup = self.client.post("/api/backups", json={"label": "keep"}).json()
        self.category("Later")
        response = self.client.post(f"/api/backups/{backup['name']}/restore")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["restored"], backup)
        self.assertIn("before-restoring", body["safety_backup"])
        names = self.category_names()
        self.assertIn("Hobbies", names)
        self.assertNotIn("Later", names)
        with closing(sqlite3.connect(self.database.with_name(body["safety_backup"]))) as copy:
            saved = {row[0] for row in copy.execute("SELECT name FROM categories")}
        self.assertIn("Later", saved)

    def test_restore_with_an_unknown_name_changes_nothing(self):
        self.category("Hobbies")
        for name in ("finance.sqlite3", "finance-backup-20200101-000000.sqlite3"):
            self.assertEqual(self.client.post(f"/api/backups/{name}/restore").status_code, 404, name)
        # A slash never reaches the handler, so a traversal name cannot match a route.
        self.assertIn(self.client.post("/api/backups/..%2Ffinance.sqlite3/restore").status_code, (404, 405))
        self.assertEqual(self.backup_names(), [])
        self.assertIn("Hobbies", self.category_names())

    def test_restore_of_a_corrupt_backup_is_rejected(self):
        self.category("Hobbies")
        (self.database.parent / "finance-backup-20260101-000000.sqlite3").write_text("not a database")
        response = self.client.post("/api/backups/finance-backup-20260101-000000.sqlite3/restore")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a usable backup", response.json()["detail"])
        self.assertIn("Hobbies", self.category_names())

    def test_restore_does_nothing_when_the_safety_backup_fails(self):
        self.category("Hobbies")
        backup = self.client.post("/api/backups", json={}).json()
        self.category("Later")
        with patch("backend.pfm.core.backup_database", side_effect=OSError("disk full")):
            response = self.client.post(f"/api/backups/{backup['name']}/restore")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Later", self.category_names())

    def test_restore_clears_pending_import_previews(self):
        backup = self.client.post("/api/backups", json={}).json()
        preview = self.client.post(
            "/api/import/preview",
            params={"filename": "export.csv"},
            content=make_csv([transaction(ACCOUNT, "01/05/2026", "-12,50", "87,50", "Shop")]).encode(),
        ).json()
        self.assertEqual(self.client.post(f"/api/backups/{backup['name']}/restore").status_code, 200)
        commit = self.client.post(f"/api/import/{preview['token']}/commit", json={"account_type": "personal"})
        self.assertEqual(commit.status_code, 404)

    def test_restore_removes_imported_data_added_after_the_backup(self):
        backup = self.client.post("/api/backups", json={}).json()
        self.import_csv([transaction(ACCOUNT, "01/05/2026", "-12,50", "87,50", "Shop")])
        self.assertEqual(self.client.post(f"/api/backups/{backup['name']}/restore").status_code, 200)
        self.assertEqual(self.client.get("/api/imports?account_type=personal").json(), [])

    def category(self, name):
        response = self.client.post("/api/categories", json={"account_type": "personal", "name": name})
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def rule(self, category_id, text="market"):
        response = self.client.post("/api/category-rules", json={
            "account_type": "personal", "category_id": category_id, "match_text": text,
            "match_fields": ["merchant"], "flow_type": "expense",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def backup_names(self):
        return [item["name"] for item in self.client.get("/api/backups").json()]

    def later(self, minutes):
        fake = MagicMock()
        fake.now.return_value = datetime.now() + timedelta(minutes=minutes)
        fake.fromisoformat = datetime.fromisoformat
        return patch("backend.pfm.server.datetime", fake)

    def test_undoing_an_import_backs_up_first_every_time(self):
        for _ in range(2):
            preview, commit = self.import_csv([transaction(ACCOUNT, "01/05/2026", "-12,50", "87,50", "Fresh Market")])
            before = self.backup_names()
            self.assertEqual(self.client.delete(f"/api/imports/{self.client.get('/api/imports', params={'account_type': 'personal'}).json()[0]['id']}").status_code, 200)
            added = [name for name in self.backup_names() if name not in before]
            self.assertEqual(len(added), 1)
            self.assertIn("before-undoing-import", added[0])

    def test_removing_and_merging_categories_back_up_first_every_time(self):
        first, second, third = self.category("A"), self.category("B"), self.category("C")
        steps = [
            ("before-merging-categories", lambda: self.client.post(
                "/api/categories/merge", json={"account_type": "personal", "source_id": first, "target_id": second})),
            ("before-removing-category", lambda: self.client.delete(f"/api/categories/{third}")),
            ("before-removing-category", lambda: self.client.delete(f"/api/categories/{second}")),
        ]
        for label, action in steps:
            before = self.backup_names()
            self.assertEqual(action().status_code, 200)
            added = [name for name in self.backup_names() if name not in before]
            self.assertEqual(len(added), 1)
            self.assertIn(label, added[0])

    def test_rule_backups_are_throttled_to_one_per_half_hour(self):
        category = self.category("Groceries")
        first, second = self.rule(category, "market"), self.rule(category, "shop")
        run = lambda rule_id: self.client.post(f"/api/category-rules/{rule_id}/run", json={"account_type": "personal"})
        self.assertEqual(self.backup_names(), [])
        self.assertEqual(run(first).status_code, 200)
        self.assertEqual(len(self.backup_names()), 1)
        self.assertIn("before-applying-rules", self.backup_names()[0])
        deleted = self.client.delete(f"/api/category-rules/{second}", params={"account_type": "personal"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(len(self.backup_names()), 1)
        with self.later(29):
            run(first)
        self.assertEqual(len(self.backup_names()), 1)
        with self.later(31):
            run(first)
        self.assertEqual(len(self.backup_names()), 2)

    def test_deleting_a_rule_backs_up_when_the_newest_backup_is_old(self):
        category = self.category("Groceries")
        rule = self.rule(category)
        self.assertEqual(self.client.post("/api/backups", json={"label": "manual"}).status_code, 201)
        with self.later(31):
            deleted = self.client.delete(f"/api/category-rules/{rule}", params={"account_type": "personal"})
        self.assertEqual(deleted.status_code, 200)
        names = self.backup_names()
        self.assertEqual(len(names), 2)
        self.assertTrue(any("before-deleting-rule" in name for name in names))

    def test_saving_a_rule_makes_no_backup_and_running_it_reports_counts(self):
        category = self.category("Groceries")
        self.import_csv([transaction(ACCOUNT, "01/05/2026", "-12,50", "87,50", "Fresh Market")])
        rule = self.rule(category)
        body = {
            "account_type": "personal", "category_id": category, "match_text": "fresh",
            "match_fields": ["merchant"], "flow_type": "expense",
        }
        saved = self.client.put(f"/api/category-rules/{rule}", json=body)
        self.assertEqual((saved.status_code, saved.json()), (200, {"id": rule}))
        self.assertEqual(self.backup_names(), [])
        run = self.client.post(f"/api/category-rules/{rule}/run", json={"account_type": "personal"})
        self.assertEqual((run.status_code, run.json()), (200, {"updated": 1, "released": 0}))
        self.assertEqual(len(self.backup_names()), 1)

    def test_other_changes_make_no_backup(self):
        category = self.category("Groceries")
        self.client.patch(f"/api/categories/{category}", json={"name": "Food"})
        self.rule(category)
        self.assertEqual(self.backup_names(), [])

    def test_a_failed_backup_stops_the_action(self):
        first, second = self.category("A"), self.category("B")
        rule = self.rule(first)
        with patch.object(core, "backup_database", side_effect=OSError("disk full")):
            merge = self.client.post(
                "/api/categories/merge", json={"account_type": "personal", "source_id": first, "target_id": second})
            removal = self.client.delete(f"/api/categories/{second}")
            deleted = self.client.delete(f"/api/category-rules/{rule}", params={"account_type": "personal"})
        for response in (merge, removal, deleted):
            self.assertEqual(response.status_code, 500)
            self.assertIn("nothing was changed", response.json()["detail"])
        names = [item["name"] for item in self.client.get("/api/category-management", params={"account_type": "personal"}).json()]
        self.assertTrue({"A", "B"} <= set(names))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0], 1)

    def test_importing_the_server_does_not_open_a_database(self):
        self.assertFalse(hasattr(__import__("backend.pfm.server", fromlist=["app"]), "app"))


if __name__ == "__main__":
    unittest.main()
