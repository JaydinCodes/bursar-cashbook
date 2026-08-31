"""Statement import adapters for common South African bank CSV exports."""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import xlrd
from openpyxl import load_workbook


DATE_HEADERS = ("date", "transaction date", "date of transaction", "posting date")
DESCRIPTION_HEADERS = (
    "description",
    "details",
    "narration",
    "transaction description",
    "payee",
    "beneficiary",
)
DEBIT_HEADERS = ("debit", "withdrawal", "money out", "debits")
CREDIT_HEADERS = ("credit", "deposit", "money in", "credits")
AMOUNT_HEADERS = ("amount", "transaction amount", "value")

# These profiles are intentionally aliases, not brittle fixed layouts.  Banks
# can change exports, so all profiles retain the generic fallback headers.
BANK_PROFILES = {
    "fnb": {"names": ("fnb", "first national bank"), "description": ("transaction description",)},
    "absa": {"names": ("absa",), "description": ("transaction description",)},
    "standard bank": {
        "names": ("standard bank", "standard"),
        "description": ("transaction details", "transaction description"),
    },
    "nedbank": {"names": ("nedbank",), "description": ("transaction details",)},
    "capitec": {"names": ("capitec", "capitec bank"), "description": ("description", "transaction description")},
}


class StatementParseError(ValueError):
    pass


@dataclass(frozen=True)
class RawTransaction:
    txn_date: date
    description: str
    amount: Decimal
    direction: str


def _header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _profile_headers(bank: str | None, kind: str, generic: tuple[str, ...]) -> tuple[str, ...]:
    if not bank:
        return generic
    normalised = bank.strip().lower()
    for profile in BANK_PROFILES.values():
        if normalised in profile["names"]:
            return tuple(profile.get(kind, ())) + generic
    return generic


def _find_header_row(rows: list[list[object]], recognised: set[str]) -> int:
    for index, row in enumerate(rows[:25]):
        if sum(_header(cell) in recognised for cell in row) >= 2:
            return index
    raise StatementParseError(
        "Could not find statement columns. Include a date, description, and amount or debit/credit columns."
    )


def _column_index(headers: list[str], accepted: tuple[str, ...], required: bool = False):
    for accepted_name in accepted:
        if accepted_name in headers:
            return headers.index(accepted_name)
    if required:
        raise StatementParseError(f"Missing required column: one of {', '.join(accepted)}")
    return None


def _cell(row: list[object], index: int | None) -> object:
    return row[index] if index is not None and index < len(row) else ""


def _parse_amount(value: object) -> Decimal | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw in {"-", "--"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = re.sub(r"[^0-9,.-]", "", raw).replace(",", "")
    try:
        result = Decimal(cleaned)
    except InvalidOperation as exc:
        raise StatementParseError(f"Invalid amount: {raw!r}") from exc
    return -abs(result) if negative else result


def _parse_date(value: object) -> date:
    if isinstance(value, (int, float)) and value > 0:
        return xlrd.xldate_as_datetime(value, 0).date()
    raw = str(value).strip()
    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise StatementParseError(f"Invalid transaction date: {raw!r}")


def parse_rows(rows: list[list[object]], bank: str | None = None) -> list[RawTransaction]:
    date_headers = _profile_headers(bank, "date", DATE_HEADERS)
    description_headers = _profile_headers(bank, "description", DESCRIPTION_HEADERS)
    debit_headers = _profile_headers(bank, "debit", DEBIT_HEADERS)
    credit_headers = _profile_headers(bank, "credit", CREDIT_HEADERS)
    amount_headers = _profile_headers(bank, "amount", AMOUNT_HEADERS)
    recognised = set(date_headers + description_headers + debit_headers + credit_headers + amount_headers)
    header_row = _find_header_row(rows, recognised)
    headers = [_header(cell) for cell in rows[header_row]]
    date_col = _column_index(headers, date_headers, required=True)
    description_col = _column_index(headers, description_headers, required=True)
    debit_col = _column_index(headers, debit_headers)
    credit_col = _column_index(headers, credit_headers)
    amount_col = _column_index(headers, amount_headers)
    if amount_col is None and debit_col is None and credit_col is None:
        raise StatementParseError("Missing amount, debit, or credit column.")

    transactions = []
    for row in rows[header_row + 1 :]:
        if not any(str(cell).strip() for cell in row):
            continue
        description = str(_cell(row, description_col)).strip()
        if not description:
            continue
        try:
            txn_date = _parse_date(_cell(row, date_col))
            debit = _parse_amount(_cell(row, debit_col))
            credit = _parse_amount(_cell(row, credit_col))
            amount = _parse_amount(_cell(row, amount_col))
        except StatementParseError:
            # Bank exports often contain totals/footer rows. A malformed row
            # without a description has already been ignored; surfaced rows
            # with a description must be fixed rather than silently imported.
            raise

        if debit is not None and debit != 0:
            direction, amount = "debit", abs(debit)
        elif credit is not None and credit != 0:
            direction, amount = "credit", abs(credit)
        elif amount is not None and amount != 0:
            direction, amount = ("debit", abs(amount)) if amount < 0 else ("credit", amount)
        else:
            continue
        transactions.append(RawTransaction(txn_date, description, amount.quantize(Decimal("0.01")), direction))
    if not transactions:
        raise StatementParseError("No transactions found in the uploaded statement.")
    return transactions


def parse_statement(filename: str, content: bytes, bank: str | None = None) -> list[RawTransaction]:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension == "csv":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect=dialect))
    elif extension == "xls":
        book = xlrd.open_workbook(file_contents=content)
        sheet = book.sheet_by_index(0)
        rows = [sheet.row_values(index) for index in range(sheet.nrows)]
    elif extension == "xlsx":
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    elif extension == "pdf":
        raise StatementParseError("PDF uploads need a bank-specific parser. Export the statement as CSV or XLSX first.")
    else:
        raise StatementParseError("Upload a CSV or .xls bank statement.")
    return parse_rows(rows, bank=bank)
