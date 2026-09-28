import json
import re
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import backups
from app.diagnostics import build_diagnostics_zip
from app.errors import new_error_id
from app.main import app, get_db
from app.models import AuditEvent, Base, Category, Rule, Statement, Transaction
from app.version import APP_VERSION


class SupportabilityApiTests(unittest.TestCase):
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

    def _import_statement(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance,Reference\n"
            "01/08/2026,SENSITIVE SCHOOL SUPPLIER,100.00,,900.00,SECRET-REF\n"
        ).encode()
        response = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("private-statement.csv", content, "text/csv")},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["statement_id"]

    def test_health_exposes_application_version(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], APP_VERSION)

    def test_learning_status_reports_local_rule_summary(self):
        db = self.session_factory()
        try:
            category = Category(name="Stationery", type="expense")
            db.add(category)
            db.flush()
            db.add(
                Rule(
                    payee_pattern="SCHOOL SUPPLIER",
                    category_id=category.id,
                    hit_count=3,
                    confidence=1,
                )
            )
            db.commit()
        finally:
            db.close()

        response = self.client.get("/learning/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["examples"], 3)
        self.assertEqual(response.json()["recent_patterns"][0]["category"], "Stationery")

    def test_import_history_and_audit_history_track_state(self):
        statement_id = self._import_statement()

        history = self.client.get("/imports/history").json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], statement_id)
        self.assertEqual(history[0]["pending"], 1)
        self.assertEqual(history[0]["reconciliation_status"], "passed")

        audit = self.client.get("/audit/history").json()
        imported = [event for event in audit if event["event_type"] == "statement.imported"]
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["entity_id"], statement_id)
        self.assertEqual(imported[0]["details"]["transactions_imported"], 1)

    def test_unsynced_statement_can_be_removed_with_its_transactions(self):
        statement_id = self._import_statement()

        response = self.client.delete(f"/imports/{statement_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transactions_removed"], 1)
        self.assertEqual(self.client.get("/imports/history").json(), [])
        db = self.session_factory()
        try:
            self.assertEqual(db.query(Transaction).count(), 0)
            self.assertTrue(
                db.query(AuditEvent)
                .filter(AuditEvent.event_type == "statement.removed")
                .count()
            )
        finally:
            db.close()

    def test_workspace_reset_removes_all_local_database_records(self):
        self._import_statement()
        self.client.post("/categories", json={"name": "Stationery", "type": "expense"})

        response = self.client.post("/workspace/reset")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "reset")
        db = self.session_factory()
        try:
            self.assertEqual(db.query(Statement).count(), 0)
            self.assertEqual(db.query(Transaction).count(), 0)
            self.assertEqual(db.query(AuditEvent).count(), 0)
        finally:
            db.close()

    def test_current_year_summary_falls_back_to_latest_statement_year(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2021,TEST SUPPLIER,100.00,,900.00\n"
        ).encode()
        response = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("2021-statement.csv", content, "text/csv")},
        )
        self.assertEqual(response.status_code, 201)

        summary = self.client.get(f"/cashbook/summary?year={date.today().year}").json()

        self.assertEqual(summary["year"], 2021)

    def test_review_updates_history_and_creates_audit_event(self):
        self._import_statement()
        category = self.client.post(
            "/categories",
            json={"name": "Stationery", "type": "expense"},
        ).json()
        transaction = self.client.get("/transactions/pending").json()[0]

        reviewed = self.client.post(
            f"/transactions/{transaction['id']}/review",
            json={"category_id": category["id"], "learn": True},
        )
        self.assertEqual(reviewed.status_code, 200)

        history = self.client.get("/imports/history").json()[0]
        self.assertEqual(history["pending"], 0)
        self.assertEqual(history["approved"] + history["corrected"], 1)

        events = self.client.get("/audit/history").json()
        reviews = [event for event in events if event["event_type"] == "transaction.reviewed"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["entity_id"], transaction["id"])

    def test_diagnostics_zip_omits_full_transaction_description_and_reference(self):
        self._import_statement()

        response = self.client.get("/diagnostics/export")
        self.assertEqual(response.status_code, 200)

        with ZipFile(BytesIO(response.content)) as archive:
            names = set(archive.namelist())
            self.assertIn("diagnostics.json", names)
            self.assertIn("import-history.json", names)
            self.assertIn("audit-history.json", names)
            self.assertIn("README.txt", names)

            combined = b"\n".join(archive.read(name) for name in names)
            self.assertNotIn(b"SENSITIVE SCHOOL SUPPLIER", combined)
            self.assertNotIn(b"SECRET-REF", combined)
            self.assertNotIn(b"private-statement.csv", combined)

            metadata = json.loads(archive.read("diagnostics.json"))
            self.assertEqual(metadata["app_version"], APP_VERSION)

    def test_audit_event_table_is_append_only_by_usage(self):
        self._import_statement()
        db = self.session_factory()
        try:
            events = db.query(AuditEvent).all()
            self.assertGreaterEqual(len(events), 1)
            self.assertTrue(all(event.created_at is not None for event in events))
        finally:
            db.close()


class BackupTests(unittest.TestCase):
    def test_creates_restorable_sqlite_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            database_path = directory_path / "cashbook.db"
            backup_dir = directory_path / "backups"

            engine = create_engine(f"sqlite:///{database_path}")
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE sample (value INTEGER NOT NULL)"))
                connection.execute(text("INSERT INTO sample(value) VALUES (42)"))

            with patch.object(backups, "BACKUP_DIR", backup_dir), patch.object(
                backups, "BACKUP_RETENTION", 10
            ):
                backup_path = backups.create_sqlite_backup(engine, "test")

            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.is_file())

            backup_engine = create_engine(f"sqlite:///{backup_path}")
            with backup_engine.connect() as connection:
                value = connection.execute(text("SELECT value FROM sample")).scalar_one()
            self.assertEqual(value, 42)

            backup_engine.dispose()
            engine.dispose()


class ErrorIdTests(unittest.TestCase):
    def test_error_id_is_support_friendly(self):
        error_id = new_error_id()
        self.assertRegex(error_id, r"^ERR-\d{8}-[A-F0-9]{8}$")
        self.assertTrue(re.fullmatch(r"ERR-\d{8}-[A-F0-9]{8}", error_id))


if __name__ == "__main__":
    unittest.main()
