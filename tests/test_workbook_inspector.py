import unittest
from pathlib import Path

from app.workbook_inspector import column_letter, inspect_workbook


class WorkbookInspectorTests(unittest.TestCase):
    fixture = Path(__file__).parents[1] / "data" / "2020_cashbook.xls"

    def setUp(self):
        if not self.fixture.exists(): self.skipTest("sanitized workbook fixture unavailable")
        self.report = inspect_workbook(self.fixture)

    def test_fixture_discovers_every_monthly_ledger_without_mutating(self):
        ledgers = [item for item in self.report["sheets"] if item["ledger"]]
        self.assertEqual(len(ledgers), 24)
        self.assertEqual(column_letter(15), "P")
        self.assertTrue(self.report["schema_fingerprint"])

    def test_hierarchical_categories_and_capture_ranges_are_reported(self):
        august = next(item for item in self.report["sheets"] if item["sheet_name"] == "Aug PC")
        self.assertEqual(august["ledger"], {"month": 8, "type": "payment"})
        self.assertEqual(august["fields"]["date"]["column"], 0)
        self.assertEqual(august["fields"]["total_amount"]["column"], 3)
        self.assertTrue(august["categories"])
        self.assertTrue(all(item["column_letter"] for item in august["categories"]))

    def test_fingerprint_ignores_transaction_values(self):
        first = self.report["schema_fingerprint"]
        second = inspect_workbook(self.fixture)["schema_fingerprint"]
        self.assertEqual(first, second)
