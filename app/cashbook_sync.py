"""Live cashbook registration and in-place synchronization.

The registered workbook is the accounting destination. Approved transactions
are written into that same file; the application does not generate a separate
cashbook for download.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil

from sqlalchemy.orm import Session

from .config import CASHBOOK_BACKUP_DIR, DEFAULT_ACTIVE_CASHBOOK
from .models import (
    CashbookProfile,
    CashbookSync,
    CashbookSyncBatch,
    CashbookSyncBatchEntry,
    Category,
    Transaction,
)
from .wced_export import (
    WcedExportError,
    WcedPlacement,
    cashbook_target_sheet,
    discover_sheet_layout,
    first_empty_capture_row,
    validate_wced_cashbook,
)
from .workbook_inspector import inspect_workbook

ADAPTER_NAME = "monthly_pc_rc_v1"
FINAL_STATUSES = ("approved", "corrected")
CASHBOOK_BACKUP_RETENTION = 20
CASHBOOK_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


class CashbookSyncError(ValueError):
    pass


def _cashbook_year(source_filename: str) -> int | None:
    match = CASHBOOK_YEAR_RE.search(source_filename)
    return int(match.group(1)) if match else None


def _is_ready_for_cashbook(transaction: Transaction) -> bool:
    return transaction.status in {"approved", "corrected"}

def _require_xls_dependencies():
    try:
        import xlrd
        from xlutils.copy import copy as copy_workbook
    except ImportError as exc:
        raise CashbookSyncError(
            "Live cashbook synchronization requires xlrd and xlutils. "
            "Install requirements.txt first."
        ) from exc
    return xlrd, copy_workbook


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
    raise CashbookSyncError(f"No empty capture rows remain in {sheet.name}.")


def inspect_cashbook(content: bytes) -> dict:
    """Parse and validate the supported cashbook structure.

    Phase 6 supports the existing monthly PC/RC workbook family. Category
    columns are discovered from each workbook rather than hard-coded, allowing
    bursars to have different category layouts within that family. Additional
    workbook families can be added later as adapters.
    """
    xlrd, _ = _require_xls_dependencies()
    try:
        workbook = xlrd.open_workbook(file_contents=content, formatting_info=True)
    except Exception as exc:
        raise CashbookSyncError("The selected cashbook is not a readable legacy .xls workbook.") from exc

    try:
        schema = inspect_workbook(content)
    except Exception as exc:
        raise CashbookSyncError("The selected cashbook structure could not be inspected safely.") from exc
    month_names = (
        "Jan", "Feb", "Mar", "April", "May", "June",
        "July", "Aug", "Sept", "Oct", "Nov", "Dec",
    )
    required = {f"{month} {suffix}" for month in month_names for suffix in ("PC", "RC")}
    missing = sorted(required.difference(workbook.sheet_names()))
    if missing:
        raise CashbookSyncError(
            "This cashbook layout is not supported by the current adapter. "
            f"Missing sheet(s): {', '.join(missing[:6])}"
            + ("…" if len(missing) > 6 else "")
        )

    sheets: dict[str, dict] = {}
    payment_categories: set[str] = set()
    receipt_categories: set[str] = set()

    for sheet_name in sorted(required):
        sheet = workbook.sheet_by_name(sheet_name)
        if sheet_name.endswith(" PC"):
            mapping = discover_sheet_layout(sheet, "debit")
            categories = mapping.category_columns
            payment_categories.update(categories)
        else:
            mapping = discover_sheet_layout(sheet, "credit")
            categories = mapping.category_columns
            receipt_categories.update(categories)

        if not categories:
            raise CashbookSyncError(
                f"Could not discover cashbook category columns in {sheet_name}."
            )

        sheets[sheet_name] = {
            "start_row": mapping.start_row,
            "date_column": mapping.date_column,
            "payee_column": mapping.payee_column,
            "reference_column": mapping.reference_column,
            "total_column": mapping.total_column,
            "category_count": len(categories),
            "categories": categories,
        }

    return {
        "adapter": ADAPTER_NAME,
        "sheet_count": len(required),
        "sheets": sheets,
        "payment_categories": sorted(payment_categories),
        "receipt_categories": sorted(receipt_categories),
        "schema_fingerprint": schema["schema_fingerprint"],
        "schema": schema,
    }


def get_active_cashbook(db: Session) -> CashbookProfile | None:
    return (
        db.query(CashbookProfile)
        .filter(CashbookProfile.active.is_(True))
        .order_by(CashbookProfile.id.desc())
        .first()
    )


def sync_categories_from_layout(db: Session, layout: dict) -> dict:
    """Upsert workbook categories without removing existing learned categories."""
    created = existing = 0
    for category_type, names in (("expense", layout.get("payment_categories", [])),
                                 ("income", layout.get("receipt_categories", []))):
        for raw_name in names:
            name = str(raw_name).strip()
            if not name:
                continue
            if db.query(Category).filter_by(name=name, type=category_type).first() is None:
                db.add(Category(name=name, type=category_type))
                created += 1
            else:
                existing += 1
    db.flush()
    return {"created": created, "existing": existing}


def sync_categories_from_active_cashbook(db: Session) -> dict:
    profile = get_active_cashbook(db)
    if profile is None:
        raise CashbookSyncError("Register a live cashbook before refreshing categories.")
    try:
        layout = json.loads(profile.layout_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CashbookSyncError("The registered cashbook category layout is invalid.") from exc
    result = sync_categories_from_layout(db, layout)
    db.commit()
    return result


def register_cashbook(
    db: Session,
    *,
    source_filename: str,
    content: bytes,
    replace: bool = False,
) -> CashbookProfile:
    layout = inspect_cashbook(content)
    existing = get_active_cashbook(db)

    if existing is not None:
        if not replace:
            raise CashbookSyncError(
                "A live cashbook is already registered. Choose replace only before "
                "transactions have been synchronized into it."
            )
        sync_count = (
            db.query(CashbookSync)
            .filter(CashbookSync.cashbook_profile_id == existing.id)
            .count()
        )
        if sync_count:
            raise CashbookSyncError(
                "This cashbook already contains synchronized transactions. Replacing it "
                "would break the audit trail. Keep using the registered cashbook or restore "
                "a cashbook backup instead."
            )
        existing.active = False

    DEFAULT_ACTIVE_CASHBOOK.parent.mkdir(parents=True, exist_ok=True)
    temporary = DEFAULT_ACTIVE_CASHBOOK.with_suffix(".xls.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(DEFAULT_ACTIVE_CASHBOOK)
    except PermissionError as exc:
        temporary.unlink(missing_ok=True)
        raise CashbookSyncError(
            "The managed cashbook is open in Excel or locked by another program. "
            "Close it, then connect the cashbook again."
        ) from exc

    profile = CashbookProfile(
        name="Active cashbook",
        adapter=layout["adapter"],
        source_filename=source_filename,
        file_path=str(DEFAULT_ACTIVE_CASHBOOK.resolve()),
        file_hash=sha256(content).hexdigest(),
        layout_json=json.dumps(layout, separators=(",", ":"), sort_keys=True),
        active=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def _cashbook_backup(path: Path, reason: str) -> Path:
    CASHBOOK_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    safe_reason = "".join(character if character.isalnum() or character == "-" else "-" for character in reason)
    destination = CASHBOOK_BACKUP_DIR / f"cashbook-{timestamp}-{safe_reason}.xls"
    shutil.copy2(path, destination)

    backups = sorted(
        CASHBOOK_BACKUP_DIR.glob("cashbook-*.xls"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[CASHBOOK_BACKUP_RETENTION:]:
        # A completed sync batch depends on its pre-sync workbook.  Never
        # discard those durable undo points as part of routine housekeeping.
        if "-before-sync" not in stale.name:
            stale.unlink(missing_ok=True)
    return destination


def list_cashbook_backups() -> list[dict]:
    CASHBOOK_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for path in sorted(
        CASHBOOK_BACKUP_DIR.glob("cashbook-*.xls"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        stat = path.stat()
        result.append(
            {
                "filename": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            }
        )
    return result


def undo_latest_sync(db: Session) -> dict:
    """Reverse exactly one completed sync batch, newest first."""
    profile = get_active_cashbook(db)
    if profile is None:
        raise CashbookSyncError("No cashbook is connected.")
    batch = (
        db.query(CashbookSyncBatch)
        .filter(CashbookSyncBatch.cashbook_profile_id == profile.id)
        .filter(CashbookSyncBatch.undone_at.is_(None))
        .order_by(CashbookSyncBatch.completed_at.desc(), CashbookSyncBatch.id.desc())
        .first()
    )
    if batch is None:
        raise CashbookSyncError("There is no reversible sync in this cashbook.")
    backup_path = CASHBOOK_BACKUP_DIR / batch.pre_sync_backup_filename
    if not backup_path.is_file():
        raise CashbookSyncError("The pre-sync backup is missing, so undo was stopped.")
    path = Path(profile.file_path)
    if not path.is_file():
        raise CashbookSyncError("The registered cashbook file is missing. Undo was stopped.")
    if sha256(path.read_bytes()).hexdigest() != batch.post_sync_file_hash:
        raise CashbookSyncError(
            "The cashbook has changed since the last sync. Undo was stopped to protect newer workbook changes."
        )

    safety_backup = _cashbook_backup(path, "before-undo")
    temporary = path.with_suffix(".xls.undo.tmp")
    try:
        shutil.copy2(backup_path, temporary)
        temporary.replace(path)
        for entry in batch.entries:
            if entry.operation == "insert":
                sync = db.get(CashbookSync, entry.resulting_sync_id)
                if sync is None or sync.transaction_id != entry.transaction_id:
                    raise CashbookSyncError("The sync ledger changed after this batch. Undo was stopped.")
                db.delete(sync)
            elif entry.operation == "update":
                sync = db.get(CashbookSync, entry.previous_sync_id)
                if sync is None or sync.transaction_id != entry.transaction_id:
                    raise CashbookSyncError("The sync ledger changed after this batch. Undo was stopped.")
                sync.category_id = entry.previous_category_id
                sync.sheet_name = entry.previous_sheet_name
                sync.row_index = entry.previous_row_index
                sync.category_column = entry.previous_category_column
                sync.backup_filename = entry.previous_backup_filename
                sync.synced_at = entry.previous_synced_at
            else:
                raise CashbookSyncError("The sync batch contains an unknown ledger operation.")
        batch.undone_at = datetime.now().astimezone().replace(tzinfo=None)
        batch.undo_safety_backup_filename = safety_backup.name
        profile.file_hash = sha256(path.read_bytes()).hexdigest()
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            shutil.copy2(safety_backup, temporary)
            temporary.replace(path)
        except OSError as restore_exc:
            raise CashbookSyncError(
                "Undo could not be completed and the workbook could not be restored from its safety backup."
            ) from restore_exc
        if isinstance(exc, CashbookSyncError):
            raise
        raise CashbookSyncError("Undo could not update the sync ledger; the workbook was restored safely.") from exc
    return {"status": "undone", "backup": batch.pre_sync_backup_filename, "transactions_reverted": len(batch.entries), "batch_id": batch.id}


def _same_money(actual: object, expected: Decimal) -> bool:
    try:
        return abs(Decimal(str(actual)) - expected) <= Decimal("0.005")
    except Exception:
        return False


def _validate_existing_synced_row(sheet, sync: CashbookSync, transaction: Transaction, layout) -> None:
    row = sync.row_index
    if row >= sheet.nrows:
        raise CashbookSyncError(
            f"The cashbook row for transaction {transaction.id} no longer exists. "
            "The workbook appears to have been structurally edited."
        )
    if sheet.cell_value(row, layout.date_column) != float(transaction.txn_date.day):
        raise CashbookSyncError(
            f"Transaction {transaction.id} moved in the cashbook. Automatic correction was stopped."
        )
    if not _same_money(sheet.cell_value(row, layout.total_column), transaction.amount):
        raise CashbookSyncError(
            f"Transaction {transaction.id} no longer matches its recorded cashbook row. "
            "Automatic correction was stopped."
        )

def _normalize_cashbook_text(value: object) -> str:
    """
    Normalize workbook/bank text only for comparison.

    We deliberately keep this conservative. Historical matching must never
    decide two transactions are identical purely from fuzzy text.
    """
    text = str(value or "").upper().strip()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def adopt_existing_cashbook_row(
    db: Session,
    *,
    transaction_id: int,
    row_index: int,
) -> dict:
    """
    Tell Ledgerly that an existing workbook row represents a transaction.

    This does NOT write to the workbook.

    It only creates Ledgerly's CashbookSync ledger record after validating
    the row against the current transaction.
    """
    profile = get_active_cashbook(db)

    if profile is None:
        raise CashbookSyncError(
            "No cashbook is connected. Connect a cashbook first."
        )

    transaction = db.get(Transaction, transaction_id)

    if transaction is None:
        raise CashbookSyncError(
            f"Transaction {transaction_id} does not exist."
        )

    if not _is_ready_for_cashbook(transaction):
        raise CashbookSyncError(
            "Only approved or corrected transactions can be linked "
            "to an existing cashbook row."
        )

    existing_sync = (
        db.query(CashbookSync)
        .filter(
            CashbookSync.transaction_id == transaction.id,
            CashbookSync.cashbook_profile_id == profile.id,
        )
        .one_or_none()
    )

    if existing_sync is not None:
        raise CashbookSyncError(
            "This transaction is already linked to the cashbook."
        )

    category = _current_category(db, transaction)

    xlrd, _ = _require_xls_dependencies()
    path = Path(profile.file_path)

    if not path.is_file():
        raise CashbookSyncError(
            "The registered cashbook file is missing."
        )

    try:
        workbook = xlrd.open_workbook(
            str(path),
            formatting_info=True,
        )
    except Exception as exc:
        raise CashbookSyncError(
            "The registered cashbook could not be opened."
        ) from exc

    sheet_name = cashbook_target_sheet(
        transaction.txn_date,
        transaction.direction,
    )

    try:
        sheet = workbook.sheet_by_name(sheet_name)
    except Exception as exc:
        raise CashbookSyncError(
            f"The cashbook does not contain {sheet_name}."
        ) from exc

    try:
        layout = discover_sheet_layout(
            sheet,
            transaction.direction,
        )
    except WcedExportError as exc:
        raise CashbookSyncError(str(exc)) from exc

    if row_index < layout.start_row or row_index >= sheet.nrows:
        raise CashbookSyncError(
            "The selected cashbook row is outside the transaction area."
        )

    category_column = layout.category_columns.get(category.name)

    if category_column is None:
        raise CashbookSyncError(
            f"Category {category.name!r} does not exist in {sheet_name}."
        )

    # 1. Day must match.
    day_value = sheet.cell_value(
        row_index,
        layout.date_column,
    )

    try:
        existing_day = int(float(day_value))
    except (TypeError, ValueError) as exc:
        raise CashbookSyncError(
            "The selected row does not contain a valid transaction day."
        ) from exc

    if existing_day != transaction.txn_date.day:
        raise CashbookSyncError(
            "The selected cashbook row has a different transaction date."
        )

    # 2. Total amount must match.
    if not _same_money(
        sheet.cell_value(row_index, layout.total_column),
        transaction.amount,
    ):
        raise CashbookSyncError(
            "The selected cashbook row has a different total amount."
        )

    # 3. Selected transaction's category must already contain the amount.
    #
    # This protects against linking a transaction to the wrong existing row.
    if not _same_money(
        sheet.cell_value(row_index, category_column),
        transaction.amount,
    ):
        raise CashbookSyncError(
            "The selected row is not allocated to the transaction's "
            "current category. Review the row or transaction category first."
        )

    sync = CashbookSync(
        transaction_id=transaction.id,
        cashbook_profile_id=profile.id,
        category_id=category.id,
        sheet_name=sheet_name,
        row_index=row_index,
        category_column=category_column,
        # No Ledgerly write occurred, therefore there is no before-sync backup.
        backup_filename=None,
    )

    db.add(sync)
    db.commit()
    db.refresh(sync)

    return {
        "status": "adopted",
        "transaction_id": transaction.id,
        "cashbook_sync_id": sync.id,
        "sheet_name": sheet_name,
        "row_index": row_index,
        "excel_row": row_index + 1,
        "message": (
            "Existing cashbook row linked successfully. "
            "Ledgerly will not append this transaction again."
        ),
    }

def _historical_cashbook_matches(
    sheet,
    layout,
    transaction: Transaction,
    category: Category,
) -> list[dict]:
    """
    Find rows already present in the workbook that could represent transaction.

    A candidate requires:
        - same monthly PC/RC sheet (already guaranteed by caller)
        - same day
        - same total amount

    Category, narrative and reference are supporting evidence only.

    IMPORTANT:
    This function never automatically claims that a candidate is the same
    transaction. It only detects possible duplicates.
    """
    matches: list[dict] = []

    category_column = layout.category_columns.get(category.name)
    if category_column is None:
        return matches

    wanted_narrative = _normalize_cashbook_text(
        transaction.cashbook_narrative or transaction.payee_raw
    )
    wanted_reference = _normalize_cashbook_text(transaction.reference)

    for row in range(layout.start_row, sheet.nrows):
        day_value = sheet.cell_value(row, layout.date_column)

        # Stop once the capture section reaches the monthly total row.
        if str(day_value).strip().lower().startswith("total "):
            break

        try:
            existing_day = int(float(day_value))
        except (TypeError, ValueError):
            continue

        if existing_day != transaction.txn_date.day:
            continue

        existing_total = sheet.cell_value(row, layout.total_column)

        if not _same_money(existing_total, transaction.amount):
            continue

        existing_narrative = ""
        if layout.payee_column is not None:
            existing_narrative = _normalize_cashbook_text(
                sheet.cell_value(row, layout.payee_column)
            )

        existing_reference = ""
        if layout.reference_column is not None:
            existing_reference = _normalize_cashbook_text(
                sheet.cell_value(row, layout.reference_column)
            )

        category_matches = _same_money(
            sheet.cell_value(row, category_column),
            transaction.amount,
        )

        narrative_matches = bool(
            wanted_narrative
            and existing_narrative
            and wanted_narrative == existing_narrative
        )

        reference_matches = bool(
            wanted_reference
            and existing_reference
            and wanted_reference == existing_reference
        )

        # Same day + amount always deserves review.
        #
        # Strong means we have additional evidence, but even a strong match
        # should be confirmed by the bursar rather than silently adopted.
        strong = (
            category_matches
            and (narrative_matches or reference_matches)
        )

        matches.append(
            {
                "row_index": row,
                # Human-friendly Excel row number.
                "excel_row": row + 1,
                "day": existing_day,
                "amount": f"{Decimal(str(existing_total)):.2f}",
                "narrative": existing_narrative,
                "reference": existing_reference or None,
                "category_matches": category_matches,
                "narrative_matches": narrative_matches,
                "reference_matches": reference_matches,
                "strength": "strong" if strong else "possible",
            }
        )

    return matches

def _current_category(db: Session, transaction: Transaction) -> Category:
    """Resolve the transaction's current category from its FK, not a cached relationship.

    SessionLocal uses expire_on_commit=False, so transaction.category can remain
    cached after category_id changes. Cashbook placement must always follow the
    persisted/current foreign key.
    """
    if transaction.category_id is None:
        raise CashbookSyncError(
            f"Transaction {transaction.id} is final but has no cashbook category."
        )

    category = db.get(Category, transaction.category_id)
    if category is None:
        raise CashbookSyncError(
            f"Transaction {transaction.id} references missing category "
            f"{transaction.category_id}."
        )
    return category


def _write_new_transaction(writable_sheet, row: int, transaction: Transaction, layout) -> None:
    amount = float(transaction.amount)
    writable_sheet.write(row, layout.date_column, transaction.txn_date.day)
    if layout.payee_column is not None:
        writable_sheet.write(row, layout.payee_column, transaction.cashbook_narrative or transaction.payee_raw)
    # A blank reference is intentionally left blank; no synthetic IMPORT/id is used.
    if layout.reference_column is not None and transaction.reference:
        writable_sheet.write(row, layout.reference_column, transaction.reference)
    writable_sheet.write(row, layout.total_column, amount)


def _eligible_transactions(db: Session, transaction_ids: list[int] | None) -> list[Transaction]:
    query = (
        db.query(Transaction)
        .join(Transaction.category)
        .filter(Transaction.status.in_(FINAL_STATUSES))
    )
    if transaction_ids is not None:
        if not transaction_ids:
            return []
        query = query.filter(Transaction.id.in_(transaction_ids))
    return query.order_by(Transaction.txn_date, Transaction.id).all()


def preview_live_cashbook_sync(db: Session) -> dict:
    """Build a non-mutating, fail-closed final sync plan."""
    profile = get_active_cashbook(db)
    if profile is None:
        raise CashbookSyncError("No cashbook is connected. Connect a cashbook first.")
    xlrd, _ = _require_xls_dependencies()
    try:
        source = xlrd.open_workbook(str(profile.file_path), formatting_info=True)
    except Exception as exc:
        raise CashbookSyncError("The registered cashbook could not be opened.") from exc
    rows, blocked, historical_matches, next_rows = [], [], [], {}
    synced_ids = {
        row[0]
        for row in db.query(CashbookSync.transaction_id)
        .filter(CashbookSync.cashbook_profile_id == profile.id)
        .all()
    }
    for transaction in _eligible_transactions(db, None):
        try:
            category = _current_category(db, transaction)
            sheet_name = cashbook_target_sheet(transaction.txn_date, transaction.direction)
            sheet = source.sheet_by_name(sheet_name)
            layout = discover_sheet_layout(sheet, transaction.direction)
            if category.name not in layout.category_columns:
                raise CashbookSyncError(f"{category.name} is not available in {sheet_name}. Review category mapping.")
            # A transaction Ledgerly has never written must never be appended
            # over a possible manually-entered historical transaction.  The
            # match is deliberately returned for a bursar to decide on; it is
            # never treated as proof of duplication.
            if transaction.id not in synced_ids:
                matches = _historical_cashbook_matches(
                    sheet, layout, transaction, category
                )
                if matches:
                    historical_matches.append({
                        "type": "historical_match",
                        "transaction_id": transaction.id,
                        "sheet_name": sheet_name,
                        "matches": matches,
                    })
                    continue
            if sheet_name not in next_rows:
                next_rows[sheet_name] = first_empty_capture_row(sheet, layout)
            rows.append({"transaction_id": transaction.id, "date": transaction.txn_date.isoformat(),
                         "payee": transaction.payee_raw, "amount": str(transaction.amount),
                         "direction": transaction.direction, "sheet_name": sheet_name,
                         "category": category.name, "row_index": next_rows[sheet_name],
                         "cashbook_narrative": transaction.cashbook_narrative or transaction.payee_raw,
                         "narrative_field": "Details" if transaction.direction == "debit" else "From (Receipt Numbers)"})
            next_rows[sheet_name] += 1
        except (CashbookSyncError, WcedExportError, Exception) as exc:
            blocked.append({"transaction_id": transaction.id, "reason": str(exc)})
    summary: dict[str, dict] = {}
    for row in rows:
        item = summary.setdefault(row["sheet_name"], {"sheet_name": row["sheet_name"], "transaction_count": 0, "amount": Decimal("0")})
        item["transaction_count"] += 1; item["amount"] += Decimal(row["amount"])
    return {"ready": not blocked and not historical_matches, "transactions": rows,
            "blocked": blocked, "historical_matches": historical_matches,
            "summary": [{**value, "amount": f'{value["amount"]:.2f}'} for value in summary.values()]}


def sync_live_cashbook(
    db: Session,
    *,
    transaction_ids: list[int] | None = None,
) -> dict:
    """Synchronize final classifications into the registered workbook in place.

    New final transactions are appended once. If a previously synchronized
    transaction is later corrected, its existing row is reallocated rather
    than appended again.
    """
    profile = get_active_cashbook(db)
    if profile is None:
        return {
            "status": "not_registered",
            "message": "Register the bursar's live cashbook before synchronization.",
            "written": 0,
            "updated": 0,
            "already_synced": 0,
        }

    xlrd, copy_workbook = _require_xls_dependencies()
    path = Path(profile.file_path)
    if not path.is_file():
        raise CashbookSyncError(
            "The registered cashbook file is missing. Restore it from a cashbook backup."
        )

    try:
        stored_layout = json.loads(profile.layout_json)
        current_schema = inspect_workbook(path)
    except Exception as exc:
        raise CashbookSyncError("The registered cashbook structure could not be inspected safely.") from exc
    if stored_layout.get("schema_fingerprint") and current_schema["schema_fingerprint"] != stored_layout["schema_fingerprint"]:
        raise CashbookSyncError(
            "The cashbook structure has changed since it was connected. Review the workbook mapping before syncing."
        )

    transactions = _eligible_transactions(db, transaction_ids)
    if not transactions:
        return {
            "status": "up_to_date",
            "message": "There are no approved transactions waiting for the cashbook.",
            "written": 0,
            "updated": 0,
            "already_synced": 0,
        }

    cashbook_year = _cashbook_year(profile.source_filename)
    statement_years = sorted({transaction.txn_date.year for transaction in transactions})
    if cashbook_year is not None and statement_years != [cashbook_year]:
        years = ", ".join(str(year) for year in statement_years)
        raise CashbookSyncError(
            f"This is a {cashbook_year} cashbook and cannot receive transaction(s) "
            f"from {years}. Connect the matching-year cashbook before synchronizing."
        )

    try:
        source = xlrd.open_workbook(str(path), formatting_info=True)
    except Exception as exc:
        raise CashbookSyncError("The registered cashbook could not be opened.") from exc

    writable = copy_workbook(source)
    sync_rows = {
        row.transaction_id: row
        for row in (
            db.query(CashbookSync)
            .filter(CashbookSync.cashbook_profile_id == profile.id)
            .filter(CashbookSync.transaction_id.in_([item.id for item in transactions]))
            .all()
        )
    }

    # Preflight before creating an xlutils copy, a backup, or a replacement
    # file.  This is intentionally separate from the normal sync loop so one
    # unresolved possible duplicate blocks the *entire* request atomically.
    historical_matches: list[dict] = []
    for transaction in transactions:
        if transaction.id in sync_rows:
            continue
        category = _current_category(db, transaction)
        sheet_name = cashbook_target_sheet(transaction.txn_date, transaction.direction)
        try:
            source_sheet = source.sheet_by_name(sheet_name)
            layout = discover_sheet_layout(source_sheet, transaction.direction)
        except WcedExportError as exc:
            raise CashbookSyncError(str(exc)) from exc
        except Exception as exc:
            raise CashbookSyncError(f"The registered cashbook is missing {sheet_name}.") from exc
        matches = _historical_cashbook_matches(source_sheet, layout, transaction, category)
        if matches:
            historical_matches.append({
                "type": "historical_match",
                "transaction_id": transaction.id,
                "sheet_name": sheet_name,
                "matches": matches,
            })
    if historical_matches:
        return {
            "status": "historical_match",
            "message": (
                "Possible existing cashbook rows were found. Select and confirm "
                "'Already in cashbook' for the matching row, or resolve the "
                "difference before syncing."
            ),
            "written": 0,
            "updated": 0,
            "already_synced": 0,
            "historical_matches": historical_matches,
        }

    next_rows: dict[str, int] = {}
    placements: list[WcedPlacement] = []
    new_syncs: list[tuple[Transaction, str, int, int]] = []
    updated_syncs: list[tuple[CashbookSync, int]] = []
    written = 0
    updated = 0
    already_synced = 0

    for transaction in transactions:
        if not _is_ready_for_cashbook(transaction):
            raise CashbookSyncError(
                f"Transaction {transaction.id} is not approved for cashbook sync."
            )

        category = _current_category(db, transaction)

        sheet_name = cashbook_target_sheet(transaction.txn_date, transaction.direction)
        try:
            source_sheet = source.sheet_by_name(sheet_name)
        except Exception as exc:
            raise CashbookSyncError(f"The registered cashbook is missing {sheet_name}.") from exc

        try:
            layout = discover_sheet_layout(source_sheet, transaction.direction)
        except WcedExportError as exc:
            raise CashbookSyncError(str(exc)) from exc
        category_column = layout.category_columns.get(category.name)
        if category_column is None:
            raise CashbookSyncError(
                f"Category {category.name!r} is not present in {sheet_name}. "
                "Review the category mapping before syncing."
            )

        writable_sheet = writable.get_sheet(source.sheet_names().index(sheet_name))
        existing = sync_rows.get(transaction.id)

        if existing is not None:
            if existing.sheet_name != sheet_name:
                raise CashbookSyncError(
                    f"Transaction {transaction.id} would move to another cashbook sheet. "
                    "Automatic synchronization was stopped."
                )
            _validate_existing_synced_row(source_sheet, existing, transaction, layout)
            if existing.category_id == transaction.category_id:
                already_synced += 1
                continue

            if existing.category_column != category_column:
                writable_sheet.write(existing.row_index, existing.category_column, 0)
            writable_sheet.write(existing.row_index, category_column, float(transaction.amount))
            placements.append(
                WcedPlacement(
                    transaction_id=transaction.id,
                    txn_date=transaction.txn_date,
                    description=transaction.cashbook_narrative or transaction.payee_raw,
                    amount=transaction.amount,
                    direction=transaction.direction,
                    category_name=category.name,
                    sheet_name=sheet_name,
                    row=existing.row_index,
                    category_column=category_column,
                    date_column=layout.date_column,
                    payee_column=layout.payee_column,
                    reference_column=layout.reference_column,
                    total_column=layout.total_column,
                    reference=transaction.reference,
                )
            )
            updated_syncs.append((existing, category_column))
            updated += 1
            continue

        if sheet_name not in next_rows:
            try:
                next_rows[sheet_name] = first_empty_capture_row(source_sheet, layout)
            except WcedExportError as exc:
                raise CashbookSyncError(str(exc)) from exc

        row = next_rows[sheet_name]
        _write_new_transaction(writable_sheet, row, transaction, layout)
        writable_sheet.write(row, category_column, float(transaction.amount))
        placements.append(
            WcedPlacement(
                transaction_id=transaction.id,
                txn_date=transaction.txn_date,
                description=transaction.cashbook_narrative or transaction.payee_raw,
                amount=transaction.amount,
                direction=transaction.direction,
                category_name=category.name,
                sheet_name=sheet_name,
                row=row,
                category_column=category_column,
                date_column=layout.date_column,
                payee_column=layout.payee_column,
                reference_column=layout.reference_column,
                total_column=layout.total_column,
                reference=transaction.reference,
            )
        )
        new_syncs.append((transaction, sheet_name, row, category_column))
        next_rows[sheet_name] += 1
        written += 1

    if not placements:
        return {
            "status": "up_to_date",
            "message": "The registered cashbook is already up to date.",
            "written": 0,
            "updated": 0,
            "already_synced": already_synced,
        }

    from io import BytesIO

    output = BytesIO()
    writable.save(output)
    content = output.getvalue()
    try:
        validate_wced_cashbook(content, placements, expected_sheet_names=source.sheet_names())
    except WcedExportError as exc:
        raise CashbookSyncError(str(exc)) from exc

    pre_sync_file_hash = sha256(path.read_bytes()).hexdigest()
    backup_path = _cashbook_backup(path, "before-sync")
    temporary = path.with_suffix(".xls.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    except PermissionError as exc:
        temporary.unlink(missing_ok=True)
        raise CashbookSyncError(
            "The live cashbook is open in Excel or locked by another program. "
            "Close the workbook and click Sync cashbook again."
        ) from exc
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CashbookSyncError("The live cashbook could not be updated safely.") from exc

    try:
        batch = CashbookSyncBatch(
            cashbook_profile_id=profile.id,
            pre_sync_backup_filename=backup_path.name,
            pre_sync_file_hash=pre_sync_file_hash,
            post_sync_file_hash=sha256(content).hexdigest(),
        )
        db.add(batch)
        db.flush()
        batch_entries: list[CashbookSyncBatchEntry] = []
        for transaction, sheet_name, row, category_column in new_syncs:
            sync = CashbookSync(
                transaction_id=transaction.id,
                cashbook_profile_id=profile.id,
                category_id=transaction.category_id,
                sheet_name=sheet_name,
                row_index=row,
                category_column=category_column,
                backup_filename=backup_path.name,
            )
            db.add(sync)
            batch_entries.append(CashbookSyncBatchEntry(
                sync_batch_id=batch.id, transaction_id=transaction.id, operation="insert",
            ))

        for sync, category_column in updated_syncs:
            transaction = next(item for item in transactions if item.id == sync.transaction_id)
            batch_entries.append(CashbookSyncBatchEntry(
                sync_batch_id=batch.id,
                transaction_id=sync.transaction_id,
                operation="update",
                resulting_sync_id=sync.id,
                previous_sync_id=sync.id,
                previous_category_id=sync.category_id,
                previous_sheet_name=sync.sheet_name,
                previous_row_index=sync.row_index,
                previous_category_column=sync.category_column,
                previous_backup_filename=sync.backup_filename,
                previous_synced_at=sync.synced_at,
            ))
            sync.category_id = transaction.category_id
            sync.category_column = category_column
            sync.backup_filename = backup_path.name

        db.flush()
        new_by_transaction = {
            sync.transaction_id: sync
            for sync in db.query(CashbookSync).filter(
                CashbookSync.cashbook_profile_id == profile.id,
                CashbookSync.transaction_id.in_([transaction.id for transaction, *_ in new_syncs]),
            )
        }
        for entry in batch_entries:
            if entry.operation == "insert":
                entry.resulting_sync_id = new_by_transaction[entry.transaction_id].id
            db.add(entry)

        profile.file_hash = sha256(content).hexdigest()
        # xlutils can normalize some BIFF metadata while preserving the
        # validated capture cells. Store the post-write verified schema so a
        # later sync does not mistake our own safe replacement for an edit.
        updated_layout = json.loads(profile.layout_json)
        updated_layout["schema_fingerprint"] = inspect_workbook(content)["schema_fingerprint"]
        profile.layout_json = json.dumps(updated_layout, separators=(",", ":"), sort_keys=True)
        profile.updated_at = datetime.now().astimezone().replace(tzinfo=None)
        db.commit()
    except Exception:
        db.rollback()
        rollback_temporary = path.with_suffix(".xls.rollback.tmp")
        try:
            shutil.copy2(backup_path, rollback_temporary)
            rollback_temporary.replace(path)
        except OSError as restore_exc:
            raise CashbookSyncError(
                "The sync ledger could not be saved and the workbook could not be restored from its backup."
            ) from restore_exc
        raise

    return {
        "status": "synced",
        "message": "The registered cashbook was updated successfully.",
        "written": written,
        "updated": updated,
        "already_synced": already_synced,
        "backup": backup_path.name,
        "batch_id": batch.id,
        "cashbook_filename": profile.source_filename,
    }


def cashbook_status(db: Session) -> dict:
    profile = get_active_cashbook(db)
    final_transactions = (
        db.query(Transaction)
        .filter(Transaction.status.in_(FINAL_STATUSES))
        .all()
    )

    if profile is None:
        return {
            "registered": False,
            "eligible_transactions": len(final_transactions),
            "synced": 0,
            "needs_sync": len(final_transactions),
        }

    syncs = (
        db.query(CashbookSync)
        .filter(CashbookSync.cashbook_profile_id == profile.id)
        .all()
    )
    by_transaction = {sync.transaction_id: sync for sync in syncs}
    needs_sync = 0
    synced = 0
    for transaction in final_transactions:
        sync = by_transaction.get(transaction.id)
        if sync is None or sync.category_id != transaction.category_id:
            needs_sync += 1
        else:
            synced += 1

    return {
        "registered": True,
        "profile_id": profile.id,
        "source_filename": profile.source_filename,
        "managed_path": profile.file_path,
        "adapter": profile.adapter,
        "financial_year": _cashbook_year(profile.source_filename),
        "registered_at": profile.registered_at.isoformat() if profile.registered_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "eligible_transactions": len(final_transactions),
        "synced": synced,
        "needs_sync": needs_sync,
        "backup_count": len(list_cashbook_backups()),
    }


def sync_state_for_transactions(db: Session, transaction_ids: list[int]) -> dict[int, str]:
    profile = get_active_cashbook(db)
    if profile is None:
        return {transaction_id: "cashbook_not_registered" for transaction_id in transaction_ids}

    rows = (
        db.query(CashbookSync)
        .filter(CashbookSync.cashbook_profile_id == profile.id)
        .filter(CashbookSync.transaction_id.in_(transaction_ids))
        .all()
        if transaction_ids
        else []
    )
    syncs = {row.transaction_id: row for row in rows}
    transactions = (
        db.query(Transaction).filter(Transaction.id.in_(transaction_ids)).all()
        if transaction_ids
        else []
    )
    result: dict[int, str] = {}
    for transaction in transactions:
        if transaction.status == "pending":
            result[transaction.id] = "waiting_review"
            continue
        sync = syncs.get(transaction.id)
        if sync is None or sync.category_id != transaction.category_id:
            result[transaction.id] = "needs_sync"
        else:
            result[transaction.id] = "synced"
    return result
