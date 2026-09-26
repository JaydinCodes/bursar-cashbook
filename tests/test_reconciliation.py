import unittest
from pathlib import Path

from app.bank_parser import parse_statement
from app.reconciliation import reconcile_statement


PDF_PATH = Path("data/bankstatement.pdf")


class TestRealStandardBankPdfReconciliation(unittest.TestCase):

    def test_real_pdf_reconciles(self):
        content = PDF_PATH.read_bytes()

        statement = parse_statement(
            filename="standard_bank_statement.pdf",
            content=content,
        )

        result = reconcile_statement(statement)

        print(f"\nOpening balance: {result.opening_balance}")
        print(f"Total debits: {result.total_debits}")
        print(f"Total credits: {result.total_credits}")
        print(f"Calculated closing: {result.calculated_closing_balance}")
        print(f"Actual closing: {result.closing_balance}")
        print(f"Difference: {result.difference}")
        print(f"Transaction order: {result.transaction_order}")

        self.assertEqual(
            result.difference,
            0,
        )


if __name__ == "__main__":
    unittest.main()