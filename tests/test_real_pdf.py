import unittest
from pathlib import Path

from app.bank_parser import _parse_pdf, _normalize_pdf_rows


PDF_PATH = Path("data/bankstatement.pdf")


class TestRealStandardBankPdf(unittest.TestCase):

    def test_real_pdf(self):
        content = PDF_PATH.read_bytes()

        raw_rows = _parse_pdf(content)

        print(f"\nRaw rows: {len(raw_rows)}")

        normalized_rows = _normalize_pdf_rows(raw_rows)

        print(f"Normalized rows: {len(normalized_rows)}")

        self.assertGreater(len(raw_rows), 0)
        self.assertGreater(len(normalized_rows), 0)

        for row in normalized_rows[:5]:
            print(row)


if __name__ == "__main__":
    unittest.main()