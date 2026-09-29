from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from .bank_parser import parse_statement
from .categorize import categorize
from .models import Statement, Transaction
from .merchant_identity import merchant_key
from .fingerprints import standard_bank_transaction_fingerprint
from .reconciliation import reconcile_statement


def import_statement(
    db: Session,
    *,
    filename: str,
    content: bytes,
    bank: str = "Standard Bank",
) -> Statement:
    """
    Import a bank statement into the database.

    Pipeline:

        parse
        -> reconcile
        -> create Statement
        -> create Transactions
        -> categorize transactions
        -> commit

    Nothing is written if parsing or reconciliation fails.
    """

    if not content:
        raise ValueError("Statement file is empty.")

    if not filename.strip():
        raise ValueError("Statement filename is required.")

    source_hash = hashlib.sha256(content).hexdigest()

    # ---------------------------------------------------------
    # Prevent importing the exact same statement twice
    # ---------------------------------------------------------

    existing = (
        db.query(Statement)
        .filter(Statement.source_hash == source_hash)
        .first()
    )

    if existing is not None:
        raise ValueError("This statement has already been imported.")

    # ---------------------------------------------------------
    # 1. Parse
    # ---------------------------------------------------------

    parsed = parse_statement(
        filename=filename,
        content=content,
        bank=bank,
    )

    # ---------------------------------------------------------
    # 2. Reconcile
    # ---------------------------------------------------------

    reconciliation = reconcile_statement(parsed)

        # ---------------------------------------------------------
    # 3. Determine transactions to import
    # ---------------------------------------------------------

    transactions_to_import = []

    for raw_transaction in parsed.transactions:
        # Statement exports commonly overlap.  Transaction identity must not
        # include the file hash or the same bank entry will be imported twice.
        fingerprint = standard_bank_transaction_fingerprint(raw_transaction)

        existing_transaction = (
            db.query(Transaction)
            .filter(Transaction.fingerprint == fingerprint)
            .first()
        )

        if existing_transaction is not None:
            continue

        transactions_to_import.append(
            (raw_transaction, fingerprint)
        )

    duplicate_count = (
        len(parsed.transactions)
        - len(transactions_to_import)
    )

    # ---------------------------------------------------------
    # 4. Create Statement
    # ---------------------------------------------------------

    statement = Statement(
        bank=bank,
        source_filename=filename,
        source_hash=source_hash,
        period_start=min(
            transaction.txn_date
            for transaction in parsed.transactions
        ),
        period_end=max(
            transaction.txn_date
            for transaction in parsed.transactions
        ),
        financial_year=max(
            transaction.txn_date
            for transaction in parsed.transactions
        ).year,
        opening_balance=reconciliation.opening_balance,
        closing_balance=reconciliation.closing_balance,
        total_debits=reconciliation.total_debits,
        total_credits=reconciliation.total_credits,
        reconciliation_difference=reconciliation.difference,
        reconciliation_status="passed",
        source_transaction_count=len(parsed.transactions),
        imported_transaction_count=len(transactions_to_import),
        duplicate_transaction_count=duplicate_count,
    )

    db.add(statement)
    db.flush()

    # ---------------------------------------------------------
    # 5. Create Transactions
    # ---------------------------------------------------------

    for raw_transaction, fingerprint in transactions_to_import:

        category_id, confidence, method = categorize(
            raw_transaction.description,
            db,
        )

        transaction = Transaction(
            statement_id=statement.id,
            fingerprint=fingerprint,
            source_row=raw_transaction.source_row,
            txn_date=raw_transaction.txn_date,
            payee_raw=raw_transaction.description,
            payee_normalized=raw_transaction.description.strip().upper(),
            merchant_key=merchant_key(raw_transaction.description),
            reference=raw_transaction.reference,
            balance_after=raw_transaction.balance_after,
            amount=raw_transaction.amount,
            direction=raw_transaction.direction,
            suggested_category_id=category_id,
            suggestion_confidence=confidence,
            suggestion_method=method,
            status="pending",
        )

        db.add(transaction)

    db.commit()
    db.refresh(statement)

    return statement


