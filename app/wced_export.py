"""Write classified transactions into the monthly WCED legacy XLS cashbook."""
from __future__ import annotations

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


@dataclass(frozen=True)
class WcedSheetLayout:
    """Explicit, validated capture mapping for one monthly PC/RC sheet."""
    sheet_name: str
    direction: str
    start_row: int
    date_column: int
    payee_column: int | None
    reference_column: int | None
    total_column: int
    category_columns: dict[str, int]

    @property
    def occupancy_columns(self) -> tuple[int, ...]:
        return tuple(column for column in (self.date_column, self.payee_column, self.reference_column, self.total_column) if column is not None)


def discover_sheet_layout(sheet, direction: str) -> WcedSheetLayout:
    """Discover a supported WCED monthly capture sheet or fail closed."""
    if direction not in {"debit", "credit"}:
        raise WcedExportError(f"Unsupported transaction direction: {direction!r}.")
    labels = [str(sheet.cell_value(4, column)).strip().upper() for column in range(sheet.ncols)]
    def required(label: str) -> int:
        matches = [column for column, value in enumerate(labels) if value == label]
        if len(matches) != 1:
            friendly = "Total Payments" if direction == "debit" and label == "TOTAL AMOUNT" else label.title()
            raise WcedExportError(f"Could not safely determine the {friendly} column in {sheet.name}.")
        return matches[0]
    date_column = required("DAY")
    total_column = required("TOTAL AMOUNT")
    if direction == "debit":
        payee_column = required("DETAILS")
        categories = {name: column for column, name in pc_column_categories(sheet).items()}
        return WcedSheetLayout(sheet.name, direction, 6, date_column, payee_column, None, total_column, categories)
    reference_column = required("DEPOSIT NUMBER")
    # The receipt cashbook's "From" field is the appropriate payee field.
    from_columns = [column for column, value in enumerate(labels) if value == "RECEIPT NUMBERS"]
    payee_column = from_columns[0] if len(from_columns) == 1 else None
    categories = rc_column_categories(sheet)
    return WcedSheetLayout(sheet.name, direction, 7, date_column, payee_column, reference_column, total_column, categories)


def first_empty_capture_row(sheet, layout: WcedSheetLayout) -> int:
    for row in range(layout.start_row, sheet.nrows):
        first_cell = str(sheet.cell_value(row, layout.date_column)).strip().lower()
        if first_cell.startswith("total "):
            break
        if all(not str(sheet.cell_value(row, column)).strip() or sheet.cell_value(row, column) == 0 for column in layout.occupancy_columns):
            return row
    raise WcedExportError(f"No empty capture rows remain in {sheet.name}.")


def build_cashbook_placement(transaction, category_name: str, layout: WcedSheetLayout, row: int) -> WcedPlacement:
    """Pure, observable placement plan used before a workbook is written."""
    category_column = layout.category_columns.get(category_name)
    if category_column is None:
        raise WcedExportError(f"{category_name} is not available in {layout.sheet_name}. Review category mapping.")
    return WcedPlacement(
        transaction_id=transaction.id,
        txn_date=transaction.txn_date,
        description=transaction.payee_raw,
        amount=transaction.amount,
        direction=transaction.direction,
        category_name=category_name,
        sheet_name=layout.sheet_name,
        row=row,
        category_column=category_column,
        date_column=layout.date_column,
        payee_column=layout.payee_column,
        reference_column=layout.reference_column,
        total_column=layout.total_column,
        reference=transaction.reference,
    )


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
    date_column: int = 0
    payee_column: int | None = None
    reference_column: int | None = None
    total_column: int = 0
    reference: str | None = None


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
            writable_sheet.write(row, 1, transaction.description)
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
                date_column=0,
                payee_column=2 if suffix == "PC" else 1,
                reference_column=3 if suffix == "RC" else None,
                total_column=3 if suffix == "PC" else 4,
                reference=None,
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
    *,
    expected_sheet_names: tuple[str, ...] | list[str] | None = None,
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

    if expected_sheet_names is not None and tuple(workbook.sheet_names()) != tuple(expected_sheet_names):
        raise WcedExportError("Final export validation failed: workbook sheet structure changed.")

    for placement in placements:
        try:
            sheet = workbook.sheet_by_name(placement.sheet_name)
        except Exception as exc:
            raise WcedExportError(
                f"Final export validation failed: missing sheet {placement.sheet_name}."
            ) from exc

        if sheet.cell_value(placement.row, placement.date_column) != float(placement.txn_date.day):
            raise WcedExportError(
                f"Final export validation failed for transaction {placement.transaction_id}: date was not written correctly."
            )

        if not _same_money(sheet.cell_value(placement.row, placement.total_column), placement.amount):
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

        if placement.payee_column is not None:
            actual_description = str(sheet.cell_value(placement.row, placement.payee_column))
            if actual_description != placement.description:
                raise WcedExportError(
                    f"Final export validation failed for transaction {placement.transaction_id}: description mismatch."
                )
        if placement.reference_column is not None and placement.reference:
            if str(sheet.cell_value(placement.row, placement.reference_column)) != placement.reference:
                raise WcedExportError(
                    f"Final export validation failed for transaction {placement.transaction_id}: receipt reference mismatch."
                )


def cashbook_target_sheet(txn_date: date, direction: str) -> str:
    if direction not in {"debit", "credit"}:
        raise WcedExportError(f"Unsupported transaction direction: {direction!r}.")
    suffix = "PC" if direction == "debit" else "RC"
    return f"{MONTH_SHEET_NAMES[txn_date.month]} {suffix}"
