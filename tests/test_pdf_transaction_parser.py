from app.bank_parser import _normalize_pdf_rows


def test_normalize_pdf_rows(self):
    rows = [
        ["Date", "Description", "Payments", "Deposits", "Balance"],
        [
            "01 Aug 26 CHOLE -1.20 1,517.48\n"
            "FEE: PAYMENT CONFIRM - SMS",
            None,
            None,
            None,
            None,
        ],
        [
            "06 Aug 26 L PETRUS 30.00 863.29\n"
            "PAYSHAP PAYMENT FROM",
            None,
            None,
            None,
            None,
        ],
        [
            "24 Aug 26 S2S*009STANDC 5196*5110 21 AUG "
            "-29.50 -21.36\n"
            "DEBIT CARD PURCHASE FROM",
            None,
            None,
            None,
            None,
        ],
        [
            "01 Sep 26 M*APPLE.COM/BI5196120422965110 "
            "14.03 1,427.20\n"
            "DEBIT CARD REFUND FROM",
            None,
            None,
            None,
            None,
        ],
        [
            "Payments -R26,073.80",
            None,
            None,
            None,
            None,
        ],
    ]

    result = _normalize_pdf_rows(rows)

    self.assertEqual(len(result), 4)

    self.assertEqual(result[0][2], "1.20")
    self.assertEqual(result[0][3], "")

    self.assertEqual(result[1][2], "")
    self.assertEqual(result[1][3], "30.00")

    self.assertEqual(result[2][2], "29.50")
    self.assertEqual(result[2][4], "-21.36")

    self.assertEqual(result[3][2], "")
    self.assertEqual(result[3][3], "14.03")