from datetime import date, datetime
import json
import os
import subprocess
import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .audit import audit_event_to_dict, record_audit_event
from .automation import (
    AUTO_APPROVE_MIN_CONFIDENCE,
    AUTO_APPROVE_MIN_HITS,
    trusted_exact_match,
)
from .backups import (
    BACKUP_DIR,
    create_sqlite_backup,
    list_sqlite_backups,
    restore_sqlite_backup,
)
from .bank_parser import StatementParseError, parse_statement
from .config import (
    CASHBOOK_BACKUP_DIR,
    CASHBOOK_DIR,
    CONFIG_DIR,
    ensure_writable_directory,
    resource_path,
)
from .categorize import (
    categorize,
    learn_from_correction,
    move_learning_vote,
    normalize_payee,
)
from .db import SessionLocal, engine, init_db
from .diagnostics import build_diagnostics_zip
from .errors import new_error_id
from .fingerprints import standard_bank_transaction_fingerprint
from .logging_config import LOG_DIR, logger
from .models import AuditEvent, CashbookProfile, CashbookSync, Category, Rule, Statement, Transaction
from .reconciliation import StatementReconciliationError, reconcile_statement
from .version import APP_VERSION
from .wced_export import cashbook_target_sheet
from .cashbook_sync import (
    CashbookSyncError,
    cashbook_status,
    get_active_cashbook,
    inspect_cashbook,
    list_cashbook_backups,
    register_cashbook,
    sync_live_cashbook,
    undo_latest_sync,
    preview_live_cashbook_sync,
    sync_state_for_transactions,
    sync_categories_from_layout,
    sync_categories_from_active_cashbook,
)
from .presentation import display_payee, display_reference
from .workbook_inspector import inspect_workbook
from .merchant_identity import merchant_key

app = FastAPI(title="Ledgerly", version=APP_VERSION)
REVIEW_PAGE = resource_path("app", "static", "review.html")
HELP_PAGE = resource_path("app", "static", "help.html")
app.mount("/static", StaticFiles(directory=resource_path("app", "static")), name="static")
FINAL_STATUSES = ("approved", "corrected")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_TEMPLATE_BYTES = 10 * 1024 * 1024


class ReviewDecision(BaseModel):
    category_id: int
    learn: bool = True
    apply_to_matches: bool = False
    cashbook_narrative: str | None = None


class CategoryCreate(BaseModel):
    name: str
    type: str = "expense"


class RestoreBackupRequest(BaseModel):
    confirm: bool = False


@app.on_event("startup")
def startup() -> None:
    backup_path = create_sqlite_backup(engine, "startup", once_per_day=True)
    init_db()
    logger.info(
        "application_started",
        extra={
            "app_version": APP_VERSION,
            "backup_created": bool(backup_path),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    error_id = new_error_id()
    logger.exception(
        "unhandled_application_error",
        extra={
            "error_id": error_id,
            "method": request.method,
            "path": request.url.path,
            "exception_type": type(exc).__name__,
        },
    )

    db = SessionLocal()
    try:
        record_audit_event(
            db,
            "application.error",
            entity_type="request",
            details={
                "error_id": error_id,
                "method": request.method,
                "path": request.url.path,
                "exception_type": type(exc).__name__,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "failed_to_persist_error_audit_event",
            extra={"error_id": error_id},
        )
    finally:
        db.close()

    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "Something unexpected went wrong. Please send the error ID "
                "or download the diagnostic report."
            ),
            "error_id": error_id,
        },
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "bank": "Standard Bank",
        "version": APP_VERSION,
    }


@app.get("/", include_in_schema=False)
def review_page():
    return FileResponse(REVIEW_PAGE)


@app.get("/help", include_in_schema=False)
def help_page():
    return FileResponse(HELP_PAGE)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year + 1, 1, 1)


def _resolve_working_year(db: Session, year: int | None) -> int:
    latest = db.query(Transaction.txn_date).order_by(Transaction.txn_date.desc()).first()
    latest_year = latest[0].year if latest else date.today().year
    if year is None:
        return latest_year
    if year == date.today().year:
        start, end = _year_bounds(year)
        has_current_year_transactions = db.query(Transaction.id).filter(
            Transaction.txn_date >= start,
            Transaction.txn_date < end,
        ).first()
        if not has_current_year_transactions:
            return latest_year
    return year


def _ensure_year_ready_for_export(db: Session, year: int) -> None:
    start, end = _year_bounds(year)
    year_transactions = db.query(Transaction).filter(
        Transaction.txn_date >= start,
        Transaction.txn_date < end,
    )

    total = year_transactions.count()
    if total == 0:
        raise HTTPException(status_code=404, detail=f"No transactions exist for {year}.")

    pending = year_transactions.filter(Transaction.status == "pending").count()
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"{pending} transaction(s) for {year} still require review before export.",
        )

    invalid_final = year_transactions.filter(
        Transaction.status.in_(FINAL_STATUSES),
        Transaction.category_id.is_(None),
    ).count()
    if invalid_final:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{invalid_final} reviewed transaction(s) for {year} have no category. "
                "Export blocked."
            ),
        )


def _reviewed_transactions_for_year(db: Session, year: int) -> list[Transaction]:
    _ensure_year_ready_for_export(db, year)
    start, end = _year_bounds(year)

    return (
        db.query(Transaction)
        .join(Transaction.category)
        .filter(
            Transaction.txn_date >= start,
            Transaction.txn_date < end,
            Transaction.status.in_(FINAL_STATUSES),
        )
        .order_by(Transaction.txn_date, Transaction.id)
        .all()
    )




