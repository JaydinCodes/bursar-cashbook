import unittest
from pathlib import Path

from app.bank_parser import _parse_pdf, _normalize_pdf_rows


PDF_PATH = Path("data/bankstatement.pdf")


class TestRealStandardBankPdf(unittest.TestCase):

    def test_real_pdf(self):
        if not PDF_PATH.is_file():
            self.skipTest("Private local PDF is intentionally not a repository fixture")
        content = PDF_PATH.read_bytes()

        raw_rows = _parse_pdf(content)

        normalized_rows = _normalize_pdf_rows(raw_rows)

        self.assertGreater(len(raw_rows), 0)
        self.assertGreater(len(normalized_rows), 0)



if __name__ == "__main__":
    unittest.main()
