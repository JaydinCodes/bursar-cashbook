"""Write reviewed transactions into the monthly WCED cashbook sheets."""
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path

import xlrd
from xlutils.copy import copy as copy_workbook

MONTH_SHEET_NAMES = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "Aug", 9: "Sept", 10: "Oct", 11: "Nov", 12: "Dec",
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
    categories = {}
    last_top = None
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
    amount: float
    direction: str
    category_name: str
    transaction_id: int


def rc_column_categories(sheet) -> dict[str, int]:
    """Derive receipt-category names from the two-tier RC headers."""
    categories = {}
    last_top = None
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
        if all(not str(sheet.cell_value(row, column)).strip() or sheet.cell_value(row, column) == 0 for column in columns):
            return row
    raise WcedExportError(f"No empty capture rows remain in {sheet.name}.")


def export_wced_cashbook(template_path: str | Path, transactions: list[WcedTransaction]) -> bytes:
    """Return a legacy XLS copy populated with explicitly reviewed entries."""
    template = Path(template_path)
    if not template.is_file() or template.suffix.lower() != ".xls":
        raise WcedExportError("Set WCED_TEMPLATE_PATH to a valid blank WCED .xls template.")

    source = xlrd.open_workbook(str(template), formatting_info=True)
    writable = copy_workbook(source)
    next_rows: dict[str, int] = {}
    category_columns: dict[str, dict[str, int]] = {}

    for transaction in transactions:
        prefix = MONTH_SHEET_NAMES[transaction.txn_date.month]
        suffix = "PC" if transaction.direction == "debit" else "RC"
        sheet_name = f"{prefix} {suffix}"
        try:
            source_sheet = source.sheet_by_name(sheet_name)
        except xlrd.biffh.XLRDError as exc:
            raise WcedExportError(f"The template does not contain the required sheet {sheet_name}.") from exc

        if sheet_name not in category_columns:
            if suffix == "PC":
                category_columns[sheet_name] = {name: column for column, name in pc_column_categories(source_sheet).items()}
            else:
                category_columns[sheet_name] = rc_column_categories(source_sheet)
        category_column = category_columns[sheet_name].get(transaction.category_name)
        if category_column is None:
            raise WcedExportError(
                f"Category {transaction.category_name!r} is not a column in {sheet_name}. "
                "Choose a WCED category before exporting."
            )

        if sheet_name not in next_rows:
            # PC columns: Day, Cheque, Details, Total. RC: Day, receipt range,
            # deposit reference, Total. A zero in a preformatted row is blank.
            next_rows[sheet_name] = _first_empty_row(
                source_sheet,
                6 if suffix == "PC" else 7,
                (0, 2, 3) if suffix == "PC" else (0, 3, 4),
            )
        row = next_rows[sheet_name]
        writable_sheet = writable.get_sheet(source.sheet_names().index(sheet_name))
        if suffix == "PC":
            writable_sheet.write(row, 0, transaction.txn_date.day)
            writable_sheet.write(row, 2, transaction.description)
            writable_sheet.write(row, 3, transaction.amount)
        else:
            writable_sheet.write(row, 0, transaction.txn_date.day)
            writable_sheet.write(row, 3, f"IMPORT/{transaction.transaction_id}")
            writable_sheet.write(row, 4, transaction.amount)
        writable_sheet.write(row, category_column, transaction.amount)
        next_rows[sheet_name] += 1

    output = BytesIO()
    writable.save(output)
    return output.getvalue()