def _is_auto_approved(transaction: Transaction) -> bool:
    return (
        transaction.status == "approved"
        and transaction.category_id is not None
        and transaction.category_id == transaction.suggested_category_id
        and transaction.suggestion_method == "exact"
        and transaction.suggestion_confidence is not None
        and float(transaction.suggestion_confidence) >= AUTO_APPROVE_MIN_CONFIDENCE
        and transaction.learned_category_id is None
    )


def _cashbook_preview_item(transaction: Transaction) -> dict:
    final_category = transaction.category
    suggested_category = transaction.suggested_category
    category = final_category or suggested_category

    if transaction.status == "pending":
        allocation_status = "needs_review"
    elif _is_auto_approved(transaction):
        allocation_status = "auto_approved"
    else:
        allocation_status = transaction.status

    return {
        "transaction_id": transaction.id,
        "statement_id": transaction.statement_id,
        "date": transaction.txn_date.isoformat(),
        "description": transaction.payee_raw,
        "payee_display": display_payee(transaction.payee_raw),
        "reference": transaction.reference,
        "reference_display": display_reference(transaction.reference),
        "direction": transaction.direction,
        "amount": str(transaction.amount),
        "target_sheet": cashbook_target_sheet(transaction.txn_date, transaction.direction),
        "category_id": category.id if category else None,
        "category_name": category.name if category else None,
        "allocation_status": allocation_status,
        "confidence": (
            float(transaction.suggestion_confidence)
            if transaction.suggestion_confidence is not None
            else None
        ),
        "suggestion_method": transaction.suggestion_method,
    }


def _statement_history_item(db: Session, statement: Statement) -> dict:
    counts = {"pending": 0, "approved": 0, "corrected": 0}
    rows = (
        db.query(Transaction.status)
        .filter(Transaction.statement_id == statement.id)
        .all()
    )
    for (status,) in rows:
        counts[status] = counts.get(status, 0) + 1

    return {
        "id": statement.id,
        "bank": statement.bank,
        "source_filename": statement.source_filename,
        "uploaded_at": statement.uploaded_at.isoformat() if statement.uploaded_at else None,
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        "financial_year": statement.financial_year,
        "source_transactions": statement.source_transaction_count,
        "transactions_imported": statement.imported_transaction_count,
        "duplicates_skipped": statement.duplicate_transaction_count,
        "pending": counts["pending"],
        "approved": counts["approved"],
        "corrected": counts["corrected"],
        "reconciliation_status": statement.reconciliation_status,
        "reconciliation_difference": str(statement.reconciliation_difference),
    }



def _setup_status(db: Session) -> dict:
    category_count = db.query(Category).count()
    backup_writable = ensure_writable_directory(BACKUP_DIR)
    log_writable = ensure_writable_directory(LOG_DIR)
    config_writable = ensure_writable_directory(CONFIG_DIR)
    cashbook_dir_writable = ensure_writable_directory(CASHBOOK_DIR)
    cashbook_backup_writable = ensure_writable_directory(CASHBOOK_BACKUP_DIR)
    live = cashbook_status(db)

    checks = [
        {
            "id": "database",
            "label": "Local cashbook database",
            "ok": True,
            "message": "Database is ready.",
        },
        {
            "id": "categories",
            "label": "Cashbook categories",
            "ok": category_count > 0,
            "message": (
                f"{category_count} categories loaded."
                if category_count > 0
                else "No categories are loaded. Contact support before importing real statements."
            ),
        },
        {
            "id": "live_cashbook",
            "label": "Bursar's live cashbook",
            "ok": live["registered"],
            "message": (
                f"Registered: {live['source_filename']}"
                if live["registered"]
                else "Register the bursar's existing .xls cashbook. This file becomes the live destination."
            ),
        },
        {
            "id": "cashbook_storage",
            "label": "Live cashbook storage",
            "ok": cashbook_dir_writable and cashbook_backup_writable,
            "message": (
                "Cashbook and cashbook-backup folders are writable."
                if cashbook_dir_writable and cashbook_backup_writable
                else "Cashbook storage is not writable."
            ),
        },
        {
            "id": "backups",
            "label": "Database backup storage",
            "ok": backup_writable,
            "message": "Database backup folder is writable." if backup_writable else "Database backup folder is not writable.",
        },
        {
            "id": "logs",
            "label": "Diagnostic logs",
            "ok": log_writable,
            "message": "Log folder is writable." if log_writable else "Log folder is not writable.",
        },
    ]

    return {
        "version": APP_VERSION,
        "bank": "Standard Bank",
        "category_count": category_count,
        "cashbook": live,
        "ready_for_import": (
            category_count > 0
            and backup_writable
            and log_writable
            and config_writable
        ),
        "ready_for_live_sync": (
            category_count > 0
            and live["registered"]
            and cashbook_dir_writable
            and cashbook_backup_writable
        ),
        "readiness": (
            "ready" if category_count > 0 and live["registered"] and backup_writable
            and log_writable and config_writable and cashbook_dir_writable and cashbook_backup_writable
            else "setup_required" if not live["registered"] else "needs_attention"
        ),
        "checks": checks,
    }


@app.get("/setup/status")
def setup_status(db: Session = Depends(get_db)):
    return _setup_status(db)


