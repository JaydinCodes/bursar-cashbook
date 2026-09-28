import sys
import types
import unittest
from unittest.mock import patch

from app.bank_parser import _parse_positioned_pdf, parse_rows
from app.reconciliation import reconcile_statement


def word(text, x0, top):
    return {"text": text, "x0": x0, "top": top}


def page(rows):
    class Page:
        def extract_words(self, **_kwargs):
            return [item for row in rows for item in row]

    return Page()


class PositionedPdfParserTests(unittest.TestCase):
    def test_positioned_rows_deduplicate_overlapping_pages_and_reconcile(self):
        header = [
            word("Details", 10, 10), word("Fee", 120, 10), word("Debit", 150, 10),
            word("Credit", 250, 10), word("Date", 350, 10), word("Balance", 400, 10),
        ]
        opening = [
            word("BALANCE", 10, 20), word("BROUGHT", 45, 20), word("FORWARD", 90, 20),
            word("0.00", 150, 20), word("0.00", 250, 20), word("20210101", 350, 20), word("100.00", 400, 20),
        ]
        credit = [
            word("MAGTAPE", 10, 30), word("CREDIT", 60, 30), word("0.00", 150, 30),
            word("25.00", 250, 30), word("20210101", 350, 30), word("125.00", 400, 30),
        ]
        debit = [
            word("PAYMENT", 10, 40), word("-5.00", 180, 40), word("0.00", 250, 40),
            word("20210102", 350, 40), word("120.00", 400, 40),
        ]

        class Pdf:
            pages = [page([header, opening, credit]), page([header, opening, credit, debit])]
            def __enter__(self): return self
            def __exit__(self, *_args): return False

        module = types.SimpleNamespace(open=lambda _stream: Pdf())
        with patch.dict(sys.modules, {"pdfplumber": module}):
            rows = _parse_positioned_pdf(b"synthetic PDF")

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2][1], "MAGTAPE CREDIT")
        parsed = parse_rows(rows)
        reconciliation = reconcile_statement(parsed)
        self.assertEqual(str(reconciliation.opening_balance), "100.00")
        self.assertEqual(str(reconciliation.closing_balance), "120.00")
