import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cashbook_sync import sync_categories_from_layout
from app.main import app, get_db
from app.models import Base, Category, Rule, Statement, Transaction
from app.presentation import display_payee, display_reference


def statement() -> Statement:
    return Statement(bank="Standard Bank", source_filename="test.csv", source_hash="a" * 64,
        period_start=date(2026, 9, 1), period_end=date(2026, 9, 2), financial_year=2026,
        opening_balance=Decimal("100"), closing_balance=Decimal("100"), total_debits=Decimal("0"),
        total_credits=Decimal("0"), reconciliation_difference=Decimal("0"), reconciliation_status="passed",
        source_transaction_count=1, imported_transaction_count=1, duplicate_transaction_count=0)


class Phase71Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        def override():
            db = self.Session()
            try: yield db
            finally: db.close()
        app.dependency_overrides[get_db] = override
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear(); self.engine.dispose()

    def transaction(self, db, fingerprint, description, direction="debit", merchant="UBER CPT", narrative=None):
        row = statement(); row.source_hash = fingerprint * 64; db.add(row); db.flush()
        db.add(Transaction(statement_id=row.id, fingerprint=(fingerprint * 64)[:64], source_row=2,
            txn_date=date(2026, 9, 7), payee_raw=description, payee_normalized=merchant, merchant_key=merchant,
            reference=None, balance_after=Decimal("100"), amount=Decimal("24"), direction=direction, status="pending"))
        db.flush()
        db.query(Transaction).filter_by(fingerprint=(fingerprint * 64)[:64]).one().cashbook_narrative = narrative

    def test_workbook_categories_upsert_by_type_without_duplicates(self):
        db = self.Session()
        try:
            first = sync_categories_from_layout(db, {"payment_categories": ["Stationery"], "receipt_categories": ["Interest"]})
            db.commit()
            stationery = db.query(Category).filter_by(name="Stationery", type="expense").one()
            second = sync_categories_from_layout(db, {"payment_categories": ["Stationery"], "receipt_categories": ["Interest"]})
            db.commit()
            self.assertEqual(first["created"], 2); self.assertEqual(second["created"], 0)
            self.assertEqual(db.query(Category).filter_by(name="Stationery", type="expense").one().id, stationery.id)
            self.assertEqual(db.query(Category).filter_by(name="Interest", type="income").count(), 1)
        finally: db.close()

    def test_payee_and_reference_display_are_conservative(self):
        self.assertEqual(display_payee("DL*UBER CPT ZAF 06-09-2026 20H43:22 OUTSTANDING CARD AUTHORISATION"), "UBER CPT")
        self.assertEqual(display_payee("S2S*HASSANYUS 5196*5110 04 SEP DEBIT CARD PURCHASE FROM"), "HASSANYUS")
        self.assertEqual(display_payee("UNRECOGNISED BANK TEXT"), "UNRECOGNISED BANK TEXT")
        self.assertEqual(display_reference("  ABC  123 "), "ABC 123")
        self.assertIsNone(display_reference(None))

    def test_paginated_queue_and_bulk_never_crosses_direction(self):
        db = self.Session(); category = Category(name="Transport", type="expense"); db.add(category)
        self.transaction(db, "a", "DL*UBER CPT ZAF 06-09-2026")
        self.transaction(db, "b", "DL*UBER CPT ZAF 07-09-2026")
        self.transaction(db, "c", "DL*UBER CPT ZAF 08-09-2026", "credit")
        db.commit(); db.close()
        queue = self.client.get("/transactions/pending?page=1&page_size=25&search=UBER").json()
        self.assertEqual(queue["total"], 3); self.assertEqual(queue["transactions"][0]["reference_display"], None)
        debit_id = next(item["id"] for item in queue["transactions"] if item["direction"] == "debit")
        response = self.client.post(f"/transactions/{debit_id}/review", json={"category_id": category.id, "apply_to_matches": True})
        self.assertEqual(response.status_code, 200); self.assertEqual(response.json()["reviewed_count"], 2)
        reviewed = self.client.get("/transactions/pending?page=1&page_size=25&status=reviewed").json()
        self.assertEqual(reviewed["total"], 2)
        db = self.Session()
        try:
            self.assertEqual(db.query(Transaction).filter_by(direction="debit", status="pending").count(), 0)
            self.assertEqual(db.query(Transaction).filter_by(direction="credit", status="pending").count(), 1)
        finally: db.close()

    def test_bulk_review_propagates_category_and_narrative_without_learning(self):
        db = self.Session()
        try:
            expense = Category(name="Transport", type="expense")
            income = Category(name="Fees", type="income")
            db.add_all([expense, income]); db.flush()
            self.transaction(db, "a", "UBER DEBIT 1")
            self.transaction(db, "b", "UBER DEBIT 2")
            self.transaction(db, "c", "UBER CREDIT", direction="credit")
            self.transaction(db, "d", "OTHER DEBIT", merchant="OTHER MERCHANT")
            db.commit()
            debit_id = db.query(Transaction.id).filter_by(fingerprint="a" * 64).scalar()
        finally:
            db.close()

        response = self.client.post(
            f"/transactions/{debit_id}/review",
            json={
                "category_id": expense.id,
                "cashbook_narrative": "UBER TRANSPORT",
                "learn": False,
                "apply_to_matches": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reviewed_count"], 2)

        db = self.Session()
        try:
            debits = db.query(Transaction).filter_by(merchant_key="UBER CPT", direction="debit").all()
            self.assertEqual(len(debits), 2)
            self.assertTrue(all(item.category_id == expense.id for item in debits))
            self.assertTrue(all(item.cashbook_narrative == "UBER TRANSPORT" for item in debits))
            self.assertTrue(all(item.learned_category_id is None for item in debits))
            self.assertEqual(db.query(Rule).count(), 0)
            self.assertEqual(db.query(Transaction).filter_by(direction="credit", status="pending").count(), 1)
            self.assertEqual(db.query(Transaction).filter_by(merchant_key="OTHER MERCHANT", status="pending").count(), 1)
        finally:
            db.close()

    def test_bulk_blank_narrative_preserves_existing_and_learned_wording(self):
        db = self.Session()
        try:
            category = Category(name="Transport", type="expense")
            db.add(category); db.flush()
            db.add(Rule(payee_pattern="UBER CPT", category_id=category.id, hit_count=3,
                        confidence=Decimal("1"), cashbook_narrative="KEEP LEARNED"))
            self.transaction(db, "e", "UBER DEBIT 1", narrative="KEEP FIRST")
            self.transaction(db, "f", "UBER DEBIT 2", narrative="KEEP SECOND")
            db.commit()
            debit_id = db.query(Transaction.id).filter_by(fingerprint="e" * 64).scalar()
        finally:
            db.close()

        response = self.client.post(
            f"/transactions/{debit_id}/review",
            json={"category_id": category.id, "cashbook_narrative": "   ", "learn": True, "apply_to_matches": True},
        )
        self.assertEqual(response.status_code, 200)

        db = self.Session()
        try:
            narratives = {
                item.fingerprint: item.cashbook_narrative
                for item in db.query(Transaction).filter_by(merchant_key="UBER CPT", direction="debit")
            }
            self.assertEqual(narratives["e" * 64], "KEEP FIRST")
            self.assertEqual(narratives["f" * 64], "KEEP SECOND")
            self.assertEqual(db.query(Rule).filter_by(payee_pattern="UBER CPT").one().cashbook_narrative, "KEEP LEARNED")
        finally:
            db.close()

    def test_compact_transaction_queue_allows_ten_entries_per_page(self):
        response = self.client.get("/transactions/pending?page=1&page_size=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["page_size"], 10)

    def test_cashbook_actions_are_blocked_before_registration(self):
        self.assertEqual(self.client.get("/cashbook/open").status_code, 405)
        opened = self.client.post("/cashbook/open")
        synced = self.client.post("/cashbook/sync")
        self.assertEqual(opened.status_code, 409)
        self.assertEqual(synced.status_code, 409)
        self.assertEqual(opened.json()["detail"], "No cashbook is connected. Connect a cashbook first.")
        self.assertEqual(synced.json()["detail"], "No cashbook is connected. Connect a cashbook first.")
        self.assertEqual(self.client.get("/setup/status").json()["readiness"], "setup_required")

    def test_registration_exposes_connected_status_and_ready_state(self):
        workbook = Path(__file__).parents[1] / "data" / "2020_cashbook.xls"
        response = self.client.post(
            "/cashbook/register",
            data={"financial_year": "2020"},
            files={"file": ("cashbook.xls", workbook.read_bytes(), "application/vnd.ms-excel")},
        )
        self.assertEqual(response.status_code, 201)
        status = self.client.get("/cashbook/status").json()
        self.assertTrue(status["registered"])
        self.assertEqual(status["source_filename"], "cashbook.xls")
        self.assertTrue(status["adapter"])
        categories = self.client.get("/categories")
        self.assertEqual(categories.status_code, 200)
        self.assertTrue(categories.json())
        self.assertEqual(self.client.get("/setup/status").json()["readiness"], "ready")

    def test_zero_work_sync_returns_up_to_date_after_registration(self):
        workbook = Path(__file__).parents[1] / "data" / "2020_cashbook.xls"
        registered = self.client.post(
            "/cashbook/register",
            data={"financial_year": "2020"},
            files={"file": ("cashbook.xls", workbook.read_bytes(), "application/vnd.ms-excel")},
        )
        self.assertEqual(registered.status_code, 201)
        response = self.client.post("/cashbook/sync")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "up_to_date")
        self.assertEqual(data["written"], 0)
        self.assertEqual(data["updated"], 0)

    def test_frontend_uses_post_and_disables_actions_when_disconnected(self):
        markup = (Path(__file__).parents[1] / "app" / "static" / "review.html").read_text(encoding="utf-8")
        self.assertIn("api('/cashbook/open',{method:'POST'})", markup)
        self.assertIn("$('openBook').disabled=!connected", markup)
        self.assertIn("$('sync').disabled=!connected", markup)

    def test_review_modal_smoke_contract_has_category_dropdown(self):
        markup = (Path(__file__).parents[1] / "app" / "static" / "review.html").read_text(encoding="utf-8")
        self.assertIn('id="decisionCategory"', markup)
        self.assertIn("function openReview(index)", markup)
        self.assertIn("queueRows=d.transactions", markup)
        self.assertIn('data-review-index', markup)
        self.assertNotIn("encodeURIComponent(JSON.stringify(t))", markup)
        self.assertIn("categories.filter", markup)

    def test_desktop_shell_has_onboarding_and_primary_pages(self):
        markup = (Path(__file__).parents[1] / "app" / "static" / "review.html").read_text(encoding="utf-8")
        for page in ("overview", "transactions", "cashbook", "categories", "history", "settings", "help"):
            self.assertIn(f'data-page="{page}"', markup)
        self.assertIn('id="onboarding"', markup)
        self.assertIn("bursar-cashbook-onboarding-complete", markup)
        self.assertIn("No cashbook connected", markup)
