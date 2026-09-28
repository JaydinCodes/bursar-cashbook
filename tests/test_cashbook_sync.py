import importlib.util
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import cashbook_sync
from app.cashbook_sync import (
    _cashbook_year,
    _is_ready_for_cashbook,
    cashbook_status,
    register_cashbook,
    sync_live_cashbook,
)
from app.models import Base, CashbookSync, Category, Statement, Transaction


class LiveCashbookStateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_sync_without_registered_cashbook_is_safe_and_non_fatal(self):
        result = sync_live_cashbook(self.db)
        self.assertEqual(result["status"], "not_registered")
        self.assertEqual(result["written"], 0)

    def test_cashbook_year_is_read_from_a_year_labelled_filename(self):
        self.assertEqual(_cashbook_year("2020 Cashbook with WCED template.xls"), 2020)
        self.assertIsNone(_cashbook_year("school-cashbook.xls"))

    def test_current_category_uses_category_id_not_stale_relationship(self):
        first = Category(name="First", type="expense")
        second = Category(name="Second", type="expense")
        self.db.add_all([first, second])
        self.db.flush()

        statement = Statement(
            bank="Standard Bank",
            source_filename="statement.csv",
            source_hash="e" * 64,
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
        self.db.add(statement)
        self.db.flush()

        transaction = Transaction(
            statement_id=statement.id,
            fingerprint="f" * 64,
            source_row=2,
            txn_date=date(2026, 8, 1),
            payee_raw="Vendor",
            payee_normalized="VENDOR",
            balance_after=Decimal("900.00"),
            amount=Decimal("100.00"),
            direction="debit",
            category_id=first.id,
            status="corrected",
        )
        self.db.add(transaction)
        self.db.commit()

        self.assertEqual(transaction.category.id, first.id)
        transaction.category_id = second.id
        self.db.commit()

        resolved = cashbook_sync._current_category(self.db, transaction)
        self.assertEqual(resolved.id, second.id)
        self.assertEqual(resolved.name, "Second")


    def test_cashbook_status_counts_final_transactions_waiting_for_registration(self):
        category = Category(name="Stationery", type="expense")
        self.db.add(category)
        self.db.flush()
        statement = Statement(
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
        self.db.add(statement)
        self.db.flush()
        self.db.add(
            Transaction(
                statement_id=statement.id,
                fingerprint="b" * 64,
                source_row=2,
                txn_date=date(2026, 8, 1),
                payee_raw="Vendor",
                payee_normalized="VENDOR",
                balance_after=Decimal("900.00"),
                amount=Decimal("100.00"),
                direction="debit",
                category_id=category.id,
                status="corrected",
            )
        )
        self.db.commit()

        status = cashbook_status(self.db)
        self.assertFalse(status["registered"])
        self.assertEqual(status["eligible_transactions"], 1)
        self.assertEqual(status["needs_sync"], 1)

    def test_pending_transaction_is_not_ready_for_cashbook(self):
        transaction = Transaction(
            status="pending",
        )

        self.assertFalse(
            _is_ready_for_cashbook(transaction)
        )
    def test_approved_transaction_is_ready_for_cashbook(self):
        transaction = Transaction(
            status="approved",
        )

        self.assertTrue(
            _is_ready_for_cashbook(transaction)
        )
    def test_corrected_transaction_is_ready_for_cashbook(self):
        transaction = Transaction(
            status="corrected",
        )

        self.assertTrue(
            _is_ready_for_cashbook(transaction)
        )

@unittest.skipUnless(
    importlib.util.find_spec("xlrd") is not None
    and importlib.util.find_spec("xlutils") is not None,
    "xlrd/xlutils not installed",
)
class LiveCashbookXlsIntegrationTests(unittest.TestCase):
    historical = Path(__file__).parents[1] / "data" / "2020_cashbook.xls"

    def setUp(self):
        if not self.historical.is_file():
            self.skipTest("historical cashbook fixture is not available")
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_sync_is_idempotent_and_correction_updates_same_row(self):
        import xlrd
        from app.wced_export import pc_column_categories

        source = xlrd.open_workbook(str(self.historical), formatting_info=True)
        jan_pc = source.sheet_by_name("Jan PC")
        discovered = list(dict.fromkeys(pc_column_categories(jan_pc).values()))
        if len(discovered) < 2:
            self.skipTest("fixture does not expose two payment categories")
        first_name, second_name = discovered[:2]

        first = Category(name=first_name, type="expense")
        second = Category(name=second_name, type="expense")
        self.db.add_all([first, second])
        self.db.flush()

        statement = Statement(
            bank="Standard Bank",
            source_filename="statement.csv",
            source_hash="c" * 64,
            period_start=date(2020, 1, 31),
            period_end=date(2020, 1, 31),
            financial_year=2020,
            opening_balance=Decimal("1000.00"),
            closing_balance=Decimal("875.00"),
            total_debits=Decimal("125.00"),
            total_credits=Decimal("0.00"),
            reconciliation_difference=Decimal("0.00"),
            reconciliation_status="passed",
            source_transaction_count=1,
            imported_transaction_count=1,
            duplicate_transaction_count=0,
        )
        self.db.add(statement)
        self.db.flush()
        transaction = Transaction(
            statement_id=statement.id,
            fingerprint="d" * 64,
            source_row=2,
            txn_date=date(2020, 1, 31),
            payee_raw="PHASE 6 TEST SUPPLIER",
            payee_normalized="PHASE 6 TEST SUPPLIER",
            balance_after=Decimal("875.00"),
            amount=Decimal("125.00"),
            direction="debit",
            category_id=first.id,
            status="corrected",
        )
        self.db.add(transaction)
        self.db.commit()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_path = root / "active-cashbook.xls"
            backup_dir = root / "cashbook_backups"
            with patch.object(cashbook_sync, "DEFAULT_ACTIVE_CASHBOOK", live_path), patch.object(
                cashbook_sync, "CASHBOOK_BACKUP_DIR", backup_dir
            ):
                register_cashbook(
                    self.db,
                    source_filename="school-cashbook.xls",
                    content=self.historical.read_bytes(),
                )
                first_sync = sync_live_cashbook(self.db)
                self.assertEqual(first_sync["written"], 1)
                ledger = self.db.query(CashbookSync).one()
                original_row = ledger.row_index
                original_column = ledger.category_column

                second_sync = sync_live_cashbook(self.db)
                self.assertEqual(second_sync["written"], 0)
                self.assertEqual(second_sync["already_synced"], 1)
                self.assertEqual(self.db.query(CashbookSync).count(), 1)

                transaction.category_id = second.id
                transaction.status = "corrected"
                self.db.commit()
                correction = sync_live_cashbook(self.db, transaction_ids=[transaction.id])
                self.assertEqual(correction["updated"], 1)

                ledger = self.db.query(CashbookSync).one()
                self.assertEqual(ledger.row_index, original_row)
                self.assertEqual(ledger.category_id, second.id)
                self.assertNotEqual(ledger.category_column, original_column)

                workbook = xlrd.open_workbook(str(live_path))
                sheet = workbook.sheet_by_name("Jan PC")
                self.assertEqual(sheet.cell_value(original_row, 3), 125.0)
                self.assertEqual(sheet.cell_value(original_row, original_column), 0.0)
                self.assertEqual(sheet.cell_value(original_row, ledger.category_column), 125.0)
                self.assertGreaterEqual(len(list(backup_dir.glob("cashbook-*.xls"))), 2)


if __name__ == "__main__":
    unittest.main()
