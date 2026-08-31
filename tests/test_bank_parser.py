import unittest
from decimal import Decimal

from app.bank_parser import StatementParseError, parse_statement


class BankParserTests(unittest.TestCase):
    def test_parses_standard_bank_debit_and_credit(self):
        content = (
            "Transaction Date,Transaction Details,Debits,Credits,Balance,Reference\n"
            "01/08/2026,Nashua,120.50,,879.50,A1\n"
            "02/08/2026,School fees,,250.00,1129.50,A2\n"
        ).encode()

        parsed = parse_statement("statement.csv", content, bank="Standard Bank")

        self.assertEqual(len(parsed.transactions), 2)
        self.assertEqual(parsed.transactions[0].direction, "debit")
        self.assertEqual(parsed.transactions[0].amount, Decimal("120.50"))
        self.assertEqual(parsed.transactions[1].direction, "credit")
        self.assertEqual(parsed.transactions[1].balance_after, Decimal("1129.50"))

    def test_parses_signed_amount(self):
        content = (
            "Date,Details,Amount,Balance\n"
            "2026-08-01,Vendor,-42.25,957.75\n"
        ).encode()

        parsed = parse_statement("statement.csv", content, bank="Standard Bank")
        transaction = parsed.transactions[0]

        self.assertEqual(transaction.direction, "debit")
        self.assertEqual(transaction.amount, Decimal("42.25"))

    def test_rejects_non_standard_bank(self):
        content = (
            "Date,Description,Amount,Balance\n"
            "2026-08-01,Vendor,-10.00,90.00\n"
        ).encode()

        with self.assertRaises(StatementParseError):
            parse_statement("statement.csv", content, bank="FNB")

    def test_rejects_both_debit_and_credit(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Impossible,100.00,50.00,950.00\n"
        ).encode()

        with self.assertRaises(StatementParseError):
            parse_statement("statement.csv", content, bank="Standard Bank")

    def test_rejects_negative_debit_column_value(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Reversal,-100.00,,1100.00\n"
        ).encode()

        with self.assertRaises(StatementParseError):
            parse_statement("statement.csv", content, bank="Standard Bank")

    def test_supports_decimal_comma(self):
        content = (
            "Date;Details;Amount;Balance\n"
            "01/08/2026;Vendor;-42,25;957,75\n"
        ).encode()

        parsed = parse_statement("statement.csv", content, bank="Standard Bank")
        self.assertEqual(parsed.transactions[0].amount, Decimal("42.25"))

    def test_extracts_explicit_opening_and_closing_balance(self):
        content = (
            "Date,Details,Debit,Credit,Balance\n"
            ",Opening balance,,,1000.00\n"
            "01/08/2026,Vendor,100.00,,900.00\n"
            ",Closing balance,,,900.00\n"
        ).encode()

        parsed = parse_statement("statement.csv", content, bank="Standard Bank")
        self.assertEqual(parsed.explicit_opening_balance, Decimal("1000.00"))
        self.assertEqual(parsed.explicit_closing_balance, Decimal("900.00"))


if __name__ == "__main__":
    unittest.main()
