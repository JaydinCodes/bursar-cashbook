"""Strict Standard Bank statement parser for the single-bursar prototype."""

import csv
import io
import re

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from openpyxl import load_workbook

DATE_HEADERS = ("transaction date", "date", "posting date")
DESCRIPTION_HEADERS = (
    "transaction details",
    "transaction description",
    "description",
    "details",
)
DEBIT_HEADERS = ("debit", "debits", "withdrawal", "money out")
CREDIT_HEADERS = ("credit", "credits", "deposit", "money in")
AMOUNT_HEADERS = ("amount", "transaction amount")
BALANCE_HEADERS = ("balance", "running balance", "available balance")
REFERENCE_HEADERS = (
    "reference",
    "transaction reference",
    "beneficiary reference",
)


class StatementParseError(ValueError):
    pass


@dataclass(frozen=True)
class RawTransaction:
    source_row: int
    txn_date: date
    description: str
    amount: Decimal
    direction: str
    balance_after: Decimal
    reference: str | None = None


@dataclass(frozen=True)
class ParsedStatement:
    transactions: list[RawTransaction]
    explicit_opening_balance: Decimal | None = None
    explicit_closing_balance: Decimal | None = None


def _parse_pdf(content: bytes) -> list[list[object]]:
    import pdfplumber

    rows = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()

                for table in tables:
                    for row in table:
                        if row:
                            rows.append(row)
    except Exception as e:
        raise StatementParseError(
            "Could not read the PDF statement"
        )

    if not rows:
        raise StatementParseError(
            "No Transaction table could be extarcted from the pdf statement"
        )

    return rows


PDF_DATE_RE = re.compile(
    r"^(?P<date>\d{2} [A-Za-z]{3} \d{2})\s+"
)

PDF_TRANSACTION_RE = re.compile(
    r"(?P<amount>[+-]?\d[\d,]*\.\d{2})\s+"
    r"(?P<balance>[+-]?\d[\d,]*\.\d{2})$"
)


