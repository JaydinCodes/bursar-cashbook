import unittest
from pathlib import Path


class PublicFixtureTests(unittest.TestCase):
    def test_committed_cashbook_fixture_is_generated_and_non_sensitive(self):
        fixture_dir = Path(__file__).parent / "fixtures"
        fixture = fixture_dir / "synthetic_wced_2020.xls"
        self.assertTrue(fixture.is_file())
        self.assertTrue((fixture_dir / "build_synthetic_wced_cashbook.py").is_file())
        import xlrd
        workbook = xlrd.open_workbook(str(fixture))
        values = [str(sheet.cell_value(row, column)) for sheet in workbook.sheets()
                  for row in range(sheet.nrows) for column in range(sheet.ncols)]
        self.assertTrue(all("@" not in value for value in values))
        self.assertTrue(all("SYNTHETIC" in str(sheet.cell_value(0, 0)) for sheet in workbook.sheets()))
        self.assertFalse((Path(__file__).parents[1] / "data" / "2020_cashbook.xls").exists())
