import unittest

from app.bank_parser import _parse_pdf_transaction

class TestPdfTransactionParser(unittest.TestCase):

    def test_debit(self):
        raw = (
            "01 Jul 26 WOOLWORTHS 5196*5110 30 JUN -1,350.00 6,629.34\n"
            "DEBIT CARD PURCHASE FROM"
        )

        result = _parse_pdf_transaction(raw, 1)

        self.assertEqual(result[0], "01 Jul 26")
        self.assertEqual(result[1], "WOOLWORTHS 5196*5110 30 JUN DEBIT CARD PURCHASE FROM")
        self.assertEqual(result[2], "1350.00")
        self.assertEqual(result[3], "")
        self.assertEqual(result[4], "6629.34")

    def test_credit(self):
        raw = (
            "04 Jul 26 VIBES 1,555.00 6,886.06\n"
            "PAYSHAP PAYMENT FROM"
        )

        result = _parse_pdf_transaction(raw, 2)

        self.assertEqual(result[0], "04 Jul 26")
        self.assertEqual(result[1], "VIBES PAYSHAP PAYMENT FROM")
        self.assertEqual(result[2], "")
        self.assertEqual(result[3], "1555.00")
        self.assertEqual(result[4], "6886.06")

    def test_refund(self):
        raw = (
            "M*APPLE.COM/BI5196120422965110 14.03 1,427.20\n"
            "DEBIT CARD REFUND FROM"
        )

        # This one intentionally lacks the date and should not parse.
        result = _parse_pdf_transaction(raw, 3)

        self.assertIsNone(result)

    def test_negative_balance(self):
        raw = (
            "01 Jul 26 SOME PAYMENT -100.00 -21.36\n"
            "DEBIT CARD PURCHASE FROM"
        )

        result = _parse_pdf_transaction(raw, 4)

        self.assertEqual(result[2], "100.00")
        self.assertEqual(result[3], "")
        self.assertEqual(result[4], "-21.36")


if __name__ == "__main__":
    unittest.main()