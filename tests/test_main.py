import os
import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app, get_db
from app.models import Base, Rule, Transaction


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

    def test_import_requires_review_then_exports_selected_year(self):
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

        blocked = self.client.get("/exports/reviewed-cashbook.xlsx?year=2026")
        self.assertEqual(blocked.status_code, 409)

        pending = self.client.get("/transactions/pending").json()[0]
        reviewed = self.client.post(
            f"/transactions/{pending['id']}/review",
            json={"category_id": category_id, "learn": True},
        )
        self.assertEqual(reviewed.status_code, 200)

        export = self.client.get("/exports/reviewed-cashbook.xlsx?year=2026")
        self.assertEqual(export.status_code, 200)
        workbook = load_workbook(BytesIO(export.content))
        sheet = workbook["Reviewed Transactions"]
        self.assertEqual(sheet["B2"].value, "Paper supplier")
        self.assertEqual(sheet["G2"].value, "Stationery")

        other_year = self.client.get("/exports/reviewed-cashbook.xlsx?year=2025")
        self.assertEqual(other_year.status_code, 404)

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

    def test_wced_http_route_calls_real_export_service_alias(self):
        category_id = self.create_category("Nashua")
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Nashua,100.00,,900.00\n"
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

        with (
            patch.dict(os.environ, {"WCED_TEMPLATE_PATH": "fake-template.xls"}),
            patch("app.main.build_wced_cashbook", return_value=b"fake-xls") as exporter,
        ):
            response = self.client.get("/exports/wced-cashbook.xls?year=2026")

        self.assertEqual(response.status_code, 200)
        exporter.assert_called_once()

    def test_xlsx_export_neutralises_formula_text(self):
        category_id = self.create_category("Stationery")
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,=HYPERLINK(\"x\"),100.00,,900.00\n"
        ).encode()
        # CSV quoting for the embedded quotes.
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

        export = self.client.get("/exports/reviewed-cashbook.xlsx?year=2026")
        workbook = load_workbook(BytesIO(export.content), data_only=False)
        value = workbook["Reviewed Transactions"]["B2"].value
        self.assertTrue(value.startswith("'="))


if __name__ == "__main__":
    unittest.main()
