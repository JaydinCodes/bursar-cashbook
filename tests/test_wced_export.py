import unittest
from datetime import date
from io import BytesIO
from pathlib import Path

import xlrd

from app.wced_export import WcedTransaction, export_wced_cashbook


class WcedExportTests(unittest.TestCase):
    template = Path(__file__).parents[1] / "data" / "2020_cashbook.xls"

    def test_writes_debit_to_monthly_payment_sheet_and_category_column(self):
        content = export_wced_cashbook(
            self.template,
            [
                WcedTransaction(
                    txn_date=date(2020, 1, 31),
                    description="TEST SUPPLIER",
                    amount=125.50,
                    direction="debit",
                    category_name="Nashua",
                    transaction_id=900,
                )
            ],
        )
        workbook = xlrd.open_workbook(file_contents=content)
        sheet = workbook.sheet_by_name("Jan PC")
        row = 46  # First unused transaction row in this historical reference workbook.
        self.assertEqual(sheet.cell_value(row, 0), 31.0)
        self.assertEqual(sheet.cell_value(row, 2), "TEST SUPPLIER")
        self.assertEqual(sheet.cell_value(row, 3), 125.50)
        self.assertEqual(sheet.cell_value(row, 44), 125.50)

    def test_writes_credit_to_monthly_receipts_sheet_and_category_column(self):
        content = export_wced_cashbook(
            self.template,
            [
                WcedTransaction(
                    txn_date=date(2020, 1, 31),
                    description="TEST RECEIPT",
                    amount=250.00,
                    direction="credit",
                    category_name="Money Market",
                    transaction_id=901,
                )
            ],
        )
        workbook = xlrd.open_workbook(file_contents=content)
        sheet = workbook.sheet_by_name("Jan RC")
        row = 17
        self.assertEqual(sheet.cell_value(row, 0), 31.0)
        self.assertEqual(sheet.cell_value(row, 3), "IMPORT/901")
        self.assertEqual(sheet.cell_value(row, 4), 250.00)
        self.assertEqual(sheet.cell_value(row, 26), 250.00)
