import unittest
from decimal import Decimal

from app.bank_parser import parse_statement
from app.reconciliation import StatementReconciliationError, reconcile_statement


class ReconciliationTests(unittest.TestCase):
    def test_reconciles_ascending_statement(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Vendor,100.00,,900.00\n"
            "02/08/2026,School fees,,250.00,1150.00\n"
        ).encode()

        result = reconcile_statement(
            parse_statement("statement.csv", content, bank="Standard Bank")
        )

        self.assertEqual(result.opening_balance, Decimal("1000.00"))
        self.assertEqual(result.total_debits, Decimal("100.00"))
        self.assertEqual(result.total_credits, Decimal("250.00"))
        self.assertEqual(result.closing_balance, Decimal("1150.00"))
        self.assertEqual(result.difference, Decimal("0.00"))
        self.assertEqual(result.transaction_order, "ascending")

    def test_reconciles_descending_statement(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "02/08/2026,School fees,,250.00,1150.00\n"
            "01/08/2026,Vendor,100.00,,900.00\n"
        ).encode()

        result = reconcile_statement(
            parse_statement("statement.csv", content, bank="Standard Bank")
        )

        self.assertEqual(result.transaction_order, "descending")
        self.assertEqual(result.opening_balance, Decimal("1000.00"))

    def test_rejects_bad_running_balance(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Vendor,100.00,,900.00\n"
            "02/08/2026,School fees,,250.00,1149.00\n"
        ).encode()

        parsed = parse_statement("statement.csv", content, bank="Standard Bank")

        with self.assertRaises(StatementReconciliationError):
            reconcile_statement(parsed)

    def test_rejects_wrong_explicit_closing_balance(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            ",Opening balance,,,1000.00\n"
            "01/08/2026,Vendor,100.00,,900.00\n"
            ",Closing balance,,,899.00\n"
        ).encode()

        parsed = parse_statement("statement.csv", content, bank="Standard Bank")

        with self.assertRaises(StatementReconciliationError):
            reconcile_statement(parsed)


if __name__ == "__main__":
    unittest.main()
