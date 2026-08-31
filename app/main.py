from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .bank_parser import StatementParseError, parse_statement
from .categorize import categorize, learn_from_correction, normalize_payee
from .db import SessionLocal, init_db
from .models import Category, Statement, Transaction
from .wced_export import WcedExportError, WcedTransaction, export_wced_cashbook

app = FastAPI(title="Bursar Cashbook Automation")
REVIEW_PAGE = Path(__file__).parent / "static" / "review.html"


class ReviewDecision(BaseModel):
    category_id: int
    learn: bool = True


class CategoryCreate(BaseModel):
    name: str
    type: str = "expense"


@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def review_page():
    return FileResponse(REVIEW_PAGE)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/statements/upload", status_code=201)
async def upload_statement(
    file: UploadFile = File(...),
    bank: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Import a CSV or legacy XLS statement into the review queue."""
    filename = file.filename or "statement"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded statement is empty.")
    try:
        raw_transactions = parse_statement(filename, content, bank=bank)
    except StatementParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    source_hash = sha256(content).hexdigest()
    duplicate = db.query(Statement).filter(Statement.source_hash == source_hash).first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"This statement was already imported as statement {duplicate.id}.")

    statement = Statement(
        bank=bank.strip() if bank else None,
        source_filename=filename,
        source_hash=source_hash,
        period_start=min(transaction.txn_date for transaction in raw_transactions),
        period_end=max(transaction.txn_date for transaction in raw_transactions),
    )
    db.add(statement)
    db.flush()

    pending = 0
    for raw in raw_transactions:
        category_id, confidence, method = categorize(raw.description, db)
        auto_approved = method == "exact" and confidence >= 0.90
        transaction = Transaction(
            statement_id=statement.id,
            txn_date=raw.txn_date,
            payee_raw=raw.description,
            payee_normalized=normalize_payee(raw.description),
            amount=raw.amount,
            direction=raw.direction,
            category_id=category_id,
            confidence=confidence if category_id else None,
            status="auto" if auto_approved else "pending",
        )
        if not auto_approved:
            pending += 1
        db.add(transaction)
    db.commit()
    return {
        "statement_id": statement.id,
        "transactions_imported": len(raw_transactions),
        "pending_review": pending,
        "period": {"start": statement.period_start.isoformat(), "end": statement.period_end.isoformat()},
    }


@app.get("/transactions/pending")
def pending_transactions(db: Session = Depends(get_db)):
    transactions = (
        db.query(Transaction)
        .filter(Transaction.status == "pending")
        .order_by(Transaction.confidence.asc().nullsfirst(), Transaction.txn_date.desc())
        .all()
    )
    return [
        {
            "id": transaction.id,
            "date": transaction.txn_date.isoformat(),
            "description": transaction.payee_raw,
            "amount": str(transaction.amount),
            "direction": transaction.direction,
            "suggested_category_id": transaction.category_id,
            "suggested_category": transaction.category.name if transaction.category else None,
            "confidence": float(transaction.confidence) if transaction.confidence is not None else None,
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
        raise HTTPException(status_code=422, detail="Category type must be expense or income.")
    existing = db.query(Category).filter_by(name=name, type=category_type).first()
    if existing:
        raise HTTPException(status_code=409, detail="That category already exists.")
    category = Category(name=name, type=category_type)
    db.add(category)
    db.commit()
    db.refresh(category)
    return {"id": category.id, "name": category.name, "type": category.type}


@app.post("/transactions/{transaction_id}/review")
def review_transaction(transaction_id: int, decision: ReviewDecision, db: Session = Depends(get_db)):
    transaction = db.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    category = db.get(Category, decision.category_id)
    if category is None:
        raise HTTPException(status_code=422, detail="Category not found.")

    changed = transaction.category_id != category.id
    transaction.category_id = category.id
    transaction.status = "manual" if changed else "vetted"
    transaction.confidence = 1.0
    if decision.learn:
        learn_from_correction(transaction.payee_raw, category.id, db)
    db.commit()
    return {"transaction_id": transaction.id, "status": transaction.status, "learned": decision.learn}


@app.get("/exports/reviewed-cashbook.xlsx")
def export_reviewed_cashbook(db: Session = Depends(get_db)):
    """Download bursar-reviewed transactions in a clean cashbook workbook."""
    transactions = (
        db.query(Transaction)
        .join(Category)
        .filter(Transaction.status.in_(("vetted", "manual")))
        .order_by(Transaction.txn_date, Transaction.id)
        .all()
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Reviewed Transactions"
    sheet.append(["Date", "Description", "Money Out", "Money In", "Category", "Review Status"])
    for transaction in transactions:
        sheet.append(
            [
                transaction.txn_date,
                transaction.payee_raw,
                float(transaction.amount) if transaction.direction == "debit" else None,
                float(transaction.amount) if transaction.direction == "credit" else None,
                transaction.category.name,
                transaction.status,
            ]
        )

    header_fill = PatternFill("solid", fgColor="0F766E")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.column_dimensions["A"].width = 14
    sheet.column_dimensions["B"].width = 42
    sheet.column_dimensions["C"].width = 16
    sheet.column_dimensions["D"].width = 16
    sheet.column_dimensions["E"].width = 34
    sheet.column_dimensions["F"].width = 16
    for row in sheet.iter_rows(min_row=2, min_col=1, max_col=4):
        row[0].number_format = "yyyy-mm-dd"
        row[2].number_format = 'R #,##0.00'
        row[3].number_format = 'R #,##0.00'

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="reviewed-cashbook.xlsx"'},
    )


@app.get("/exports/wced-cashbook.xls")
def export_wced_cashbook(db: Session = Depends(get_db)):
    """Populate a configured blank WCED template with bursar-reviewed entries."""
    template_path = os.getenv("WCED_TEMPLATE_PATH")
    if not template_path:
        raise HTTPException(
            status_code=503,
            detail="WCED_TEMPLATE_PATH is not configured. Set it to a blank WCED .xls cashbook template.",
        )
    transactions = (
        db.query(Transaction)
        .join(Category)
        .filter(Transaction.status.in_(("vetted", "manual")))
        .order_by(Transaction.txn_date, Transaction.id)
        .all()
    )
    records = [
        WcedTransaction(
            txn_date=transaction.txn_date,
            description=transaction.payee_raw,
            amount=float(transaction.amount),
            direction=transaction.direction,
            category_name=transaction.category.name,
            transaction_id=transaction.id,
        )
        for transaction in transactions
    ]
    try:
        content = export_wced_cashbook(template_path, records)
    except WcedExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": 'attachment; filename="wced-cashbook-import.xls"'},
    )
