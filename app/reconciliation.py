from dataclasses import dataclass
from decimal import Decimal

from .bank_parser import ParsedStatement, RawTransaction

CENT = Decimal("0.01")


class StatementReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class ReconciliationResult:
    opening_balance: Decimal
    closing_balance: Decimal
    total_debits: Decimal
    total_credits: Decimal
    calculated_closing_balance: Decimal
    difference: Decimal
    transaction_order: str


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _signed_amount(transaction: RawTransaction) -> Decimal:
    return transaction.amount if transaction.direction == "credit" else -transaction.amount


def _validate_order(
    transactions: list[RawTransaction],
    explicit_opening_balance: Decimal | None,
) -> tuple[Decimal, Decimal] | None:
    first = transactions[0]
    opening_balance = (
        explicit_opening_balance
        if explicit_opening_balance is not None
        else first.balance_after - _signed_amount(first)
    )
    running_balance = _money(opening_balance)

    for transaction in transactions:
        running_balance = _money(running_balance + _signed_amount(transaction))
        if running_balance != _money(transaction.balance_after):
            return None

    return _money(opening_balance), running_balance


def _possible_orders(
    transactions: list[RawTransaction],
) -> list[tuple[str, list[RawTransaction]]]:
    dates = [transaction.txn_date for transaction in transactions]
    non_decreasing = all(left <= right for left, right in zip(dates, dates[1:]))
    non_increasing = all(left >= right for left, right in zip(dates, dates[1:]))

    if non_decreasing and not non_increasing:
        return [("ascending", transactions)]

    if non_increasing and not non_decreasing:
        return [("descending", list(reversed(transactions)))]

    # Same-day-only statements can be ambiguous by date; try source order first.
    return [
        ("ascending", transactions),
        ("descending", list(reversed(transactions))),
    ]


def reconcile_statement(parsed: ParsedStatement) -> ReconciliationResult:
    transactions = parsed.transactions

    total_debits = _money(
        sum(
            (txn.amount for txn in transactions if txn.direction == "debit"),
            Decimal("0"),
        )
    )
    total_credits = _money(
        sum(
            (txn.amount for txn in transactions if txn.direction == "credit"),
            Decimal("0"),
        )
    )

    for order_name, ordered in _possible_orders(transactions):
        balances = _validate_order(ordered, parsed.explicit_opening_balance)
        if balances is None:
            continue

        opening_balance, closing_balance = balances

        if (
            parsed.explicit_closing_balance is not None
            and closing_balance != _money(parsed.explicit_closing_balance)
        ):
            continue

        calculated_closing = _money(
            opening_balance + total_credits - total_debits
        )
        difference = _money(calculated_closing - closing_balance)

        if difference != Decimal("0.00"):
            continue

        return ReconciliationResult(
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_debits=total_debits,
            total_credits=total_credits,
            calculated_closing_balance=calculated_closing,
            difference=difference,
            transaction_order=order_name,
        )

    raise StatementReconciliationError(
        "The statement does not reconcile. At least one running balance, debit, "
        "credit, opening balance or closing balance is inconsistent. Import cancelled."
    )
