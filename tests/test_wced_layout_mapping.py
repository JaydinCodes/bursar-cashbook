import unittest
from datetime import date
from pathlib import Path

from app.wced_export import cashbook_target_sheet, discover_sheet_layout


class WcedLayoutMappingTests(unittest.TestCase):
    def test_every_month_maps_to_its_pc_and_rc_sheet(self):
        months = ("Jan", "Feb", "Mar", "April", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec")
        for number, month in enumerate(months, 1):
            self.assertEqual(cashbook_target_sheet(date(2026, number, 12), "debit"), month + " PC")
            self.assertEqual(cashbook_target_sheet(date(2026, number, 12), "credit"), month + " RC")

    def test_fixture_layouts_are_explicit_and_validated(self):
        try: import xlrd
        except ImportError: self.skipTest("xlrd unavailable")
        workbook = xlrd.open_workbook(str(Path(__file__).parents[1] / "data" / "2020_cashbook.xls"))
        pc = discover_sheet_layout(workbook.sheet_by_name("Aug PC"), "debit")
        rc = discover_sheet_layout(workbook.sheet_by_name("Aug RC"), "credit")
        self.assertEqual((pc.date_column, pc.payee_column, pc.total_column), (0, 2, 3))
        self.assertEqual((rc.date_column, rc.reference_column, rc.total_column), (0, 3, 4))
        self.assertIn("Bank Charges", pc.category_columns)