@app.post("/cashbook/register", status_code=201)
async def register_live_cashbook(
    file: UploadFile = File(...),
    replace: bool = Form(False),
    db: Session = Depends(get_db),
):
    filename = file.filename or "cashbook.xls"
    if Path(filename).suffix.lower() != ".xls":
        raise HTTPException(status_code=422, detail="The live cashbook must be a legacy .xls workbook.")

    content = await file.read(MAX_TEMPLATE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The selected cashbook file is empty.")
    if len(content) > MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=413, detail="Cashbook is too large. Maximum size is 10 MB.")

    try:
        layout = inspect_cashbook(content)
        profile = register_cashbook(
            db,
            source_filename=filename,
            content=content,
            replace=replace,
        )
    except CashbookSyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    category_sync = sync_categories_from_layout(db, layout)
    db.commit()

    try:
        sync_result = sync_live_cashbook(db)
    except CashbookSyncError as exc:
        sync_result = {
            "status": "needs_attention",
            "message": str(exc),
            "written": 0,
            "updated": 0,
            "already_synced": 0,
        }
        logger.warning(
            "initial_live_cashbook_sync_failed",
            extra={"cashbook_profile_id": profile.id, "reason": str(exc)},
        )

    record_audit_event(
        db,
        "cashbook.registered",
        entity_type="cashbook_profile",
        entity_id=profile.id,
        details={
            "source_filename": filename,
            "adapter": layout["adapter"],
            "sheet_count": layout["sheet_count"],
            "replace": replace,
        },
    )
    db.commit()
    logger.info(
        "live_cashbook_registered",
        extra={
            "cashbook_profile_id": profile.id,
            "adapter": layout["adapter"],
            "source_filename": filename,
        },
    )
    return {
        "cashbook": cashbook_status(db),
        "layout": {
            "adapter": layout["adapter"],
            "sheet_count": layout["sheet_count"],
            "payment_category_count": len(layout["payment_categories"]),
            "receipt_category_count": len(layout["receipt_categories"]),
        },
        "sync": sync_result,
        "categories": category_sync,
    }


@app.get("/cashbook/status")
def live_cashbook_status(db: Session = Depends(get_db)):
    return cashbook_status(db)


@app.get("/cashbook/structure-report")
def cashbook_structure_report(db: Session = Depends(get_db)):
    profile = get_active_cashbook(db)
    if profile is None:
        raise HTTPException(status_code=409, detail="No cashbook is connected. Connect a cashbook first.")
    try:
        return inspect_workbook(profile.file_path, profile.source_filename)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="The cashbook structure report could not be generated.") from exc


@app.post("/categories/refresh")
def refresh_categories_from_cashbook(db: Session = Depends(get_db)):
    try:
        result = sync_categories_from_active_cashbook(db)
    except CashbookSyncError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@app.get("/cashbook/backups")
def live_cashbook_backups():
    return list_cashbook_backups()


