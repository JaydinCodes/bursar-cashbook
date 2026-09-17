import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import backups
from app.main import app, get_db
from app.models import Base, Category, Statement, Transaction
from app.version import APP_VERSION


class HandoverApiTests(unittest.TestCase):
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

    def test_help_page_is_available(self):
        response = self.client.get("/help")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Bursar Cashbook Help", response.text)

    def test_version_bumped_for_handover(self):
        self.assertEqual(APP_VERSION, "0.6.0")

    def test_setup_status_reports_missing_categories_and_live_cashbook(self):
        response = self.client.get("/setup/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["ready_for_import"])
        self.assertFalse(data["ready_for_live_sync"])
        self.assertEqual(data["category_count"], 0)
        self.assertFalse(data["cashbook"]["registered"])

    def test_setup_status_becomes_import_ready_when_categories_exist(self):
        db = self.session_factory()
        db.add(Category(name="Stationery", type="expense"))
        db.commit()
        db.close()

        response = self.client.get("/setup/status")
        data = response.json()
        self.assertTrue(data["ready_for_import"])
        self.assertFalse(data["ready_for_live_sync"])
        self.assertFalse(data["cashbook"]["registered"])

    def test_cashbook_summary_tracks_review_readiness(self):
        db = self.session_factory()
        category = Category(name="Stationery", type="expense")
        db.add(category)
        db.flush()
        statement = Statement(
            bank="Standard Bank",
            source_filename="statement.csv",
            source_hash="a" * 64,
            period_start=__import__("datetime").date(2026, 8, 1),
            period_end=__import__("datetime").date(2026, 8, 1),
            financial_year=2026,
            opening_balance=Decimal("1000.00"),
            closing_balance=Decimal("900.00"),
            total_debits=Decimal("100.00"),
            total_credits=Decimal("0.00"),
            reconciliation_difference=Decimal("0.00"),
            reconciliation_status="passed",
            source_transaction_count=1,
            imported_transaction_count=1,
            duplicate_transaction_count=0,
        )
        db.add(statement)
        db.flush()
        transaction = Transaction(
            statement_id=statement.id,
            fingerprint="b" * 64,
            source_row=2,
            txn_date=__import__("datetime").date(2026, 8, 1),
            payee_raw="Vendor",
            payee_normalized="VENDOR",
            balance_after=Decimal("900.00"),
            amount=Decimal("100.00"),
            direction="debit",
            status="pending",
        )
        db.add(transaction)
        db.commit()

        pending = self.client.get("/cashbook/summary?year=2026").json()
        self.assertFalse(pending["ready"])
        self.assertEqual(pending["pending"], 1)

        transaction.status = "corrected"
        transaction.category_id = category.id
        db.commit()
        reviewed = self.client.get("/cashbook/summary?year=2026").json()
        self.assertTrue(reviewed["ready"])
        self.assertEqual(reviewed["money_out"], "100.00")
        db.close()


class BackupRestoreTests(unittest.TestCase):
    def test_restore_replaces_database_and_creates_valid_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "cashbook.db"
            backup_dir = root / "backups"
            engine = create_engine(f"sqlite:///{database_path}")

            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine)
            db = Session()
            db.add(Category(name="Before", type="expense"))
            db.commit()
            db.close()

            with patch.object(backups, "BACKUP_DIR", backup_dir), patch.object(
                backups, "BACKUP_RETENTION", 10
            ):
                backup_path = backups.create_sqlite_backup(engine, "manual")
                self.assertIsNotNone(backup_path)

                db = Session()
                db.add(Category(name="After", type="expense"))
                db.commit()
                db.close()

                backups.restore_sqlite_backup(engine, backup_path.name)

            RestoredSession = sessionmaker(bind=engine)
            db = RestoredSession()
            names = {category.name for category in db.query(Category).all()}
            db.close()
            engine.dispose()

            self.assertIn("Before", names)
            self.assertNotIn("After", names)


if __name__ == "__main__":
    unittest.main()
