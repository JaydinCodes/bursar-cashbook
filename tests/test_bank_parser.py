import unittest
from decimal import Decimal
from pathlib import Path

from app.bank_parser import parse_statement


class BankParserTests(unittest.TestCase):
    fixture_dir = Path(__file__).parents[1] / "fixtures" / "standard_bank"
    def test_parses_common_debit_credit_csv(self):
        content = (
            "Transaction Date,Description,Debit,Credit,Balance\n"
            "01/08/2026,Nashua,120.50,,1000.00\n"
            "02/08/2026,School fees,,250.00,1250.00\n"
        ).encode()

        transactions = parse_statement("statement.csv", content)

        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0].direction, "debit")
        self.assertEqual(transactions[0].amount, Decimal("120.50"))
        self.assertEqual(transactions[1].direction, "credit")

    def test_parses_signed_amount_csv(self):
        content = "Date,Details,Amount\n2026-08-01,Vendor,-42.25\n".encode()

        transaction = parse_statement("statement.csv", content)[0]

        self.assertEqual(transaction.direction, "debit")
        self.assertEqual(transaction.amount, Decimal("42.25"))

    def test_nedbank_profile_accepts_transaction_details_column(self):
        content = "Date,Transaction Details,Debits,Credits\n01/08/2026,Vendor,42.25,\n".encode()

        transaction = parse_statement("statement.csv", content, bank="Nedbank")[0]

        self.assertEqual(transaction.description, "Vendor")

    def test_standard_bank_business_csv_fixture(self):
        transactions = parse_statement(
            "standard_bank_business_csv_v1.csv",
            (self.fixture_dir / "standard_bank_business_csv_v1.csv").read_bytes(),
            bank="Standard Bank",
        )

        self.assertEqual(len(transactions), 4)
        self.assertEqual(transactions[0].direction, "debit")
        self.assertEqual(transactions[1].direction, "credit")

    def test_standard_bank_personal_signed_amount_fixture(self):
        transactions = parse_statement(
            "standard_bank_personal_csv_v1.csv",
            (self.fixture_dir / "standard_bank_personal_csv_v1.csv").read_bytes(),
            bank="Standard Bank",
        )

        self.assertEqual(len(transactions), 4)
        self.assertEqual(transactions[0].amount, Decimal("1890.75"))

    def test_standard_bank_business_xlsx_fixture(self):
        transactions = parse_statement(
            "standard_bank_business_xlsx_v1.xlsx",
            (self.fixture_dir / "standard_bank_business_xlsx_v1.xlsx").read_bytes(),
            bank="Standard Bank",
        )

        self.assertEqual(len(transactions), 4)
        self.assertEqual(transactions[-1].description, "CASH DEPOSIT SPORT FUND")