@app.post("/cashbook/sync")
def sync_cashbook_now(db: Session = Depends(get_db)):
    if get_active_cashbook(db) is None:
        raise HTTPException(
            status_code=409,
            detail="No cashbook is connected. Connect a cashbook first.",
        )
    try:
        result = sync_live_cashbook(db)
    except CashbookSyncError as exc:
        logger.warning("live_cashbook_sync_failed", extra={"reason": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    record_audit_event(
        db,
        "cashbook.synced",
        entity_type="cashbook_profile",
        entity_id=(get_active_cashbook(db).id if get_active_cashbook(db) else None),
        details=result,
    )
    db.commit()
    logger.info(
        "live_cashbook_synced",
        extra={
            "sync_status": result.get("status"),
            "sync_message": result.get("message"),
            "transactions_written": result.get("written", 0),
            "transactions_updated": result.get("updated", 0),
            "transactions_already_synced": result.get("already_synced", 0),
        },
    )
    return {**result, "cashbook": cashbook_status(db)}


@app.get("/cashbook/sync-preview")
def cashbook_sync_preview(db: Session = Depends(get_db)):
    try:
        return preview_live_cashbook_sync(db)
    except CashbookSyncError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/cashbook/reconciliation")
def cashbook_reconciliation(db: Session = Depends(get_db)):
    profile = get_active_cashbook(db)
    if profile is None:
        raise HTTPException(status_code=409, detail="No cashbook is connected.")
    syncs = {item.transaction_id: item for item in db.query(CashbookSync).filter_by(cashbook_profile_id=profile.id)}
    rows = []
    for transaction in db.query(Transaction).order_by(Transaction.txn_date, Transaction.id):
        sync = syncs.get(transaction.id)
        rows.append({"transaction_id": transaction.id, "date": transaction.txn_date.isoformat(), "description": transaction.cashbook_narrative or transaction.payee_raw, "amount": str(transaction.amount), "direction": transaction.direction, "status": "synced" if sync else "not_synced", "sheet_name": sync.sheet_name if sync else None, "excel_row": sync.row_index + 1 if sync else None, "category": transaction.category.name if transaction.category else None})
    return {"rows": rows, "synced": sum(1 for row in rows if row["status"] == "synced"), "total": len(rows)}


@app.post("/cashbook/undo-last-sync")
def undo_last_cashbook_sync(db: Session = Depends(get_db)):
    try:
        result = undo_latest_sync(db)
    except CashbookSyncError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_audit_event(db, "cashbook.undo", entity_type="cashbook_profile", entity_id=get_active_cashbook(db).id, details=result)
    db.commit()
    return result


@app.post("/cashbook/open")
def open_live_cashbook(db: Session = Depends(get_db)):
    profile = get_active_cashbook(db)
    if profile is None:
        raise HTTPException(
            status_code=409,
            detail="No cashbook is connected. Connect a cashbook first.",
        )
    path = Path(profile.file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The registered cashbook file is missing.")

    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not open the cashbook automatically. File: {path}",
        ) from exc

    return {"opened": True, "filename": profile.source_filename, "managed_path": str(path)}

@app.get("/backups")
def backup_history():
    return list_sqlite_backups()


@app.post("/backups", status_code=201)
def create_manual_backup(db: Session = Depends(get_db)):
    path = create_sqlite_backup(engine, "manual")
    if path is None:
        raise HTTPException(status_code=409, detail="A local SQLite database is not available to back up.")

    record_audit_event(
        db,
        "backup.created",
        entity_type="application",
        details={"filename": path.name, "reason": "manual"},
    )
    db.commit()
    logger.info("manual_backup_created", extra={"backup_filename": path.name})
    return {"filename": path.name, "message": "Backup created successfully."}


@app.post("/backups/{filename}/restore")
def restore_backup(filename: str, request: RestoreBackupRequest):
    if not request.confirm:
        raise HTTPException(status_code=422, detail="Restore confirmation is required.")

    safety_backup = create_sqlite_backup(engine, "before-restore")
    if safety_backup is None:
        raise HTTPException(status_code=409, detail="Could not create the required safety backup before restore.")

    try:
        restored = restore_sqlite_backup(engine, filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    init_db()
    db = SessionLocal()
    try:
        record_audit_event(
            db,
            "backup.restored",
            entity_type="application",
            details={
                "restored_filename": restored.name,
                "safety_backup_filename": safety_backup.name,
            },
        )
        db.commit()
    finally:
        db.close()

    logger.warning(
        "database_backup_restored",
        extra={
            "restored_filename": restored.name,
            "safety_backup_filename": safety_backup.name,
        },
    )
    return {
        "restored": restored.name,
        "safety_backup": safety_backup.name,
        "message": "Backup restored. Reload the cashbook before continuing.",
    }


def _cashbook_summary(db: Session, year: int) -> dict:
    start, end = _year_bounds(year)
    transactions = (
        db.query(Transaction)
        .filter(Transaction.txn_date >= start, Transaction.txn_date < end)
        .order_by(Transaction.txn_date, Transaction.id)
        .all()
    )
    statements = (
        db.query(Statement)
        .filter(Statement.period_end >= start, Statement.period_start < end)
        .order_by(Statement.period_start, Statement.id)
        .all()
    )

    pending = sum(1 for transaction in transactions if transaction.status == "pending")
    reviewed = sum(1 for transaction in transactions if transaction.status in FINAL_STATUSES)
    auto_approved = sum(1 for transaction in transactions if _is_auto_approved(transaction))
    corrected = sum(1 for transaction in transactions if transaction.status == "corrected")
    manually_approved = sum(
        1
        for transaction in transactions
        if transaction.status == "approved" and not _is_auto_approved(transaction)
    )
    money_out = sum(
        (transaction.amount for transaction in transactions if transaction.direction == "debit"),
        start=0,
    )
    money_in = sum(
        (transaction.amount for transaction in transactions if transaction.direction == "credit"),
        start=0,
    )
    all_reconciled = bool(statements) and all(
        statement.reconciliation_status == "passed"
        and statement.reconciliation_difference == 0
        for statement in statements
    )
    difference = sum((statement.reconciliation_difference for statement in statements), start=0)
    sync_states = sync_state_for_transactions(db, [transaction.id for transaction in transactions])
    synced = sum(1 for state in sync_states.values() if state == "synced")
    needs_sync = sum(1 for state in sync_states.values() if state == "needs_sync")
    live = cashbook_status(db)

    return {
        "year": year,
        "transaction_count": len(transactions),
        "reviewed": reviewed,
        "auto_approved": auto_approved,
        "manually_approved": manually_approved,
        "corrected": corrected,
        "pending": pending,
        "money_out": f"{money_out:.2f}",
        "money_in": f"{money_in:.2f}",
        "statement_count": len(statements),
        "all_statements_reconciled": all_reconciled,
        "reconciliation_difference": f"{difference:.2f}",
        "cashbook_registered": live["registered"],
        "cashbook_synced": synced,
        "cashbook_needs_sync": needs_sync,
        "ready": bool(transactions) and pending == 0 and reviewed == len(transactions) and all_reconciled,
    }


@app.get("/cashbook/summary")
def cashbook_summary(
    year: int | None = Query(None, ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    return _cashbook_summary(db, _resolve_working_year(db, year))


@app.get("/exports/summary", include_in_schema=False)
def deprecated_export_summary(
    year: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    # Compatibility alias for Phase 3/5 frontends. Phase 6 no longer exports a cashbook.
    return _cashbook_summary(db, year)


@app.get("/cashbook/preview")
def cashbook_preview(
    year: int | None = Query(None, ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    year = _resolve_working_year(db, year)
    start, end = _year_bounds(year)
    transactions = (
        db.query(Transaction)
        .filter(Transaction.txn_date >= start, Transaction.txn_date < end)
        .order_by(Transaction.txn_date, Transaction.id)
        .all()
    )
    sync_states = sync_state_for_transactions(db, [transaction.id for transaction in transactions])
    rows = [
        {**_cashbook_preview_item(transaction), "sync_status": sync_states.get(transaction.id, "waiting_review")}
        for transaction in transactions
    ]

    allocation_map: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        category_name = row["category_name"] or "Unallocated"
        key = (category_name, row["direction"], row["allocation_status"])
        current = allocation_map.setdefault(
            key,
            {
                "category_name": category_name,
                "direction": row["direction"],
                "allocation_status": row["allocation_status"],
                "transaction_count": 0,
                "total_amount": 0,
                "target_sheets": set(),
            },
        )
        current["transaction_count"] += 1
        current["total_amount"] += float(row["amount"])
        current["target_sheets"].add(row["target_sheet"])

    allocations = []
    for current in allocation_map.values():
        allocations.append(
            {
                **current,
                "total_amount": f'{current["total_amount"]:.2f}',
                "target_sheets": sorted(current["target_sheets"]),
            }
        )
    allocations.sort(
        key=lambda item: (item["direction"], item["category_name"], item["allocation_status"])
    )

    statements = (
        db.query(Statement)
        .filter(Statement.period_end >= start, Statement.period_start < end)
        .order_by(Statement.period_start, Statement.id)
        .all()
    )

    return {
        "year": year,
        "automation_policy": {
            "exact_match_only": True,
            "minimum_confidence": AUTO_APPROVE_MIN_CONFIDENCE,
            "minimum_historical_hits": AUTO_APPROVE_MIN_HITS,
            "income_auto_approval": True,
        },
        "summary": _cashbook_summary(db, year),
        "statements": [
            {
                "statement_id": statement.id,
                "period_start": statement.period_start.isoformat(),
                "period_end": statement.period_end.isoformat(),
                "opening_balance": str(statement.opening_balance),
                "total_credits": str(statement.total_credits),
                "total_debits": str(statement.total_debits),
                "closing_balance": str(statement.closing_balance),
                "reconciliation_difference": str(statement.reconciliation_difference),
                "reconciliation_status": statement.reconciliation_status,
            }
            for statement in statements
        ],
        "allocations": allocations,
        "rows": rows,
    }


@app.get("/imports/history")
def import_history(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    statements = (
        db.query(Statement)
        .order_by(Statement.uploaded_at.desc(), Statement.id.desc())
        .limit(limit)
        .all()
    )
    return [_statement_history_item(db, statement) for statement in statements]


@app.delete("/imports/{statement_id}")
def delete_imported_statement(
    statement_id: int,
    db: Session = Depends(get_db),
):
    statement = db.get(Statement, statement_id)
    if statement is None:
        raise HTTPException(status_code=404, detail="Statement import was not found.")

    transaction_ids = [
        transaction_id
        for (transaction_id,) in db.query(Transaction.id)
        .filter(Transaction.statement_id == statement.id)
        .all()
    ]
    if transaction_ids and db.query(CashbookSync.id).filter(
        CashbookSync.transaction_id.in_(transaction_ids)
    ).first():
        raise HTTPException(
            status_code=409,
            detail=(
                "This statement has transactions already written to the cashbook. "
                "Restore a cashbook backup instead of removing the import."
            ),
        )

    deleted_transactions = len(transaction_ids)
    if transaction_ids:
        db.query(Transaction).filter(Transaction.id.in_(transaction_ids)).delete(
            synchronize_session=False
        )
    db.delete(statement)
    record_audit_event(
        db,
        "statement.removed",
        entity_type="statement",
        entity_id=statement_id,
        details={"transactions_removed": deleted_transactions},
    )
    db.commit()
    return {
        "statement_id": statement_id,
        "transactions_removed": deleted_transactions,
    }


@app.post("/workspace/reset")
def reset_workspace(db: Session = Depends(get_db)):
    """Clear local application records without altering the Excel cashbook file."""
    counts = {
        "statements": db.query(Statement).count(),
        "transactions": db.query(Transaction).count(),
    }
    db.query(CashbookSync).delete(synchronize_session=False)
    db.query(Transaction).delete(synchronize_session=False)
    db.query(Statement).delete(synchronize_session=False)
    db.query(Rule).delete(synchronize_session=False)
    db.query(Category).delete(synchronize_session=False)
    db.query(CashbookProfile).delete(synchronize_session=False)
    db.query(AuditEvent).delete(synchronize_session=False)
    db.commit()
    logger.warning("workspace_reset", extra=counts)
    return {"status": "reset", **counts}


@app.get("/learning/status")
def learning_status(db: Session = Depends(get_db)):
    """Return the local category-learning summary without bank transaction data."""
    rules = (
        db.query(Rule)
        .join(Rule.category)
        .order_by(Rule.hit_count.desc(), Rule.updated_at.desc(), Rule.id.desc())
        .all()
    )
    return {
        "enabled": True,
        "patterns": len(rules),
        "examples": sum(rule.hit_count for rule in rules),
        "recent_patterns": [
            {
                "merchant": rule.payee_pattern,
                "category": rule.category.name,
                "direction": rule.category.type,
                "examples": rule.hit_count,
                "confidence": float(rule.confidence),
            }
            for rule in rules[:8]
        ],
    }


@app.get("/audit/history")
def audit_history(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    events = (
        db.query(AuditEvent)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .all()
    )
    return [audit_event_to_dict(event) for event in events]


@app.get("/diagnostics/export")
def export_diagnostics(db: Session = Depends(get_db)):
    record_audit_event(
        db,
        "diagnostics.exported",
        entity_type="application",
        details={"app_version": APP_VERSION},
    )
    db.commit()

    content = build_diagnostics_zip(db)
    filename = f"bursar-diagnostics-{datetime.now().astimezone():%Y%m%d-%H%M%S}.zip"
    logger.info("diagnostics_exported", extra={"app_version": APP_VERSION})

    return StreamingResponse(
        BytesIO(content),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/statements/upload", status_code=201)
async def upload_statement(
    file: UploadFile = File(...),
    bank: str = Form("Standard Bank"),
    db: Session = Depends(get_db),
):
    filename = file.filename or "statement"
    content = await file.read(MAX_UPLOAD_BYTES + 1)

    if not content:
        raise HTTPException(status_code=400, detail="The uploaded statement is empty.")

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Statement is too large. Maximum upload size is 15 MB.",
        )

    logger.info(
        "statement_import_started",
        extra={
            "bank": bank,
            "file_extension": Path(filename).suffix.lower(),
            "file_size_bytes": len(content),
        },
    )

    source_hash = sha256(content).hexdigest()
    duplicate_statement = (
        db.query(Statement).filter(Statement.source_hash == source_hash).first()
    )
    if duplicate_statement:
        logger.warning(
            "duplicate_statement_rejected",
            extra={"existing_statement_id": duplicate_statement.id},
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "This exact statement file was already imported as statement "
                f"{duplicate_statement.id}."
            ),
        )

    try:
        parsed = parse_statement(filename, content, bank=bank)
        reconciliation = reconcile_statement(parsed)
    except (StatementParseError, StatementReconciliationError) as exc:
        logger.warning(
            "statement_validation_failed",
            extra={"validation_type": type(exc).__name__},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    transactions_with_fingerprints = [
        (transaction, standard_bank_transaction_fingerprint(transaction))
        for transaction in parsed.transactions
    ]

    fingerprint_counts: dict[str, int] = {}
    for _, fingerprint in transactions_with_fingerprints:
        fingerprint_counts[fingerprint] = fingerprint_counts.get(fingerprint, 0) + 1

    repeated_in_source = [
        fingerprint for fingerprint, count in fingerprint_counts.items() if count > 1
    ]
    if repeated_in_source:
        logger.warning(
            "duplicate_rows_in_statement_rejected",
            extra={"duplicate_fingerprint_count": len(repeated_in_source)},
        )
        raise HTTPException(
            status_code=422,
            detail=(
                "The uploaded statement contains duplicate transaction rows. "
                "Import cancelled so the duplicate can be investigated."
            ),
        )

    fingerprints = [fingerprint for _, fingerprint in transactions_with_fingerprints]
    existing_fingerprints = {
        row[0]
        for row in (
            db.query(Transaction.fingerprint)
            .filter(Transaction.fingerprint.in_(fingerprints))
            .all()
        )
    }

    new_transactions = [
        (transaction, fingerprint)
        for transaction, fingerprint in transactions_with_fingerprints
        if fingerprint not in existing_fingerprints
    ]
    duplicate_count = len(parsed.transactions) - len(new_transactions)

    if not new_transactions:
        logger.warning(
            "fully_overlapping_statement_rejected",
            extra={"source_transaction_count": len(parsed.transactions)},
        )
        raise HTTPException(
            status_code=409,
            detail="Every transaction in this statement has already been imported.",
        )

    backup_path = create_sqlite_backup(db.get_bind(), "before-import")
    if backup_path:
        logger.info("database_backup_created", extra={"reason": "before-import"})

    period_start = min(transaction.txn_date for transaction in parsed.transactions)
    period_end = max(transaction.txn_date for transaction in parsed.transactions)
    financial_year = period_start.year if period_start.year == period_end.year else None

    statement = Statement(
        bank="Standard Bank",
        source_filename=filename,
        source_hash=source_hash,
        period_start=period_start,
        period_end=period_end,
        financial_year=financial_year,
        opening_balance=reconciliation.opening_balance,
        closing_balance=reconciliation.closing_balance,
        total_debits=reconciliation.total_debits,
        total_credits=reconciliation.total_credits,
        reconciliation_difference=reconciliation.difference,
        reconciliation_status="passed",
        source_transaction_count=len(parsed.transactions),
        imported_transaction_count=len(new_transactions),
        duplicate_transaction_count=duplicate_count,
    )
    db.add(statement)
    db.flush()

    auto_approved_count = 0
    pending_review_count = 0

    for raw, fingerprint in new_transactions:
        suggested_category_id = None
        suggestion_confidence = None
        suggestion_method = None
        category_id = None
        status = "pending"
        trusted_match = None
        expected_category_type = "expense" if raw.direction == "debit" else "income"

        suggested_category_id, confidence, suggestion_method = categorize(
            raw.description,
            db,
            category_type=expected_category_type,
        )
        if suggested_category_id is not None:
            suggestion_confidence = confidence

        learned_narrative = None
        if suggested_category_id is not None:
            narrative_rule = db.query(Rule).filter(
                Rule.payee_pattern == merchant_key(raw.description),
                Rule.category_id == suggested_category_id,
            ).first()
            learned_narrative = narrative_rule.cashbook_narrative if narrative_rule else None

        if suggestion_method == "exact":
            trusted_match = trusted_exact_match(
                raw.description,
                db,
                category_type=expected_category_type,
            )

        if (
            trusted_match is not None
            and trusted_match.category_id == suggested_category_id
        ):
            category_id = suggested_category_id
            status = "approved"
            auto_approved_count += 1
        else:
            pending_review_count += 1

        transaction = Transaction(
            statement_id=statement.id,
            fingerprint=fingerprint,
            source_row=raw.source_row,
            txn_date=raw.txn_date,
            payee_raw=raw.description,
            payee_normalized=normalize_payee(raw.description),
            merchant_key=merchant_key(raw.description),
            cashbook_narrative=learned_narrative,
            reference=raw.reference,
            balance_after=raw.balance_after,
            amount=raw.amount,
            direction=raw.direction,
            suggested_category_id=suggested_category_id,
            suggestion_confidence=suggestion_confidence,
            suggestion_method=suggestion_method,
            category_id=category_id,
            learned_category_id=None,
            status=status,
        )
        db.add(transaction)
        db.flush()

        if status == "approved":
            record_audit_event(
                db,
                "transaction.auto_approved",
                entity_type="transaction",
                entity_id=transaction.id,
                details={
                    "statement_id": statement.id,
                    "category_id": category_id,
                    "confidence": suggestion_confidence,
                    "historical_hits": trusted_match.hit_count if trusted_match else None,
                    "policy_min_confidence": AUTO_APPROVE_MIN_CONFIDENCE,
                    "policy_min_hits": AUTO_APPROVE_MIN_HITS,
                },
            )

    record_audit_event(
        db,
        "statement.imported",
        entity_type="statement",
        entity_id=statement.id,
        details={
            "bank": "Standard Bank",
            "financial_year": financial_year,
            "source_transactions": len(parsed.transactions),
            "transactions_imported": len(new_transactions),
            "duplicates_skipped": duplicate_count,
            "auto_approved": auto_approved_count,
            "pending_review": pending_review_count,
            "reconciliation_status": "passed",
            "reconciliation_difference": str(reconciliation.difference),
        },
    )

    db.commit()
    db.refresh(statement)

    try:
        cashbook_sync_result = sync_live_cashbook(db)
    except CashbookSyncError as exc:
        cashbook_sync_result = {
            "status": "needs_attention",
            "message": str(exc),
            "written": 0,
            "updated": 0,
        }
        logger.warning("automatic_cashbook_sync_failed", extra={"statement_id": statement.id, "reason": str(exc)})

    logger.info(
        "statement_import_completed",
        extra={
            "statement_id": statement.id,
            "transactions_imported": len(new_transactions),
            "duplicates_skipped": duplicate_count,
            "auto_approved": auto_approved_count,
            "pending_review": pending_review_count,
            "financial_year": financial_year,
            "reconciliation_difference": str(reconciliation.difference),
        },
    )

    return {
        "statement_id": statement.id,
        "source_transactions": len(parsed.transactions),
        "transactions_imported": len(new_transactions),
        "duplicates_skipped": duplicate_count,
        "auto_approved": auto_approved_count,
        "pending_review": pending_review_count,
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "cashbook_sync": cashbook_sync_result,
        "reconciliation": {
            "status": "passed",
            "opening_balance": str(reconciliation.opening_balance),
            "total_debits": str(reconciliation.total_debits),
            "total_credits": str(reconciliation.total_credits),
            "closing_balance": str(reconciliation.closing_balance),
            "difference": str(reconciliation.difference),
            "transaction_order": reconciliation.transaction_order,
        },
    }


def _transaction_review_item(transaction: Transaction, matching_count: int = 0) -> dict:
    historical_hits = 0
    if transaction.suggested_category_id is not None:
        rule = (
            transaction._sa_instance_state.session.query(Rule)
            .filter(Rule.payee_pattern == transaction.merchant_key)
            .filter(Rule.category_id == transaction.suggested_category_id)
            .first()
        )
        historical_hits = rule.hit_count if rule else 0
    return {
            "id": transaction.id,
            "date": transaction.txn_date.isoformat(),
            # Keep these presentation aliases while the desktop shell is updated.
            "txn_date": transaction.txn_date.isoformat(),
            "description": transaction.payee_raw,
            "payee_raw": transaction.payee_raw,
            "cashbook_narrative": transaction.cashbook_narrative,
            "payee_display": display_payee(transaction.payee_raw),
            "merchant_key": transaction.merchant_key,
            "reference": transaction.reference,
            "reference_display": display_reference(transaction.reference),
            "amount": str(transaction.amount),
            "direction": transaction.direction,
            "balance_after": str(transaction.balance_after),
            "suggested_category_id": transaction.suggested_category_id,
            "suggested_category": transaction.suggested_category.name if transaction.suggested_category else None,
            "suggested_category_name": transaction.suggested_category.name if transaction.suggested_category else None,
            "category": transaction.category.name if transaction.category else None,
            "category_id": transaction.category_id,
            "status": transaction.status,
            "matching_pending_count": matching_count,
            "confidence": float(transaction.suggestion_confidence) if transaction.suggestion_confidence is not None else None,
            "suggestion_method": transaction.suggestion_method,
            "historical_hit_count": historical_hits,
        }


@app.get("/transactions/pending")
def pending_transactions(
    page: int | None = Query(None, ge=1),
    page_size: int = Query(25, ge=10, le=100),
    search: str | None = Query(None, max_length=200),
    status: str = Query("needs_classification"),
    db: Session = Depends(get_db),
):
    query = db.query(Transaction)
    if status == "suggested":
        query = query.filter(
            Transaction.status == "pending",
            Transaction.suggested_category_id.is_not(None),
        )
    elif status in {"all", "needs_classification"}:
        query = query.filter(Transaction.status == "pending")
    elif status == "reviewed":
        query = query.filter(Transaction.status.in_(FINAL_STATUSES))
    else:
        raise HTTPException(status_code=422, detail="Invalid transaction filter.")
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.outerjoin(Transaction.category).filter(
            (Transaction.payee_raw.ilike(term)) | (Transaction.reference.ilike(term)) | (Category.name.ilike(term))
        )
    query = query.order_by(Transaction.suggestion_confidence.asc().nullsfirst(), Transaction.txn_date.desc(), Transaction.id.desc())
    if page is None:  # Legacy API response retained for existing integrations.
        transactions = query.all()
        return [_transaction_review_item(item) for item in transactions]
    total = query.count()
    transactions = query.offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for transaction in transactions:
        matches = db.query(Transaction.id).filter(
            Transaction.status == "pending",
            Transaction.merchant_key == transaction.merchant_key,
            Transaction.direction == transaction.direction,
        ).count() - 1
        items.append(_transaction_review_item(transaction, max(0, matches)))
    return {"transactions": items, "page": page, "page_size": page_size,
            "total": total, "page_count": max(1, (total + page_size - 1) // page_size)}


@app.get("/categories")
def categories(db: Session = Depends(get_db)):
    profile = get_active_cashbook(db)
    layout = json.loads(profile.layout_json) if profile else {}
    linked = {
        *( (name, "expense") for name in layout.get("payment_categories", []) ),
        *( (name, "income") for name in layout.get("receipt_categories", []) ),
    }
    return [
        {"id": category.id, "name": category.name, "type": category.type,
         "linked_to_cashbook": (category.name, category.type) in linked}
        for category in db.query(Category).order_by(Category.type, Category.name).all()
    ]


@app.post("/categories", status_code=201)
def create_category(category_input: CategoryCreate, db: Session = Depends(get_db)):
    name = category_input.name.strip()
    category_type = category_input.type.strip().lower()

    if not name:
        raise HTTPException(status_code=422, detail="Category name is required.")

    if category_type not in {"expense", "income"}:
        raise HTTPException(
            status_code=422,
            detail="Category type must be expense or income.",
        )

    existing = db.query(Category).filter_by(name=name, type=category_type).first()
    if existing:
        raise HTTPException(status_code=409, detail="That category already exists.")

    category = Category(name=name, type=category_type)
    db.add(category)
    db.flush()
    record_audit_event(
        db,
        "category.created",
        entity_type="category",
        entity_id=category.id,
        details={"name": category.name, "type": category.type},
    )
    db.commit()
    db.refresh(category)

    logger.info(
        "category_created",
        extra={"category_id": category.id, "category_type": category.type},
    )
    return {"id": category.id, "name": category.name, "type": category.type}


@app.post("/transactions/{transaction_id}/review")
def review_transaction(
    transaction_id: int,
    decision: ReviewDecision,
    db: Session = Depends(get_db),
):
    transaction = db.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    category = db.get(Category, decision.category_id)
    if category is None:
        raise HTTPException(status_code=422, detail="Category not found.")

    expected_type = "expense" if transaction.direction == "debit" else "income"
    if category.type != expected_type:
        raise HTTPException(
            status_code=422,
            detail=(
                f"A {transaction.direction} transaction must use an "
                f"{expected_type} category."
            ),
        )

    if decision.apply_to_matches:
        if transaction.status != "pending":
            raise HTTPException(status_code=409, detail="Bulk review is available only for a pending transaction.")
        matching_ids = [
            row[0] for row in db.query(Transaction.id).filter(
                Transaction.status == "pending",
                Transaction.merchant_key == transaction.merchant_key,
                Transaction.direction == transaction.direction,
            ).order_by(Transaction.id).all()
        ]
        # Direction is deliberately part of the match: an identical merchant
        # must never bulk-classify both income and expense transactions.
        results = []
        for matching_id in matching_ids:
            results.append(review_transaction(
                matching_id,
                ReviewDecision(category_id=decision.category_id, learn=decision.learn),
                db,
            ))
        return {
            "transaction_id": transaction_id,
            "bulk": True,
            "reviewed_count": len(results),
            "cashbook_sync": results[-1]["cashbook_sync"] if results else None,
        }

    previous_category_id = transaction.category_id
    previous_status = transaction.status
    same_final_category = (
        transaction.status in FINAL_STATUSES and transaction.category_id == category.id
    )

    learning_changed = False
    if transaction.learned_category_id is not None:
        if transaction.learned_category_id != category.id:
            move_learning_vote(
                transaction.payee_raw,
                transaction.learned_category_id,
                category.id,
                db,
            )
            transaction.learned_category_id = category.id
            learning_changed = True
    elif decision.learn:
        learn_from_correction(
            transaction.payee_raw,
            category.id,
            db,
            cashbook_narrative=decision.cashbook_narrative,
        )
        transaction.learned_category_id = category.id
        learning_changed = True

    if decision.cashbook_narrative is not None:
        transaction.cashbook_narrative = decision.cashbook_narrative.strip() or None

    if same_final_category and not learning_changed:
        try:
            cashbook_sync_result = sync_live_cashbook(db, transaction_ids=[transaction.id])
        except CashbookSyncError as exc:
            cashbook_sync_result = {
                "status": "needs_attention",
                "message": str(exc),
                "written": 0,
                "updated": 0,
            }
        logger.info(
            "transaction_review_idempotent_retry",
            extra={"transaction_id": transaction.id, "category_id": category.id},
        )
        return {
            "transaction_id": transaction.id,
            "status": transaction.status,
            "learned": False,
            "idempotent": True,
            "cashbook_sync": cashbook_sync_result,
        }

    transaction.category_id = category.id
    transaction.category = category
    transaction.status = (
        "approved"
        if transaction.suggested_category_id is not None
        and transaction.suggested_category_id == category.id
        else "corrected"
    )

    record_audit_event(
        db,
        "transaction.reviewed",
        entity_type="transaction",
        entity_id=transaction.id,
        details={
            "statement_id": transaction.statement_id,
            "previous_category_id": previous_category_id,
            "category_id": category.id,
            "previous_status": previous_status,
            "status": transaction.status,
            "learning_changed": learning_changed,
        },
    )
    db.commit()

    try:
        cashbook_sync_result = sync_live_cashbook(db, transaction_ids=[transaction.id])
    except CashbookSyncError as exc:
        cashbook_sync_result = {
            "status": "needs_attention",
            "message": str(exc),
            "written": 0,
            "updated": 0,
        }
        logger.warning("automatic_cashbook_sync_failed", extra={"transaction_id": transaction.id, "reason": str(exc)})

    logger.info(
        "transaction_reviewed",
        extra={
            "transaction_id": transaction.id,
            "statement_id": transaction.statement_id,
            "category_id": category.id,
            "status": transaction.status,
            "learning_changed": learning_changed,
        },
    )

    return {
        "transaction_id": transaction.id,
        "status": transaction.status,
        "learned": learning_changed,
        "idempotent": False,
        "cashbook_sync": cashbook_sync_result,
    }


@app.get("/exports/reviewed-cashbook.xlsx", include_in_schema=False)
def retired_reviewed_cashbook_export():
    raise HTTPException(
        status_code=410,
        detail=(
            "Phase 6 does not generate a separate cashbook file. Approved transactions "
            "are synchronized into the registered live cashbook."
        ),
    )


@app.get("/exports/wced-cashbook.xls", include_in_schema=False)
def retired_wced_cashbook_export():
    raise HTTPException(
        status_code=410,
        detail=(
            "Cashbook download has been retired. Open the registered live cashbook; "
            "the application updates that workbook in place."
        ),
    )
