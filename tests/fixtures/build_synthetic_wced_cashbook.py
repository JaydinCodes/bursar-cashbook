"""Generate the committed, non-sensitive WCED-style XLS fixture.

This deliberately contains only invented labels and empty capture rows.  It is
kept as source so the binary fixture can be independently regenerated.
"""
from pathlib import Path

import xlwt


MONTHS = ("Jan", "Feb", "Mar", "April", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec")
PC_CATEGORIES = ("Bank Charges", "Nashua", "Synthetic Supplies", "Synthetic Transport")
RC_CATEGORIES = ("Money Market", "Synthetic Fees", "Synthetic Donations")


def build(destination: Path) -> None:
    book = xlwt.Workbook()
    heading = xlwt.easyxf("font: bold on; align: horiz center")
    for month in MONTHS:
        pc = book.add_sheet(f"{month} PC")
        pc.write(0, 0, "SYNTHETIC WCED-STYLE CASHBOOK FIXTURE")
        pc.write(4, 0, "DAY", heading)
        pc.write(4, 2, "DETAILS", heading)
        pc.write(4, 3, "TOTAL AMOUNT", heading)
        for column, category in enumerate(PC_CATEGORIES, 4):
            pc.write(4, column, category, heading)
        pc.write(106, 0, "Total Payments", heading)

        rc = book.add_sheet(f"{month} RC")
        rc.write(0, 0, "SYNTHETIC WCED-STYLE CASHBOOK FIXTURE")
        rc.write(4, 0, "DAY", heading)
        rc.write(4, 1, "RECEIPT NUMBERS", heading)
        rc.write(4, 3, "DEPOSIT NUMBER", heading)
        rc.write(4, 4, "TOTAL AMOUNT", heading)
        for column, category in enumerate(RC_CATEGORIES, 5):
            rc.write(4, column, category, heading)
        rc.write(107, 0, "Total Receipts", heading)
    destination.parent.mkdir(parents=True, exist_ok=True)
    book.save(str(destination))


if __name__ == "__main__":
    build(Path(__file__).with_name("synthetic_wced_2020.xls"))
