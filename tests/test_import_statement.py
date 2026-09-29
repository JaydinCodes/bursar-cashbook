import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.import_statement import import_statement
from app.models import Base, Category, Transaction


class ImportStatementTests(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        Base.metadata.create_all(self.engine)

        self.db = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_import_creates_statement_and_transactions(self):
        category = Category(
            name="Stationery",
            type="expense",
        )

        self.db.add(category)
        self.db.commit()

        content = (
            b"Date,Description,Debit,Credit,Balance\n"
            b"01/08/2026,STATIONERY SHOP,100.00,,900.00\n"
            b"02/08/2026,CLIENT PAYMENT,,500.00,1400.00\n"
        )

        statement = import_statement(
            self.db,
            filename="statement.csv",
            content=content,
        )

        self.assertIsNotNone(statement.id)

        self.assertEqual(
            statement.source_filename,
            "statement.csv",
        )

        self.assertEqual(
            statement.reconciliation_status,
            "passed",
        )

        self.assertEqual(
            statement.opening_balance,
            Decimal("1000.00"),
        )

        self.assertEqual(
            statement.closing_balance,
            Decimal("1400.00"),
        )

        self.assertEqual(
            statement.total_debits,
            Decimal("100.00"),
        )

        self.assertEqual(
            statement.total_credits,
            Decimal("500.00"),
        )

        self.assertEqual(
            statement.imported_transaction_count,
            2,
        )

        transactions = (
            self.db.query(Transaction)
            .filter(Transaction.statement_id == statement.id)
            .all()
        )

        self.assertEqual(len(transactions), 2)

        for transaction in transactions:
            self.assertEqual(
                transaction.status,
                "pending"
            )

    def test_duplicate_statement_is_rejected(self):
        category = Category(
            name="Stationery",
            type="expense",
        )

        self.db.add(category)
        self.db.commit()

        content = (
            b"Date,Description,Debit,Credit,Balance\n"
            b"01/08/2026,STATIONERY SHOP,100.00,,900.00\n"
            b"02/08/2026,CLIENT PAYMENT,,500.00,1400.00\n"
        )

        import_statement(
            self.db,
            filename="statement.csv",
            content=content,
        )

        with self.assertRaisesRegex(
            ValueError,
            "already been imported",
        ):
            import_statement(
                self.db,
                filename="statement.csv",
                content=content,
            )

    def test_overlapping_statement_imports_only_new_transactions(self):
        self.db.add_all([
            Category(name="Stationery", type="expense"),
            Category(name="Income", type="income"),
        ])
        self.db.commit()
        first = (
            b"Date,Description,Debit,Credit,Balance\n"
            b"01/08/2026,STATIONERY SHOP,100.00,,900.00\n"
            b"02/08/2026,CLIENT PAYMENT,,500.00,1400.00\n"
        )
        overlapping = (
            b"Date,Description,Debit,Credit,Balance\n"
            b"02/08/2026,CLIENT PAYMENT,,500.00,1400.00\n"
            b"03/08/2026,SECOND PAYMENT,,200.00,1600.00\n"
        )
        import_statement(self.db, filename="first.csv", content=first)
        second = import_statement(self.db, filename="overlap.csv", content=overlapping)
        self.assertEqual(second.imported_transaction_count, 1)
        self.assertEqual(second.duplicate_transaction_count, 1)
        self.assertEqual(self.db.query(Transaction).count(), 3)

if __name__ == "__main__":
    unittest.main()
