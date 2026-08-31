from hashlib import sha256

from .bank_parser import RawTransaction
from .categorize import normalize_payee


def standard_bank_transaction_fingerprint(transaction: RawTransaction) -> str:
    """Build a deterministic identity that survives overlapping statement exports."""
    canonical = "|".join(
        (
            "STANDARD_BANK",
            transaction.txn_date.isoformat(),
            transaction.direction,
            f"{transaction.amount:.2f}",
            f"{transaction.balance_after:.2f}",
            (transaction.reference or "").strip().upper(),
            normalize_payee(transaction.description),
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
