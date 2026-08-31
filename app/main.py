from datetime import date
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .bank_parser import StatementParseError, parse_statement
from .categorize import (
    categorize,
    learn_from_correction,
    move_learning_vote,
    normalize_payee,
)
from .db import SessionLocal, init_db
from .fingerprints import standard_bank_transaction_fingerprint
from .models import Category, Statement, Transaction
from .reconciliation import StatementReconciliationError, reconcile_statement
from .wced_export import (
    WcedExportError,
    WcedTransaction,
    export_wced_cashbook as build_wced_cashbook,
)

app = FastAPI(title="Bursar Cashbook Automation")
REVIEW_PAGE = Path(__file__).parent / "static" / "review.html"
FINAL_STATUSES = ("approved", "corrected")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class ReviewDecision(BaseModel):
    category_id: int
    learn: bool = True


class CategoryCreate(BaseModel):
    name: str
    type: str = "expense"


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "bank": "Standard Bank"}


@app.get("/", include_in_schema=False)
def review_page():
    return FileResponse(REVIEW_PAGE)


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
            detail=(
                f"{pending} transaction(s) for {year} still require review before export."
            ),
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

    source_hash = sha256(content).hexdigest()
    duplicate_statement = (
        db.query(Statement).filter(Statement.source_hash == source_hash).first()
    )
    if duplicate_statement:
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
        raise HTTPException(
            status_code=409,
            detail="Every transaction in this statement has already been imported.",
        )

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

    db.commit()
    db.refresh(statement)

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
        for category in (
            db.query(Category).order_by(Category.type, Category.name).all()
        )
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

    existing = (
        db.query(Category).filter_by(name=name, type=category_type).first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="That category already exists.")

    category = Category(name=name, type=category_type)
    db.add(category)
    db.commit()
    db.refresh(category)

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

    db.commit()

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

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="reviewed-cashbook-{year}.xlsx"'
            )
        },
    )


@app.get("/exports/wced-cashbook.xls")
def download_wced_cashbook(
    year: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    template_path = os.getenv("WCED_TEMPLATE_PATH")
    if not template_path:
        raise HTTPException(
            status_code=503,
            detail=(
                "WCED_TEMPLATE_PATH is not configured. Set it to the blank "
                "WCED .xls cashbook template."
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
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.ms-excel",
        headers={
            "Content-Disposition": f'attachment; filename="wced-cashbook-{year}.xls"'
        },
    )
