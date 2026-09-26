import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Category, Statement, Transaction
from app.review import approve_transaction, correct_transaction, ready_for_cashbook


class ReviewTests(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        Base.metadata.create_all(self.engine)

        self.db = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )()

        self.stationery = Category(
            name="Stationery",
            type="expense",
        )

        self.equipment = Category(
            name="Office Equipment",
            type="expense",
        )

        self.db.add_all([
            self.stationery,
            self.equipment,
        ])
        self.db.flush()

        self.statement = Statement(
            bank="Standard Bank",
            source_filename="statement.csv",
            source_hash="a" * 64,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 1),
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

        self.db.add(self.statement)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_approve_uses_suggested_category(self):
        transaction = Transaction(
            statement_id=self.statement.id,
            fingerprint="b" * 64,
            source_row=2,
            txn_date=date(2026, 8, 1),
            payee_raw="NASHUA",
            payee_normalized="NASHUA",
            balance_after=Decimal("900.00"),
            amount=Decimal("100.00"),
            direction="debit",
            suggested_category_id=self.stationery.id,
            suggestion_confidence=0.95,
            suggestion_method="exact",
            status="pending",
        )

        self.db.add(transaction)
        self.db.commit()

        result = approve_transaction(
            self.db,
            transaction.id,
        )

        self.assertEqual(
            result.category_id,
            self.stationery.id,
        )

        self.assertEqual(
            result.status,
            "approved",
        )

    def test_correct_transaction_changes_category(self):
        transaction = Transaction(
            statement_id=self.statement.id,
            fingerprint="c" * 64,
            source_row=2,
            txn_date=date(2026, 8, 1),
            payee_raw="NASHUA",
            payee_normalized="NASHUA",
            balance_after=Decimal("900.00"),
            amount=Decimal("100.00"),
            direction="debit",
            suggested_category_id=self.stationery.id,
            suggestion_confidence=0.95,
            suggestion_method="exact",
            status="pending",
        )

        self.db.add(transaction)
        self.db.commit()

        result = correct_transaction(
            self.db,
            transaction.id,
            self.equipment.id,
        )

        self.assertEqual(
            result.category_id,
            self.equipment.id,
        )

        self.assertEqual(
            result.learned_category_id,
            self.equipment.id,
        )

        self.assertEqual(
            result.status,
            "corrected",
        )
    def test_correction_teaches_categorizer(self):
        transaction = Transaction(
            statement_id=self.statement.id,
            fingerprint="d" * 64,
            source_row=2,
            txn_date=date(2026, 8, 1),
            payee_raw="NEW SUPPLIER",
            payee_normalized="NEW SUPPLIER",
            balance_after=Decimal("900.00"),
            amount=Decimal("100.00"),
            direction="debit",
            suggested_category_id=self.stationery.id,
            suggestion_confidence=0.50,
            suggestion_method="fuzzy",
            status="pending",
        )

        self.db.add(transaction)
        self.db.commit()

        correct_transaction(
            self.db,
            transaction.id,
            self.equipment.id,
        )

        from app.categorize import categorize

        category_id, confidence, method = categorize(
            "NEW SUPPLIER",
            self.db,
        )

        self.assertEqual(
            category_id,
            self.equipment.id,
        )

        self.assertEqual(
            confidence,
            1.0,
        )

        self.assertEqual(
            method,
            "exact",
        )
    def test_approved_transaction_is_ready_for_cashbook(self):
        transaction = Transaction(
            statement_id=self.statement.id,
            fingerprint="f" * 64,
            source_row=2,
            txn_date=date(2026, 8, 1),
            payee_raw="NASHUA",
            payee_normalized="NASHUA",
            balance_after=Decimal("900.00"),
            amount=Decimal("100.00"),
            direction="debit",
            category_id=self.stationery.id,
            status="approved",
        )

        self.db.add(transaction)
        self.db.commit()

        ready = ready_for_cashbook(self.db)

        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, transaction.id)
    def test_corrected_transaction_is_ready_for_cashbook(self):
        transaction = Transaction(
            statement_id=self.statement.id,
            fingerprint="g" * 64,
            source_row=2,
            txn_date=date(2026, 8, 1),
            payee_raw="NASHUA",
            payee_normalized="NASHUA",
            balance_after=Decimal("900.00"),
            amount=Decimal("100.00"),
            direction="debit",
            category_id=self.equipment.id,
            learned_category_id=self.equipment.id,
            status="corrected",
        )

        self.db.add(transaction)
        self.db.commit()

        ready = ready_for_cashbook(self.db)

        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, transaction.id)
    
    def test_pending_transaction_is_not_ready_for_cashbook(self):
        transaction = Transaction(
            statement_id=self.statement.id,
            fingerprint="e" * 64,
            source_row=2,
            txn_date=date(2026, 8, 1),
            payee_raw="NASHUA",
            payee_normalized="NASHUA",
            balance_after=Decimal("900.00"),
            amount=Decimal("100.00"),
            direction="debit",
            suggested_category_id=self.stationery.id,
            suggestion_confidence=0.95,
            suggestion_method="exact",
            status="pending",
        )

        self.db.add(transaction)
        self.db.commit()

        ready = ready_for_cashbook(self.db)

        self.assertEqual(ready, [])
if __name__ == "__main__":
    unittest.main()