def _parse_pdf_transaction(
    raw: str,
    source_row: int,
) -> list[object] | None:
    """
    Parse one Standard Bank PDF transaction row.

    Returns:
        [date, description, debit, credit, balance]

    Example:
        [
            "01 Jul 26",
            "WOOLWORTHS 5196*5110 30 JUN DEBIT CARD PURCHASE FROM",
            "1350.00",
            "",
            "6629.34",
        ]
    """
    if not raw:
        return None

    lines = [
        line.strip()
        for line in raw.splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    first_line = lines[0]

    # Transaction rows must start with DD Mon YY.
    date_match = PDF_DATE_RE.match(first_line)

    if not date_match:
        return None

    txn_date = date_match.group("date")
    remainder = first_line[date_match.end():].strip()

    # The final two monetary values are:
    # transaction amount + running balance.
    money_match = PDF_TRANSACTION_RE.search(remainder)

    if not money_match:
        raise StatementParseError(
            f"Could not parse Standard Bank PDF row {source_row}: "
            f"{raw!r}"
        )

    amount_text = money_match.group("amount")
    balance_text = money_match.group("balance")

    description = remainder[:money_match.start()].strip()

    # The second line contains useful transaction-type information.
    if len(lines) > 1:
        detail = " ".join(lines[1:])
        description = f"{description} {detail}".strip()

    amount = Decimal(amount_text.replace(",", ""))
    balance = Decimal(balance_text.replace(",", ""))

    if amount < 0:
        debit = str(abs(amount))
        credit = ""
    else:
        debit = ""
        credit = str(amount)

    return [
        txn_date,
        description,
        debit,
        credit,
        str(balance),
    ]
def _normalize_pdf_rows(
    rows: list[list[object]],
) -> list[list[object]]:
    """
    Convert Standard Bank PDF rows into the structure
    expected by parse_rows().
    """
    normalized: list[list[object]] =  [["Date", "Description", "Debit", "Credit", "Balance"]]

    for source_row, row in enumerate(rows, start=1):
        if not row:
            continue

        raw = str(row[0] or "").strip()

        if not raw:
            continue

        # Ignore PDF table headers.
        if raw.lower() == "date":
            continue

        # Opening balance.
        opening_match = re.search(
            r"STATEMENT OPENING BALANCE\s+"
            r"([+-]?\d[\d,]*\.\d{2})",
            raw,
            re.IGNORECASE,
        )

        if opening_match:
            normalized.append(
                [
                    "",
                    "STATEMENT OPENING BALANCE",
                    "",
                    "",
                    opening_match.group(1).replace(",", ""),
                ]
            )
            continue

        # Ignore summary rows such as:
        # Payments -R26,073.80
        # Deposits R26,595.57
        if raw.lower().startswith(("payments ", "deposits ")):
            continue

        transaction = _parse_pdf_transaction(
            raw,
            source_row,
        )

        if transaction is not None:
            normalized.append(transaction)

    return normalized 
    


def _header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _column_index(
    headers: list[str],
    accepted: tuple[str, ...],
    *,
    required: bool = False,
) -> int | None:
    for name in accepted:
        if name in headers:
            return headers.index(name)

    if required:
        raise StatementParseError(
            f"Missing required column. Expected one of: {', '.join(accepted)}"
        )

    return None


def _cell(row: list[object], index: int | None) -> object:
    if index is None or index >= len(row):
        return ""
    return row[index]


def _find_header_row(rows: list[list[object]]) -> int:
    for index, row in enumerate(rows[:25]):
        headers = {_header(cell) for cell in row}
        has_date = bool(headers.intersection(DATE_HEADERS))
        has_description = bool(headers.intersection(DESCRIPTION_HEADERS))
        has_balance = bool(headers.intersection(BALANCE_HEADERS))
        has_movement = bool(
            headers.intersection(DEBIT_HEADERS + CREDIT_HEADERS + AMOUNT_HEADERS)
        )

        if has_date and has_description and has_balance and has_movement:
            return index

    raise StatementParseError(
        "Could not identify a supported Standard Bank statement header. "
        "Expected date, description, balance and debit/credit or amount columns."
    )


def _parse_amount(value: object) -> Decimal | None:
    if value is None:
        return None

    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))

    raw = str(value).strip()
    if not raw or raw in {"-", "--"}:
        return None

    parentheses_negative = raw.startswith("(") and raw.endswith(")")
    cleaned = re.sub(r"[^0-9,.\-]", "", raw)

    if not cleaned:
        return None

    if cleaned.count("-") > 1 or ("-" in cleaned and not cleaned.startswith("-")):
        raise StatementParseError(f"Invalid amount: {raw!r}")

    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", cleaned):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")

    if cleaned.count(".") > 1:
        raise StatementParseError(f"Invalid amount: {raw!r}")

    try:
        result = Decimal(cleaned)
    except InvalidOperation as exc:
        raise StatementParseError(f"Invalid amount: {raw!r}") from exc

    if parentheses_negative:
        result = -abs(result)

    return result.quantize(Decimal("0.01"))


def _excel_serial_to_date(value: float, datemode: int) -> date:
    # Excel's 1900 date system contains the historical fake 1900-02-29 day.
    base = datetime(1899, 12, 30) if datemode == 0 else datetime(1904, 1, 1)
    return (base + timedelta(days=float(value))).date()


def _parse_date(value: object, *, excel_datemode: int = 0) -> date:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, (int, float)) and value > 0:
        return _excel_serial_to_date(
            float(value),
            excel_datemode,
        )

    raw = str(value).strip()

    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%y",
        "%d %b %Y",
        "%d %B %Y",
        "%d %b %y",       # Standard Bank PDF: 30 Jun 26
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    raise StatementParseError(
        f"Invalid transaction date: {raw!r}"
    )




