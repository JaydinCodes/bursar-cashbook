import unittest
from hashlib import sha256
from pathlib import Path
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app, get_db
from app.models import Base, Rule, Transaction
from app import cashbook_sync


class MainApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def create_category(self, name="Stationery", category_type="expense"):
        response = self.client.post(
            "/categories",
            json={"name": name, "type": category_type},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def test_import_requires_review_then_becomes_ready_for_live_cashbook(self):
        category_id = self.create_category()
        content = (
            "Transaction Date,Details,Debit,Credit,Balance,Reference\n"
            "01/08/2026,Paper supplier,99.50,,900.50,PAPER-1\n"
        ).encode()

        upload = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )
        self.assertEqual(upload.status_code, 201)
        self.assertEqual(upload.json()["reconciliation"]["difference"], "0.00")
        self.assertEqual(upload.json()["cashbook_sync"]["status"], "pending_confirmation")

        pending_summary = self.client.get("/cashbook/summary?year=2026").json()
        self.assertEqual(pending_summary["pending"], 1)
        self.assertFalse(pending_summary["ready"])

        pending = self.client.get("/transactions/pending").json()[0]
        reviewed = self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": category_id, "learn": True},
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json()["cashbook_sync"]["status"], "pending_confirmation")

        summary = self.client.get("/cashbook/summary?year=2026").json()
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["cashbook_needs_sync"], 0)
        self.assertFalse(summary["cashbook_registered"])

        # Phase 6 explicitly retires generated/downloaded cashbooks.
        retired = self.client.get("/exports/wced-cashbook.xls?year=2026")
        self.assertEqual(retired.status_code, 410)

    def test_excel_changes_only_after_explicit_sync_confirmation(self):
        workbook = Path(__file__).parent / "fixtures" / "synthetic_wced_2020.xls"
        statement = (
            "Transaction Date,Details,Debit,Credit,Balance,Reference\n"
            "31/01/2020,CONTROLLED WORKFLOW SUPPLIER,100.00,,900.00,CONTROL-1\n"
        ).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_path = root / "active-cashbook.xls"
            backup_dir = root / "cashbook_backups"
            with patch.object(cashbook_sync, "DEFAULT_ACTIVE_CASHBOOK", live_path), patch.object(
                cashbook_sync, "CASHBOOK_BACKUP_DIR", backup_dir
            ):
                registered = self.client.post(
                    "/cashbook/register",
                    data={"financial_year": "2020"},
                    files={"file": ("2020 cashbook.xls", workbook.read_bytes(), "application/vnd.ms-excel")},
                )
                self.assertEqual(registered.status_code, 201)
                original_hash = sha256(live_path.read_bytes()).hexdigest()

                uploaded = self.client.post(
                    "/statements/upload",
                    data={"bank": "Standard Bank"},
                    files={"file": ("statement.csv", statement, "text/csv")},
                )
                self.assertEqual(uploaded.status_code, 201)
                self.assertEqual(uploaded.json()["cashbook_sync"]["status"], "pending_confirmation")
                self.assertEqual(uploaded.json()["cashbook_sync"]["eligible_transactions"], 0)
                self.assertEqual(sha256(live_path.read_bytes()).hexdigest(), original_hash)

                # Pending transactions are never eligible for the explicit writer.
                pending_sync = self.client.post("/cashbook/sync")
                self.assertEqual(pending_sync.status_code, 200)
                self.assertEqual(pending_sync.json()["status"], "up_to_date")
                self.assertEqual(sha256(live_path.read_bytes()).hexdigest(), original_hash)

                category_id = next(
                    item["id"] for item in self.client.get("/categories").json()
                    if item["type"] == "expense"
                )
                pending = self.client.get("/transactions/pending").json()[0]
                reviewed = self.client.post(
                    f"/transactions/{pending['id']}/review",
                    json={"category_id": category_id, "cashbook_narrative": "CONTROLLED SUPPLIER"},
                )
                self.assertEqual(reviewed.status_code, 200)
                self.assertEqual(reviewed.json()["cashbook_sync"]["cashbook_needs_sync"], 1)
                self.assertEqual(reviewed.json()["cashbook_sync"]["eligible_transactions"], 1)
                self.assertEqual(sha256(live_path.read_bytes()).hexdigest(), original_hash)

                preview = self.client.get("/cashbook/sync-preview")
                self.assertEqual(preview.status_code, 200)
                self.assertTrue(preview.json()["ready"])
                self.assertEqual(preview.json()["transactions"][0]["payee"], "CONTROLLED WORKFLOW SUPPLIER")
                self.assertEqual(preview.json()["transactions"][0]["cashbook_narrative"], "CONTROLLED SUPPLIER")
                self.assertEqual(sha256(live_path.read_bytes()).hexdigest(), original_hash)

                synced = self.client.post("/cashbook/sync")
                self.assertEqual(synced.status_code, 200)
                self.assertEqual(synced.json()["status"], "synced")
                self.assertEqual(synced.json()["written"], 1)
                synced_hash = sha256(live_path.read_bytes()).hexdigest()
                self.assertNotEqual(synced_hash, original_hash)

                repeated = self.client.post("/cashbook/sync")
                self.assertEqual(repeated.status_code, 200)
                self.assertEqual(repeated.json()["status"], "up_to_date")
                self.assertEqual(sha256(live_path.read_bytes()).hexdigest(), synced_hash)

    def test_overlapping_statement_skips_existing_transactions(self):
        first = (
            "Transaction Date,Description,Debit,Credit,Balance,Reference\n"
            "01/08/2026,Vendor A,100.00,,900.00,A1\n"
            "02/08/2026,Fees,,200.00,1100.00,A2\n"
        ).encode()
        second = (
            "Transaction Date,Description,Debit,Credit,Balance,Reference\n"
            "01/08/2026,Vendor A,100.00,,900.00,A1\n"
            "02/08/2026,Fees,,200.00,1100.00,A2\n"
            "03/08/2026,Vendor B,50.00,,1050.00,A3\n"
        ).encode()

        first_response = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("first.csv", first, "text/csv")},
        )
        self.assertEqual(first_response.status_code, 201)

        second_response = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("second.csv", second, "text/csv")},
        )
        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(second_response.json()["transactions_imported"], 1)
        self.assertEqual(second_response.json()["duplicates_skipped"], 2)

        db = self.session_factory()
        try:
            self.assertEqual(db.query(Transaction).count(), 3)
        finally:
            db.close()

    def test_same_statement_file_is_rejected(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Vendor,100.00,,900.00\n"
        ).encode()

        first = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )
        second = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)

    def test_duplicate_rows_inside_same_statement_are_rejected(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance,Reference\n"
            "01/08/2026,Vendor,100.00,,900.00,A1\n"
            "01/08/2026,Vendor,100.00,,900.00,A1\n"
        ).encode()

        response = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )
        self.assertEqual(response.status_code, 422)

    def test_review_retry_is_idempotent(self):
        category_id = self.create_category()
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Paper supplier,100.00,,900.00\n"
        ).encode()
        self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )
        pending = self.client.get("/transactions/pending").json()[0]

        first = self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": category_id, "learn": True},
        )
        second = self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": category_id, "learn": True},
        )

        self.assertFalse(first.json()["idempotent"])
        self.assertTrue(second.json()["idempotent"])
        self.assertFalse(second.json()["learned"])

        db = self.session_factory()
        try:
            self.assertEqual(sum(rule.hit_count for rule in db.query(Rule).all()), 1)
        finally:
            db.close()

    def test_review_can_be_corrected_without_double_learning(self):
        first_category = self.create_category("Stationery")
        second_category = self.create_category("Printing")
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Paper supplier,100.00,,900.00\n"
        ).encode()
        self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )
        pending = self.client.get("/transactions/pending").json()[0]

        self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": first_category, "learn": True},
        )
        changed = self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": second_category, "learn": True},
        )
        self.assertEqual(changed.status_code, 200)

        db = self.session_factory()
        try:
            rules = db.query(Rule).all()
            self.assertEqual(sum(rule.hit_count for rule in rules), 1)
            transaction = db.query(Transaction).one()
            self.assertEqual(transaction.category_id, second_category)
            self.assertEqual(transaction.learned_category_id, second_category)
        finally:
            db.close()

    def test_income_requires_income_category(self):
        expense_id = self.create_category("Stationery", "expense")
        income_id = self.create_category("School Fees", "income")
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Fees,,100.00,1100.00\n"
        ).encode()
        self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )
        pending = self.client.get("/transactions/pending").json()[0]

        wrong = self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": expense_id},
        )
        self.assertEqual(wrong.status_code, 422)

        correct = self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": income_id},
        )
        self.assertEqual(correct.status_code, 200)

    def test_cashbook_download_routes_are_retired(self):
        reviewed = self.client.get("/exports/reviewed-cashbook.xlsx?year=2026")
        wced = self.client.get("/exports/wced-cashbook.xls?year=2026")
        self.assertEqual(reviewed.status_code, 410)
        self.assertEqual(wced.status_code, 410)
        self.assertIn("registered live cashbook", wced.json()["detail"])

    def test_trusted_exact_rule_is_auto_approved_and_populates_preview(self):
        category_id = self.create_category("Nashua")
        db = self.session_factory()
        try:
            db.add(
                Rule(
                    payee_pattern="NASHUA",
                    category_id=category_id,
                    confidence=1.0,
                    hit_count=5,
                )
            )
            db.commit()
        finally:
            db.close()

        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Nashua,100.00,,900.00\n"
        ).encode()
        upload = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )

        self.assertEqual(upload.status_code, 201)
        self.assertEqual(upload.json()["auto_approved"], 1)
        self.assertEqual(upload.json()["pending_review"], 0)
        self.assertEqual(self.client.get("/transactions/pending").json(), [])

        preview = self.client.get("/cashbook/preview?year=2026").json()
        self.assertEqual(preview["summary"]["auto_approved"], 1)
        self.assertEqual(preview["rows"][0]["allocation_status"], "auto_approved")
        self.assertEqual(preview["rows"][0]["target_sheet"], "Aug PC")
        self.assertEqual(preview["rows"][0]["category_name"], "Nashua")

    def test_trusted_income_rule_is_auto_approved_after_learning_history(self):
        category_id = self.create_category("School Fees", "income")
        db = self.session_factory()
        try:
            db.add(
                Rule(
                    payee_pattern="SCHOOL FEES",
                    category_id=category_id,
                    confidence=1.0,
                    hit_count=4,
                )
            )
            db.commit()
        finally:
            db.close()

        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,School fees,,250.00,1250.00\n"
        ).encode()
        upload = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )

        self.assertEqual(upload.status_code, 201)
        self.assertEqual(upload.json()["auto_approved"], 1)
        self.assertEqual(upload.json()["pending_review"], 0)
        preview = self.client.get("/cashbook/preview?year=2026").json()
        self.assertEqual(preview["rows"][0]["target_sheet"], "Aug RC")
        self.assertEqual(preview["rows"][0]["category_name"], "School Fees")

    def test_exact_rule_below_trust_threshold_stays_pending(self):
        category_id = self.create_category("Stationery")
        db = self.session_factory()
        try:
            db.add(
                Rule(
                    payee_pattern="PAPER SUPPLIER",
                    category_id=category_id,
                    confidence=0.90,
                    hit_count=10,
                )
            )
            db.commit()
        finally:
            db.close()

        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Paper supplier,100.00,,900.00\n"
        ).encode()
        upload = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )

        self.assertEqual(upload.json()["auto_approved"], 0)
        self.assertEqual(upload.json()["pending_review"], 1)

    def test_exact_rule_with_too_few_hits_stays_pending(self):
        category_id = self.create_category("Stationery")
        db = self.session_factory()
        try:
            db.add(
                Rule(
                    payee_pattern="PAPER SUPPLIER",
                    category_id=category_id,
                    confidence=1.0,
                    hit_count=2,
                )
            )
            db.commit()
        finally:
            db.close()

        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Paper supplier,100.00,,900.00\n"
        ).encode()
        upload = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )

        self.assertEqual(upload.json()["auto_approved"], 0)
        self.assertEqual(upload.json()["pending_review"], 1)

    def test_auto_approved_allocation_can_be_corrected_from_preview(self):
        first_category = self.create_category("Nashua")
        second_category = self.create_category("Printing")
        db = self.session_factory()
        try:
            db.add(
                Rule(
                    payee_pattern="NASHUA",
                    category_id=first_category,
                    confidence=1.0,
                    hit_count=5,
                )
            )
            db.commit()
        finally:
            db.close()

        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Nashua,100.00,,900.00\n"
        ).encode()
        self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )
        preview = self.client.get("/cashbook/preview?year=2026").json()
        transaction_id = preview["rows"][0]["transaction_id"]

        corrected = self.client.post(
            f"/transactions/{transaction_id}/review",
            json={"category_id": second_category, "learn": True},
        )
        self.assertEqual(corrected.status_code, 200)
        self.assertEqual(corrected.json()["status"], "corrected")

        preview = self.client.get("/cashbook/preview?year=2026").json()
        self.assertEqual(preview["rows"][0]["category_name"], "Printing")
        self.assertEqual(preview["rows"][0]["allocation_status"], "corrected")

    def test_preview_treats_formula_like_description_as_plain_data(self):
        category_id = self.create_category("Stationery")
        content = (
            'Transaction Date,Description,Debit,Credit,Balance\n'
            '01/08/2026,"=HYPERLINK(""x"")",100.00,,900.00\n'
        ).encode()
        self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", content, "text/csv")},
        )
        pending = self.client.get("/transactions/pending").json()[0]
        self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": category_id, "learn": False},
        )

        preview = self.client.get("/cashbook/preview?year=2026").json()
        self.assertEqual(preview["rows"][0]["description"], '=HYPERLINK("x")')
        self.assertIn(preview["rows"][0]["sync_status"], {"cashbook_not_registered", "needs_sync"})


if __name__ == "__main__":
    unittest.main()
