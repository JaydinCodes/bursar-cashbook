from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .audit import audit_event_to_dict, record_audit_event
from .backups import (
    BACKUP_DIR,
    create_sqlite_backup,
    list_sqlite_backups,
    restore_sqlite_backup,
)
from .bank_parser import StatementParseError, parse_statement
from .config import (
    CONFIG_DIR,
    DEFAULT_WCED_TEMPLATE,
    ensure_writable_directory,
    get_wced_template_path,
    get_wced_template_source,
    is_wced_template_ready,
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
from .models import AuditEvent, Category, Statement, Transaction
from .reconciliation import StatementReconciliationError, reconcile_statement
from .version import APP_VERSION
from .wced_export import (
    WcedExportError,
    WcedTransaction,
    export_wced_cashbook as build_wced_cashbook,
)

app = FastAPI(title="Bursar Cashbook Automation", version=APP_VERSION)
REVIEW_PAGE = Path(__file__).parent / "static" / "review.html"
HELP_PAGE = Path(__file__).parent / "static" / "help.html"
FINAL_STATUSES = ("approved", "corrected")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_TEMPLATE_BYTES = 10 * 1024 * 1024


class ReviewDecision(BaseModel):
    category_id: int
    learn: bool = True


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


def _excel_safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


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
    database_ok = True
    backup_writable = ensure_writable_directory(BACKUP_DIR)
    log_writable = ensure_writable_directory(LOG_DIR)
    config_writable = ensure_writable_directory(CONFIG_DIR)
    template_ready = is_wced_template_ready()

    checks = [
        {
            "id": "database",
            "label": "Local cashbook database",
            "ok": database_ok,
            "message": "Database is ready." if database_ok else "Database is unavailable.",
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
            "id": "wced_template",
            "label": "WCED cashbook template",
            "ok": template_ready,
            "message": (
                f"Template ready ({get_wced_template_source()})."
                if template_ready
                else "Upload the blank WCED .xls template before generating WCED exports."
            ),
        },
        {
            "id": "backups",
            "label": "Backup storage",
            "ok": backup_writable,
            "message": "Backup folder is writable." if backup_writable else "Backup folder is not writable.",
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
        "template_configured": template_ready,
        "template_source": get_wced_template_source(),
        "ready_for_import": database_ok and category_count > 0 and backup_writable and log_writable,
        "ready_for_wced_export": (
            database_ok
            and category_count > 0
            and template_ready
            and backup_writable
            and log_writable
            and config_writable
        ),
        "checks": checks,
    }


@app.get("/setup/status")
def setup_status(db: Session = Depends(get_db)):
    return _setup_status(db)


def _validate_wced_template(content: bytes) -> None:
    import xlrd

    try:
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="That file is not a readable legacy .xls workbook.",
        ) from exc

    required = {
        f"{month} {suffix}"
        for month in ("Jan", "Feb", "Mar", "April", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec")
        for suffix in ("PC", "RC")
    }
    missing = sorted(required.difference(workbook.sheet_names()))
    workbook.release_resources()
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                "This does not look like the expected WCED cashbook template. "
                f"Missing sheet(s): {', '.join(missing[:6])}"
                + ("…" if len(missing) > 6 else "")
            ),
        )


@app.post("/setup/wced-template")
async def upload_wced_template(
    file: UploadFile = File(...),
    replace: bool = Form(False),
    db: Session = Depends(get_db),
):
    filename = file.filename or "template.xls"
    if Path(filename).suffix.lower() != ".xls":
        raise HTTPException(status_code=422, detail="The WCED template must be a legacy .xls file.")

    content = await file.read(MAX_TEMPLATE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The WCED template file is empty.")
    if len(content) > MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=413, detail="WCED template is too large. Maximum size is 10 MB.")

    _validate_wced_template(content)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if DEFAULT_WCED_TEMPLATE.exists() and not replace:
        raise HTTPException(
            status_code=409,
            detail="A local WCED template is already configured. Choose replace to overwrite it.",
        )

    temporary = DEFAULT_WCED_TEMPLATE.with_suffix(".xls.tmp")
    temporary.write_bytes(content)
    temporary.replace(DEFAULT_WCED_TEMPLATE)

    record_audit_event(
        db,
        "setup.wced_template_configured",
        entity_type="application",
        details={"replaced": replace, "size_bytes": len(content)},
    )
    db.commit()
    logger.info("wced_template_configured", extra={"replaced": replace, "size_bytes": len(content)})
    return _setup_status(db)


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