def parse_rows(
    rows: list[list[object]],
    *,
    excel_datemode: int = 0,
) -> ParsedStatement:
    header_row = _find_header_row(rows)
    headers = [_header(cell) for cell in rows[header_row]]

    date_col = _column_index(headers, DATE_HEADERS, required=True)
    description_col = _column_index(headers, DESCRIPTION_HEADERS, required=True)
    debit_col = _column_index(headers, DEBIT_HEADERS)
    credit_col = _column_index(headers, CREDIT_HEADERS)
    amount_col = _column_index(headers, AMOUNT_HEADERS)
    balance_col = _column_index(headers, BALANCE_HEADERS, required=True)
    reference_col = _column_index(headers, REFERENCE_HEADERS)

    if debit_col is None and credit_col is None and amount_col is None:
        raise StatementParseError(
            "Standard Bank statement has no debit, credit, or signed amount column."
        )

    transactions: list[RawTransaction] = []
    explicit_opening_balance: Decimal | None = None
    explicit_closing_balance: Decimal | None = None

    for row_index, row in enumerate(rows[header_row + 1 :], start=header_row + 2):
        if not any(str(cell or "").strip() for cell in row):
            continue

        description = str(_cell(row, description_col)).strip()
        if not description:
            continue

        debit = _parse_amount(_cell(row, debit_col))
        credit = _parse_amount(_cell(row, credit_col))
        signed_amount = _parse_amount(_cell(row, amount_col))
        balance = _parse_amount(_cell(row, balance_col))

        description_lower = description.lower()
        no_movement = all(
            value is None or value == 0 for value in (debit, credit, signed_amount)
        )

        if "opening balance" in description_lower and balance is not None and no_movement:
            explicit_opening_balance = balance
            continue

        if "closing balance" in description_lower and balance is not None and no_movement:
            explicit_closing_balance = balance
            continue

        debit_present = debit is not None and debit != 0
        credit_present = credit is not None and credit != 0

        if debit_present and credit_present:
            raise StatementParseError(
                f"Row {row_index} contains both a debit and credit. Import cancelled."
            )

        if debit_present and debit < 0:
            raise StatementParseError(
                f"Row {row_index} contains a negative debit. "
                "This reversal needs manual investigation."
            )

        if credit_present and credit < 0:
            raise StatementParseError(
                f"Row {row_index} contains a negative credit. "
                "This reversal needs manual investigation."
            )

        if debit_present:
            direction = "debit"
            amount = debit
        elif credit_present:
            direction = "credit"
            amount = credit
        elif signed_amount is not None and signed_amount != 0:
            if signed_amount < 0:
                direction = "debit"
                amount = abs(signed_amount)
            else:
                direction = "credit"
                amount = signed_amount
        else:
            # Footer/summary rows with no movement are ignored.
            continue

        if balance is None:
            raise StatementParseError(
                f"Row {row_index} contains a transaction but has no running balance."
            )

        txn_date = _parse_date(_cell(row, date_col), excel_datemode=excel_datemode)
        reference_raw = str(_cell(row, reference_col)).strip()

        transactions.append(
            RawTransaction(
                source_row=row_index,
                txn_date=txn_date,
                description=description,
                amount=amount.quantize(Decimal("0.01")),
                direction=direction,
                balance_after=balance.quantize(Decimal("0.01")),
                reference=reference_raw or None,
            )
        )

    if not transactions:
        raise StatementParseError(
            "No transactions were found in the Standard Bank statement."
        )

    return ParsedStatement(
        transactions=transactions,
        explicit_opening_balance=explicit_opening_balance,
        explicit_closing_balance=explicit_closing_balance,
    )


def parse_statement(
    filename: str,
    content: bytes,
    bank: str | None = None,
) -> ParsedStatement:
    normalized_bank = (bank or "Standard Bank").strip().lower()
    if normalized_bank not in {"standard bank", "standard"}:
        raise StatementParseError(
            "This prototype currently supports Standard Bank only."
        )

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    excel_datemode = 0

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

    elif extension == "xlsx":
        try:
            workbook = load_workbook(
                io.BytesIO(content),
                read_only=True,
                data_only=True,
            )
        except Exception as exc:
            raise StatementParseError("Could not read the XLSX statement.") from exc

        try:
            sheet = workbook.active
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
        finally:
            workbook.close()

    elif extension == "xls":
        try:
            import xlrd
        except ImportError as exc:
            raise StatementParseError(
                "Legacy XLS support requires xlrd. Install requirements.txt first."
            ) from exc

        try:
            book = xlrd.open_workbook(file_contents=content)
        except Exception as exc:
            raise StatementParseError("Could not read the XLS statement.") from exc

        excel_datemode = book.datemode
        sheet = book.sheet_by_index(0)
        rows = [sheet.row_values(index) for index in range(sheet.nrows)]
    elif extension == "pdf":
        rows = _parse_pdf(content)
        rows = _normalize_pdf_rows(rows)
        return parse_rows(rows)
    else:
        raise StatementParseError(
            "Upload a Standard Bank CSV, XLS, PDF, or XLSX statement."
        )

    return parse_rows(rows, excel_datemode=excel_datemode)
