import unittest
from pathlib import Path

from app.bank_parser import parse_statement


PDF_PATH = Path("data/bankstatement.pdf")


class TestRealStandardBankPdf(unittest.TestCase):

    def test_parse_real_pdf(self):
        if not PDF_PATH.is_file():
            self.skipTest("Private local PDF is intentionally not a repository fixture")
        content = PDF_PATH.read_bytes()

        statement = parse_statement(
            filename="standard_bank_statement.pdf",
            content=content,
        )

        self.assertGreater(len(statement.transactions), 0)
        self.assertIsNotNone(statement.explicit_opening_balance)


if __name__ == "__main__":
    unittest.main()
