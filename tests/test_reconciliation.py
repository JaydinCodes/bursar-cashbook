import unittest
from pathlib import Path

from app.bank_parser import parse_statement
from app.reconciliation import reconcile_statement


PDF_PATH = Path("data/bankstatement.pdf")


class TestRealStandardBankPdfReconciliation(unittest.TestCase):

    def test_real_pdf_reconciles(self):
        if not PDF_PATH.is_file():
            self.skipTest("Private local PDF is intentionally not a repository fixture")
        content = PDF_PATH.read_bytes()

        statement = parse_statement(
            filename="standard_bank_statement.pdf",
            content=content,
        )

        result = reconcile_statement(statement)

        self.assertEqual(
            result.difference,
            0,
        )


if __name__ == "__main__":
    unittest.main()
