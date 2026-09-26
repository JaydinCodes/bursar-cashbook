import unittest
from pathlib import Path

from app.bank_parser import parse_statement


PDF_PATH = Path("data/bankstatement.pdf")


class TestRealStandardBankPdf(unittest.TestCase):

    def test_parse_real_pdf(self):
        content = PDF_PATH.read_bytes()

        statement = parse_statement(
            filename="standard_bank_statement.pdf",
            content=content,
        )

        print(f"\nTransactions: {len(statement.transactions)}")
        print(f"Opening balance: {statement.explicit_opening_balance}")
        print(f"Closing balance: {statement.explicit_closing_balance}")

        self.assertGreater(len(statement.transactions), 0)
        self.assertIsNotNone(statement.explicit_opening_balance)

        for transaction in statement.transactions[:5]:
            print(transaction)


if __name__ == "__main__":
    unittest.main()