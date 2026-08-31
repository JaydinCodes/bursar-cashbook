"""Write classified transactions into the monthly WCED legacy XLS cashbook."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

MONTH_SHEET_NAMES = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "Aug",
    9: "Sept",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


class WcedExportError(ValueError):
    pass


def _is_real_label(value: str) -> bool:
    if not value:
        return False
    try:
        float(value)
        return False
    except ValueError:
        return True


def pc_column_categories(sheet) -> dict[int, str]:
    categories: dict[int, str] = {}
    last_top: str | None = None

    for column in range(4, sheet.ncols):
        top = str(sheet.cell_value(4, column)).strip() if sheet.nrows > 4 else ""
        sub = str(sheet.cell_value(5, column)).strip() if sheet.nrows > 5 else ""

        if _is_real_label(top):
            last_top = top

        if not last_top:
            continue

        categories[column] = f"{last_top}: {sub}" if _is_real_label(sub) else last_top

    return categories


@dataclass(frozen=True)
class WcedTransaction:
    txn_date: date
    description: str
    amount: Decimal
    direction: str
    category_name: str
    transaction_id: int


@dataclass(frozen=True)
class WcedPlacement:
    transaction_id: int
    txn_date: date
    description: str
    amount: Decimal
    direction: str
    category_name: str
    sheet_name: str
    row: int
    category_column: int


@dataclass(frozen=True)
class WcedExportResult:
    content: bytes
    placements: tuple[WcedPlacement, ...]


def rc_column_categories(sheet) -> dict[str, int]:
    categories: dict[str, int] = {}
    last_top: str | None = None

    for column in range(5, sheet.ncols):
        top = str(sheet.cell_value(4, column)).strip() if sheet.nrows > 4 else ""
        sub = str(sheet.cell_value(5, column)).strip() if sheet.nrows > 5 else ""

        if _is_real_label(top):
            last_top = top

        if not last_top:
            continue

        name = f"{last_top}: {sub}" if _is_real_label(sub) else last_top
        categories.setdefault(name, column)

    return categories


def _first_empty_row(sheet, start_row: int, columns: tuple[int, ...]) -> int:
    for row in range(start_row, sheet.nrows):
        first_cell = str(sheet.cell_value(row, 0)).strip().lower()
        if first_cell.startswith("total "):
            break

        if all(
            not str(sheet.cell_value(row, column)).strip()
            or sheet.cell_value(row, column) == 0
            for column in columns
        ):
            return row

    raise WcedExportError(f"No empty capture rows remain in {sheet.name}.")


def build_wced_cashbook(
    template_path: str | Path,
    transactions: list[WcedTransaction],
) -> WcedExportResult:
    """Populate a WCED template and return the workbook plus exact placements."""
    try:
        import xlrd
        from xlutils.copy import copy as copy_workbook
    except ImportError as exc:
        raise WcedExportError(
            "WCED XLS export requires xlrd and xlutils. Install requirements.txt first."
        ) from exc

    template = Path(template_path)
    if not template.is_file() or template.suffix.lower() != ".xls":
        raise WcedExportError(
            "Set WCED_TEMPLATE_PATH to a valid blank WCED .xls template."
        )

    try:
        source = xlrd.open_workbook(str(template), formatting_info=True)
    except Exception as exc:
        raise WcedExportError("Could not open the configured WCED template.") from exc

    writable = copy_workbook(source)
    next_rows: dict[str, int] = {}
    category_columns: dict[str, dict[str, int]] = {}
    placements: list[WcedPlacement] = []

    for transaction in transactions:
        if transaction.amount <= 0:
            raise WcedExportError(
                f"Transaction {transaction.transaction_id} has a non-positive amount."
            )

        if transaction.direction not in {"debit", "credit"}:
            raise WcedExportError(
                f"Transaction {transaction.transaction_id} has an invalid direction."
            )

        prefix = MONTH_SHEET_NAMES[transaction.txn_date.month]
        suffix = "PC" if transaction.direction == "debit" else "RC"
        sheet_name = f"{prefix} {suffix}"

        try:
            source_sheet = source.sheet_by_name(sheet_name)
        except xlrd.biffh.XLRDError as exc:
            raise WcedExportError(
                f"The template does not contain the required sheet {sheet_name}."
            ) from exc

        if sheet_name not in category_columns:
            if suffix == "PC":
                category_columns[sheet_name] = {
                    name: column
                    for column, name in pc_column_categories(source_sheet).items()
                }
            else:
                category_columns[sheet_name] = rc_column_categories(source_sheet)

        category_column = category_columns[sheet_name].get(transaction.category_name)
        if category_column is None:
            raise WcedExportError(
                f"Category {transaction.category_name!r} is not a column in {sheet_name}. "
                "Choose a WCED category before exporting."
            )

        if sheet_name not in next_rows:
            next_rows[sheet_name] = _first_empty_row(
                source_sheet,
                6 if suffix == "PC" else 7,
                (0, 2, 3) if suffix == "PC" else (0, 3, 4),
            )

        row = next_rows[sheet_name]
        writable_sheet = writable.get_sheet(source.sheet_names().index(sheet_name))
        amount = float(transaction.amount)

        if suffix == "PC":
            writable_sheet.write(row, 0, transaction.txn_date.day)
            writable_sheet.write(row, 2, transaction.description)
            writable_sheet.write(row, 3, amount)
        else:
            writable_sheet.write(row, 0, transaction.txn_date.day)
            writable_sheet.write(row, 3, f"IMPORT/{transaction.transaction_id}")
            writable_sheet.write(row, 4, amount)

        writable_sheet.write(row, category_column, amount)
        placements.append(
            WcedPlacement(
                transaction_id=transaction.transaction_id,
                txn_date=transaction.txn_date,
                description=transaction.description,
                amount=transaction.amount,
                direction=transaction.direction,
                category_name=transaction.category_name,
                sheet_name=sheet_name,
                row=row,
                category_column=category_column,
            )
        )
        next_rows[sheet_name] += 1

    output = BytesIO()
    writable.save(output)
    return WcedExportResult(output.getvalue(), tuple(placements))


def export_wced_cashbook(
    template_path: str | Path,
    transactions: list[WcedTransaction],
) -> bytes:
    """Compatibility wrapper returning only the generated workbook bytes."""
    return build_wced_cashbook(template_path, transactions).content


def _same_money(actual: object, expected: Decimal) -> bool:
    try:
        return abs(Decimal(str(actual)) - expected) <= Decimal("0.005")
    except Exception:
        return False


def validate_wced_cashbook(
    content: bytes,
    placements: tuple[WcedPlacement, ...] | list[WcedPlacement],
) -> None:
    """Re-open the generated workbook and verify every intended cell placement."""
    try:
        import xlrd
    except ImportError as exc:
        raise WcedExportError(
            "WCED XLS validation requires xlrd. Install requirements.txt first."
        ) from exc

    try:
        workbook = xlrd.open_workbook(file_contents=content)
    except Exception as exc:
        raise WcedExportError("Generated WCED workbook could not be reopened for validation.") from exc

    for placement in placements:
        try:
            sheet = workbook.sheet_by_name(placement.sheet_name)
        except Exception as exc:
            raise WcedExportError(
                f"Final export validation failed: missing sheet {placement.sheet_name}."
            ) from exc

        if sheet.cell_value(placement.row, 0) != float(placement.txn_date.day):
            raise WcedExportError(
                f"Final export validation failed for transaction {placement.transaction_id}: date was not written correctly."
            )

        total_column = 3 if placement.direction == "debit" else 4
        if not _same_money(sheet.cell_value(placement.row, total_column), placement.amount):
            raise WcedExportError(
                f"Final export validation failed for transaction {placement.transaction_id}: total amount mismatch."
            )

        if not _same_money(
            sheet.cell_value(placement.row, placement.category_column),
            placement.amount,
        ):
            raise WcedExportError(
                f"Final export validation failed for transaction {placement.transaction_id}: category allocation mismatch."
            )

        if placement.direction == "debit":
            actual_description = str(sheet.cell_value(placement.row, 2))
            if actual_description != placement.description:
                raise WcedExportError(
                    f"Final export validation failed for transaction {placement.transaction_id}: description mismatch."
                )
        else:
            expected_reference = f"IMPORT/{placement.transaction_id}"
            if str(sheet.cell_value(placement.row, 3)) != expected_reference:
                raise WcedExportError(
                    f"Final export validation failed for transaction {placement.transaction_id}: receipt reference mismatch."
                )


def cashbook_target_sheet(txn_date: date, direction: str) -> str:
    if direction not in {"debit", "credit"}:
        raise WcedExportError(f"Unsupported transaction direction: {direction!r}.")
    suffix = "PC" if direction == "debit" else "RC"
    return f"{MONTH_SHEET_NAMES[txn_date.month]} {suffix}"
