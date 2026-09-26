from sqlalchemy.orm import Session

from .categorize import learn_from_correction
from .models import Transaction


def approve_transaction(
    db: Session,
    transaction_id: int,
) -> Transaction:
    transaction = (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id)
        .one_or_none()
    )

    if transaction is None:
        raise ValueError("Transaction not found.")

    if transaction.status not in {"pending", "corrected"}:
        raise ValueError(
            f"Transaction cannot be approved from status "
            f"'{transaction.status}'."
        )

    if transaction.category_id is None:
        if transaction.suggested_category_id is None:
            raise ValueError(
                "Transaction has no category to approve."
            )

        transaction.category_id = transaction.suggested_category_id

    transaction.status = "approved"

    db.commit()
    db.refresh(transaction)

    return transaction


def correct_transaction(
    db: Session,
    transaction_id: int,
    category_id: int,
) -> Transaction:
    transaction = (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id)
        .one_or_none()
    )

    if transaction is None:
        raise ValueError("Transaction not found.")

    if transaction.status not in {"pending", "approved", "corrected"}:
        raise ValueError(
            f"Transaction cannot be corrected from status "
            f"'{transaction.status}'."
        )

    transaction.category_id = category_id
    transaction.learned_category_id = category_id
    transaction.status = "corrected"

    learn_from_correction(
        transaction.payee_normalized,
        category_id,
        db,
    )

    db.commit()
    db.refresh(transaction)

    return transaction


def ready_for_cashbook(
    db: Session,
) -> list[Transaction]:
    return (
        db.query(Transaction)
        .filter(
            Transaction.status.in_(
                ["approved", "corrected"]
            )
        )
        .order_by(
            Transaction.txn_date,
            Transaction.id,
        )
        .all()
    )