def _export_summary(db: Session, year: int) -> dict:
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

    return {
        "year": year,
        "transaction_count": len(transactions),
        "reviewed": reviewed,
        "pending": pending,
        "money_out": f"{money_out:.2f}",
        "money_in": f"{money_in:.2f}",
        "statement_count": len(statements),
        "all_statements_reconciled": all_reconciled,
        "reconciliation_difference": f"{difference:.2f}",
        "wced_template_configured": is_wced_template_ready(),
        "ready_for_reviewed_export": bool(transactions) and pending == 0 and reviewed == len(transactions) and all_reconciled,
        "ready_for_wced_export": (
            bool(transactions)
            and pending == 0
            and reviewed == len(transactions)
            and all_reconciled
            and is_wced_template_ready()
        ),
    }


@app.get("/exports/summary")
def export_summary(
    year: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    return _export_summary(db, year)


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

    for raw, fingerprint in new_transactions:
        suggested_category_id = None
        suggestion_confidence = None
        suggestion_method = "income_manual"

        if raw.direction == "debit":
            suggested_category_id, confidence, suggestion_method = categorize(
                raw.description,
                db,
            )
            if suggested_category_id is not None:
                suggestion_confidence = confidence

        db.add(
            Transaction(
                statement_id=statement.id,
                fingerprint=fingerprint,
                source_row=raw.source_row,
                txn_date=raw.txn_date,
                payee_raw=raw.description,
                payee_normalized=normalize_payee(raw.description),
                reference=raw.reference,
                balance_after=raw.balance_after,
                amount=raw.amount,
                direction=raw.direction,
                suggested_category_id=suggested_category_id,
                suggestion_confidence=suggestion_confidence,
                suggestion_method=suggestion_method,
                category_id=None,
                learned_category_id=None,
                status="pending",
            )
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
            "reconciliation_status": "passed",
            "reconciliation_difference": str(reconciliation.difference),
        },
    )

    db.commit()
    db.refresh(statement)

    logger.info(
        "statement_import_completed",
        extra={
            "statement_id": statement.id,
            "transactions_imported": len(new_transactions),
            "duplicates_skipped": duplicate_count,
            "financial_year": financial_year,
            "reconciliation_difference": str(reconciliation.difference),
        },
    )

    return {
        "statement_id": statement.id,
        "source_transactions": len(parsed.transactions),
        "transactions_imported": len(new_transactions),
        "duplicates_skipped": duplicate_count,
        "pending_review": len(new_transactions),
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
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


@app.get("/transactions/pending")
def pending_transactions(db: Session = Depends(get_db)):
    transactions = (
        db.query(Transaction)
        .filter(Transaction.status == "pending")
        .order_by(
            Transaction.suggestion_confidence.asc().nullsfirst(),
            Transaction.txn_date.desc(),
            Transaction.id.desc(),
        )
        .all()
    )

    return [
        {
            "id": transaction.id,
            "date": transaction.txn_date.isoformat(),
            "description": transaction.payee_raw,
            "reference": transaction.reference,
            "amount": str(transaction.amount),
            "direction": transaction.direction,
            "balance_after": str(transaction.balance_after),
            "suggested_category_id": transaction.suggested_category_id,
            "suggested_category": (
                transaction.suggested_category.name
                if transaction.suggested_category
                else None
            ),
            "confidence": (
                float(transaction.suggestion_confidence)
                if transaction.suggestion_confidence is not None
                else None
            ),
            "suggestion_method": transaction.suggestion_method,
        }
        for transaction in transactions
    ]


@app.get("/categories")
def categories(db: Session = Depends(get_db)):
    return [
        {"id": category.id, "name": category.name, "type": category.type}
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

    previous_category_id = transaction.category_id
    previous_status = transaction.status
    same_final_category = (
        transaction.status in FINAL_STATUSES and transaction.category_id == category.id
    )

    learning_changed = False
    if transaction.direction == "debit":
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
            learn_from_correction(transaction.payee_raw, category.id, db)
            transaction.learned_category_id = category.id
            learning_changed = True

    if same_final_category and not learning_changed:
        logger.info(
            "transaction_review_idempotent_retry",
            extra={"transaction_id": transaction.id, "category_id": category.id},
        )
        return {
            "transaction_id": transaction.id,
            "status": transaction.status,
            "learned": False,
            "idempotent": True,
        }

    transaction.category_id = category.id
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
    }


@app.get("/exports/reviewed-cashbook.xlsx")
def export_reviewed_cashbook(
    year: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    transactions = _reviewed_transactions_for_year(db, year)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Reviewed Transactions"
    sheet.append(
        [
            "Date",
            "Description",
            "Reference",
            "Money Out",
            "Money In",
            "Balance",
            "Category",
            "Review Status",
        ]
    )

    for transaction in transactions:
        sheet.append(
            [
                transaction.txn_date,
                _excel_safe_text(transaction.payee_raw),
                _excel_safe_text(transaction.reference),
                float(transaction.amount) if transaction.direction == "debit" else None,
                float(transaction.amount) if transaction.direction == "credit" else None,
                float(transaction.balance_after),
                _excel_safe_text(transaction.category.name),
                transaction.status,
            ]
        )

    header_fill = PatternFill("solid", fgColor="0F766E")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = {
        "A": 14,
        "B": 42,
        "C": 24,
        "D": 16,
        "E": 16,
        "F": 16,
        "G": 34,
        "H": 16,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    for row in sheet.iter_rows(min_row=2, min_col=1, max_col=6):
        row[0].number_format = "yyyy-mm-dd"
        for cell in row[3:6]:
            cell.number_format = 'R #,##0.00'

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    record_audit_event(
        db,
        "export.reviewed_cashbook",
        entity_type="financial_year",
        entity_id=year,
        details={"year": year, "transaction_count": len(transactions)},
    )
    db.commit()
    logger.info(
        "reviewed_cashbook_exported",
        extra={"year": year, "transaction_count": len(transactions)},
    )

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="reviewed-cashbook-{year}.xlsx"'
        },
    )


@app.get("/exports/wced-cashbook.xls")
def download_wced_cashbook(
    year: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    template_path = get_wced_template_path()
    if template_path is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "The WCED cashbook template is not configured. Open Setup and upload "
                "the blank WCED .xls template before exporting."
            ),
        )

    transactions = _reviewed_transactions_for_year(db, year)
    records = [
        WcedTransaction(
            txn_date=transaction.txn_date,
            description=transaction.payee_raw,
            amount=transaction.amount,
            direction=transaction.direction,
            category_name=transaction.category.name,
            transaction_id=transaction.id,
        )
        for transaction in transactions
    ]

    try:
        content = build_wced_cashbook(template_path, records)
    except WcedExportError as exc:
        logger.warning(
            "wced_export_validation_failed",
            extra={"year": year, "validation_type": type(exc).__name__},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_audit_event(
        db,
        "export.wced_cashbook",
        entity_type="financial_year",
        entity_id=year,
        details={"year": year, "transaction_count": len(transactions)},
    )
    db.commit()
    logger.info(
        "wced_cashbook_exported",
        extra={"year": year, "transaction_count": len(transactions)},
    )

    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.ms-excel",
        headers={
            "Content-Disposition": f'attachment; filename="wced-cashbook-{year}.xls"'
        },
    )
