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
    adopt_existing_cashbook_row,
    cashbook_status,
    register_cashbook,
    sync_live_cashbook,
    undo_latest_sync,
)
from app.models import Base, CashbookSync, CashbookSyncBatch, Category, Statement, Transaction


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

    def _cashbook_categories(self):
        import xlrd
        from app.wced_export import pc_column_categories
        source = xlrd.open_workbook(str(self.historical), formatting_info=True)
        names = list(dict.fromkeys(pc_column_categories(source.sheet_by_name("Jan PC")).values()))
        first = Category(name=names[0], type="expense")
        second = Category(name=names[1], type="expense")
        self.db.add_all([first, second]); self.db.flush()
        return first, second

    def _transaction(self, category, fingerprint, day=31, amount=Decimal("100.00")):
        statement = Statement(
            bank="Standard Bank", source_filename=f"statement-{fingerprint}.csv", source_hash=fingerprint * 64,
            period_start=date(2020, 1, day), period_end=date(2020, 1, day), financial_year=2020,
            opening_balance=Decimal("1000.00"), closing_balance=Decimal("900.00"), total_debits=Decimal("100.00"),
            total_credits=Decimal("0.00"), reconciliation_difference=Decimal("0.00"), reconciliation_status="passed",
            source_transaction_count=1, imported_transaction_count=1, duplicate_transaction_count=0,
        )
        self.db.add(statement); self.db.flush()
        transaction = Transaction(
            statement_id=statement.id, fingerprint=fingerprint * 64, source_row=2, txn_date=date(2020, 1, day),
            payee_raw=f"SUPPLIER {fingerprint}", payee_normalized=f"SUPPLIER {fingerprint}",
            balance_after=Decimal("900.00"), amount=amount, direction="debit",
            category_id=category.id, status="corrected",
        )
        self.db.add(transaction); self.db.commit()
        return transaction

    def _registered_cashbook(self, root):
        live_path, backup_dir = root / "active-cashbook.xls", root / "cashbook_backups"
        return patch.object(cashbook_sync, "DEFAULT_ACTIVE_CASHBOOK", live_path), patch.object(
            cashbook_sync, "CASHBOOK_BACKUP_DIR", backup_dir
        )

    def _historical_row_workbook(self, *, direction, category, day, amount,
                                 narrative="HISTORICAL ENTRY", reference="HIST-REF"):
        """Return a real XLS with a manually populated WCED capture row."""
        import xlrd
        from io import BytesIO
        from xlutils.copy import copy as copy_workbook
        from app.wced_export import cashbook_target_sheet, discover_sheet_layout, first_empty_capture_row

        source = xlrd.open_workbook(str(self.historical), formatting_info=True)
        sheet_name = cashbook_target_sheet(date(2020, 1, day), direction)
        sheet = source.sheet_by_name(sheet_name)
        layout = discover_sheet_layout(sheet, direction)
        row = first_empty_capture_row(sheet, layout)
        writable = copy_workbook(source)
        target = writable.get_sheet(source.sheet_names().index(sheet_name))
        target.write(row, layout.date_column, day)
        if layout.payee_column is not None:
            target.write(row, layout.payee_column, narrative)
        if layout.reference_column is not None:
            target.write(row, layout.reference_column, reference)
        target.write(row, layout.total_column, float(amount))
        target.write(row, layout.category_columns[category.name], float(amount))
        output = BytesIO(); writable.save(output)
        return output.getvalue(), sheet_name, row

    def test_historical_row_blocks_append_and_adoption_does_not_mutate_workbook(self):
        first, _ = self._cashbook_categories()
        transaction = self._transaction(first, "h", day=30)
        transaction.amount = Decimal("987654.32")
        transaction.cashbook_narrative = "HISTORICAL ENTRY"
        self.db.commit()
        content, sheet_name, row = self._historical_row_workbook(
            direction="debit", category=first, day=30, amount=transaction.amount,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_path, backup_dir = root / "active-cashbook.xls", root / "backups"
            with patch.object(cashbook_sync, "DEFAULT_ACTIVE_CASHBOOK", live_path), patch.object(
                cashbook_sync, "CASHBOOK_BACKUP_DIR", backup_dir
            ):
                register_cashbook(self.db, source_filename="2020 cashbook.xls", content=content, financial_year=2020)
                before = live_path.read_bytes()
                preview = cashbook_sync.preview_live_cashbook_sync(self.db)
                self.assertFalse(preview["ready"])
                self.assertEqual(preview["historical_matches"][0]["type"], "historical_match")
                self.assertEqual(preview["historical_matches"][0]["sheet_name"], sheet_name)
                blocked = sync_live_cashbook(self.db)
                self.assertEqual(blocked["status"], "historical_match")
                self.assertEqual(live_path.read_bytes(), before)
                adopted = adopt_existing_cashbook_row(
                    self.db, transaction_id=transaction.id, row_index=row,
                )
                self.assertEqual(adopted["status"], "adopted")
                self.assertEqual(live_path.read_bytes(), before)
                self.assertEqual(self.db.query(CashbookSync).count(), 1)
                repeated = sync_live_cashbook(self.db)
                self.assertEqual(repeated["written"], 0)
                self.assertEqual(repeated["already_synced"], 1)

    def test_historical_adoption_revalidates_date_amount_and_category(self):
        first, second = self._cashbook_categories()
        transaction = self._transaction(first, "i", day=29)
        transaction.amount = Decimal("876543.21")
        self.db.commit()
        content, _, row = self._historical_row_workbook(
            direction="debit", category=first, day=29, amount=transaction.amount,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(cashbook_sync, "DEFAULT_ACTIVE_CASHBOOK", root / "active.xls"), patch.object(
                cashbook_sync, "CASHBOOK_BACKUP_DIR", root / "backups"
            ):
                register_cashbook(self.db, source_filename="2020 cashbook.xls", content=content, financial_year=2020)
                transaction.txn_date = date(2020, 1, 28); self.db.commit()
                with self.assertRaisesRegex(cashbook_sync.CashbookSyncError, "different transaction date"):
                    adopt_existing_cashbook_row(self.db, transaction_id=transaction.id, row_index=row)
                transaction.txn_date = date(2020, 1, 29); transaction.amount = Decimal("1.00"); self.db.commit()
                with self.assertRaisesRegex(cashbook_sync.CashbookSyncError, "different total amount"):
                    adopt_existing_cashbook_row(self.db, transaction_id=transaction.id, row_index=row)
                transaction.amount = Decimal("876543.21"); transaction.category_id = second.id; self.db.commit()
                with self.assertRaisesRegex(cashbook_sync.CashbookSyncError, "not allocated"):
                    adopt_existing_cashbook_row(self.db, transaction_id=transaction.id, row_index=row)

    def test_confirmed_profile_year_not_filename_controls_sync(self):
        first, _ = self._cashbook_categories()
        transaction = self._transaction(first, "j", day=28)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(cashbook_sync, "DEFAULT_ACTIVE_CASHBOOK", root / "active.xls"), patch.object(
                cashbook_sync, "CASHBOOK_BACKUP_DIR", root / "backups"
            ):
                with self.assertRaisesRegex(cashbook_sync.CashbookSyncError, "Confirm the cashbook accounting year"):
                    register_cashbook(self.db, source_filename="2020 Cashbook.xls", content=self.historical.read_bytes())
                profile = register_cashbook(
                    self.db, source_filename="Cashbook.xls", content=self.historical.read_bytes(), financial_year=2020,
                )
                self.assertEqual(profile.financial_year, 2020)
                transaction.txn_date = date(2021, 1, 28); self.db.commit()
                with self.assertRaisesRegex(cashbook_sync.CashbookSyncError, "cannot receive transaction"):
                    sync_live_cashbook(self.db)

    def test_sync_then_undo_reverts_only_that_batch(self):
        first, _ = self._cashbook_categories()
        transaction = self._transaction(first, "a")
        with tempfile.TemporaryDirectory() as directory:
            first_patch, backup_patch = self._registered_cashbook(Path(directory))
            with first_patch, backup_patch:
                register_cashbook(self.db, source_filename="2020 cashbook.xls", content=self.historical.read_bytes(), financial_year=2020)
                synced = sync_live_cashbook(self.db)
                self.assertEqual(synced["written"], 1)
                undone = undo_latest_sync(self.db)
                self.assertEqual(undone["transactions_reverted"], 1)
                self.assertEqual(self.db.query(CashbookSync).count(), 0)
                self.assertEqual(cashbook_status(self.db)["needs_sync"], 1)
                self.assertIsNotNone(self.db.get(CashbookSyncBatch, synced["batch_id"]).undone_at)

    def test_two_syncs_undoes_second_without_corrupting_first(self):
        first, _ = self._cashbook_categories()
        # Use values absent from the historical fixture.  A same-day/same-
        # amount row must now correctly stop sync for manual adoption.
        one = self._transaction(first, "a", amount=Decimal("987.61"))
        two = self._transaction(first, "b", amount=Decimal("987.62"))
        with tempfile.TemporaryDirectory() as directory:
            first_patch, backup_patch = self._registered_cashbook(Path(directory))
            with first_patch, backup_patch:
                register_cashbook(self.db, source_filename="2020 cashbook.xls", content=self.historical.read_bytes(), financial_year=2020)
                sync_one = sync_live_cashbook(self.db, transaction_ids=[one.id])
                sync_two = sync_live_cashbook(self.db, transaction_ids=[two.id])
                undo_latest_sync(self.db)
                self.assertIsNotNone(self.db.query(CashbookSync).filter_by(transaction_id=one.id).one_or_none())
                self.assertIsNone(self.db.query(CashbookSync).filter_by(transaction_id=two.id).one_or_none())
                self.assertIsNone(self.db.get(CashbookSyncBatch, sync_one["batch_id"]).undone_at)
                self.assertIsNotNone(self.db.get(CashbookSyncBatch, sync_two["batch_id"]).undone_at)

    def test_correction_undo_restores_prior_sync_ledger(self):
        first, second = self._cashbook_categories()
        transaction = self._transaction(first, "c")
        with tempfile.TemporaryDirectory() as directory:
            first_patch, backup_patch = self._registered_cashbook(Path(directory))
            with first_patch, backup_patch:
                register_cashbook(self.db, source_filename="2020 cashbook.xls", content=self.historical.read_bytes(), financial_year=2020)
                sync_live_cashbook(self.db)
                prior = self.db.query(CashbookSync).filter_by(transaction_id=transaction.id).one()
                prior_column = prior.category_column
                transaction.category_id = second.id; self.db.commit()
                corrected = sync_live_cashbook(self.db, transaction_ids=[transaction.id])
                self.assertEqual(corrected["updated"], 1)
                undo_latest_sync(self.db)
                restored = self.db.query(CashbookSync).filter_by(transaction_id=transaction.id).one()
                self.assertEqual(restored.category_id, first.id)
                self.assertEqual(restored.category_column, prior_column)

    def test_missing_batch_backup_refuses_without_ledger_change(self):
        first, _ = self._cashbook_categories()
        transaction = self._transaction(first, "d")
        with tempfile.TemporaryDirectory() as directory:
            first_patch, backup_patch = self._registered_cashbook(Path(directory))
            with first_patch, backup_patch:
                register_cashbook(self.db, source_filename="2020 cashbook.xls", content=self.historical.read_bytes(), financial_year=2020)
                result = sync_live_cashbook(self.db)
                (Path(directory) / "cashbook_backups" / result["backup"]).unlink()
                with self.assertRaisesRegex(cashbook_sync.CashbookSyncError, "pre-sync backup is missing"):
                    undo_latest_sync(self.db)
                self.assertIsNotNone(self.db.query(CashbookSync).filter_by(transaction_id=transaction.id).one_or_none())

    def test_restart_and_repeated_undo_are_safe(self):
        first, _ = self._cashbook_categories()
        transaction = self._transaction(first, "e")
        with tempfile.TemporaryDirectory() as directory:
            first_patch, backup_patch = self._registered_cashbook(Path(directory))
            with first_patch, backup_patch:
                register_cashbook(self.db, source_filename="2020 cashbook.xls", content=self.historical.read_bytes(), financial_year=2020)
                sync_live_cashbook(self.db)
                # Simulate a restart: discard the session and reopen against the same durable database.
                self.db.close()
                self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
                undo_latest_sync(self.db)
                with self.assertRaisesRegex(cashbook_sync.CashbookSyncError, "no reversible sync"):
                    undo_latest_sync(self.db)
                self.assertIsNone(self.db.query(CashbookSync).filter_by(transaction_id=transaction.id).one_or_none())

    def test_sync_preserves_existing_rows_and_writes_payment_details_and_receipt_from(self):
        import xlrd
        from app.wced_export import discover_sheet_layout, pc_column_categories

        source = xlrd.open_workbook(str(self.historical), formatting_info=True)
        jan_pc = source.sheet_by_name("Jan PC")
        discovered = list(dict.fromkeys(pc_column_categories(jan_pc).values()))
        if len(discovered) < 2:
            self.skipTest("fixture does not expose two payment categories")
        first_name, second_name = discovered[:2]

        first = Category(name=first_name, type="expense")
        second = Category(name=second_name, type="expense")
        jan_rc = source.sheet_by_name("Jan RC")
        receipt_layout = discover_sheet_layout(jan_rc, "credit")
        receipt_name = next(iter(receipt_layout.category_columns))
        receipt = Category(name=receipt_name, type="income")
        self.db.add_all([first, second, receipt])
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
        receipt_transaction = Transaction(
            statement_id=statement.id,
            fingerprint="e" * 64,
            source_row=3,
            txn_date=date(2020, 1, 31),
            payee_raw="PARENT SCHOOL FEE PAYMENT",
            payee_normalized="PARENT SCHOOL FEE PAYMENT",
            reference="CRD/NEW",
            balance_after=Decimal("1000.00"),
            amount=Decimal("125.00"),
            direction="credit",
            category_id=receipt.id,
            status="corrected",
        )
        self.db.add_all([transaction, receipt_transaction])
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
                    financial_year=2020,
                )
                first_sync = sync_live_cashbook(self.db)
                self.assertEqual(first_sync["written"], 2)
                ledger = self.db.query(CashbookSync).filter_by(transaction_id=transaction.id).one()
                receipt_ledger = self.db.query(CashbookSync).filter_by(transaction_id=receipt_transaction.id).one()
                original_row = ledger.row_index
                original_column = ledger.category_column

                second_sync = sync_live_cashbook(self.db)
                self.assertEqual(second_sync["written"], 0)
                self.assertEqual(second_sync["already_synced"], 2)
                self.assertEqual(self.db.query(CashbookSync).count(), 2)

                transaction.category_id = second.id
                transaction.status = "corrected"
                self.db.commit()
                correction = sync_live_cashbook(self.db, transaction_ids=[transaction.id])
                self.assertEqual(correction["updated"], 1)

                ledger = self.db.query(CashbookSync).filter_by(transaction_id=transaction.id).one()
                self.assertEqual(ledger.row_index, original_row)
                self.assertEqual(ledger.category_id, second.id)
                self.assertNotEqual(ledger.category_column, original_column)

                workbook = xlrd.open_workbook(str(live_path))
                sheet = workbook.sheet_by_name("Jan PC")
                self.assertEqual(sheet.cell_value(original_row, 2), "PHASE 6 TEST SUPPLIER")
                self.assertEqual(sheet.cell_value(original_row, 3), 125.0)
                self.assertEqual(sheet.cell_value(original_row, original_column), 0.0)
                self.assertEqual(sheet.cell_value(original_row, ledger.category_column), 125.0)
                receipt_sheet = workbook.sheet_by_name("Jan RC")
                self.assertEqual(receipt_sheet.cell_value(receipt_ledger.row_index, 0), 31.0)
                self.assertEqual(receipt_sheet.cell_value(receipt_ledger.row_index, 1), "PARENT SCHOOL FEE PAYMENT")
                self.assertEqual(receipt_sheet.cell_value(receipt_ledger.row_index, 3), "CRD/NEW")
                self.assertEqual(receipt_sheet.cell_value(receipt_ledger.row_index, 4), 125.0)
                self.assertEqual(
                    receipt_sheet.cell_value(receipt_ledger.row_index, receipt_ledger.category_column),
                    125.0,
                )
                self.assertGreaterEqual(len(list(backup_dir.glob("cashbook-*.xls"))), 2)


if __name__ == "__main__":
    unittest.main()
