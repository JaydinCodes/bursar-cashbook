import unittest
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app, get_db
from app.models import Base


class MainApiTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine)

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

    def test_category_creation_and_reviewed_cashbook_export(self):
        category = self.client.post("/categories", json={"name": "Stationery", "type": "expense"})
        self.assertEqual(category.status_code, 201)

        upload = self.client.post(
            "/statements/upload",
            data={"bank": "Standard Bank"},
            files={"file": ("statement.csv", b"Date,Details,Amount\n2026-08-01,Paper supplier,-99.50\n", "text/csv")},
        )
        pending = self.client.get("/transactions/pending").json()[0]
        reviewed = self.client.post(f"/transactions/{pending['id']}/review", json={"category_id": category.json()["id"]})
        self.assertEqual(upload.status_code, 201)
        self.assertEqual(reviewed.status_code, 200)

        export = self.client.get("/exports/reviewed-cashbook.xlsx")
        self.assertEqual(export.status_code, 200)
        workbook = load_workbook(BytesIO(export.content))
        sheet = workbook["Reviewed Transactions"]
        self.assertEqual(sheet["B2"].value, "Paper supplier")
        self.assertEqual(sheet["E2"].value, "Stationery")
