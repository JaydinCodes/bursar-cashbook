import importlib.util
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.wced_export import WcedTransaction, export_wced_cashbook


@unittest.skipUnless(
    importlib.util.find_spec("xlrd") is not None
    and importlib.util.find_spec("xlutils") is not None,
    "xlrd/xlutils not installed",
)
class WcedExportTests(unittest.TestCase):
    template = Path(__file__).parents[1] / "data" / "2020_cashbook.xls"

    def setUp(self):
        if not self.template.exists():
            self.skipTest("Historical WCED test workbook is not present in this patch folder")

    def test_writes_debit_to_payment_sheet_and_category_column(self):
        import xlrd

        content = export_wced_cashbook(
            self.template,
            [
                WcedTransaction(
                    txn_date=date(2020, 1, 31),
                    description="TEST SUPPLIER",
                    amount=Decimal("125.50"),
                    direction="debit",
                    category_name="Nashua",
                    transaction_id=900,
                )
            ],
        )

        workbook = xlrd.open_workbook(file_contents=content)
        sheet = workbook.sheet_by_name("Jan PC")
        row = 46
        self.assertEqual(sheet.cell_value(row, 0), 31.0)
        self.assertEqual(sheet.cell_value(row, 2), "TEST SUPPLIER")
        self.assertEqual(sheet.cell_value(row, 3), 125.50)
        self.assertEqual(sheet.cell_value(row, 44), 125.50)

    def test_writes_credit_to_receipts_sheet_and_category_column(self):
        import xlrd

        content = export_wced_cashbook(
            self.template,
            [
                WcedTransaction(
                    txn_date=date(2020, 1, 31),
                    description="TEST RECEIPT",
                    amount=Decimal("250.00"),
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


if __name__ == "__main__":
    unittest.main()
