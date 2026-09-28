import re
from decimal import Decimal

from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from .models import Category, Rule
from .merchant_identity import merchant_key


SUFFIXES = (" LTD", " PTY", " (PTY)", " CC")


# ---------------------------------------------------------------------------
# Merchant patterns
# ---------------------------------------------------------------------------
# These are deliberately conservative. They provide initial suggestions,
# while the existing Rule system remains responsible for learned decisions.
#
# The category names must match categories already present in the database.
# ---------------------------------------------------------------------------

MERCHANT_PATTERNS = {
    # Transport
    "UBER": ("Excursion Transport", "expense"),
    "BOLT": ("Excursion Transport", "expense"),

    # Food / refreshments
    "PANAROTTIS": ("Refreshments", "expense"),

    # Telecommunications
    "MTN": ("H Telecom", "expense"),
    "VODACOM": ("H Telecom", "expense"),
    "TELKOM": ("H Telecom", "expense"),

    # Municipal services
    "ELECTRICITY": ("Municipal Services", "expense"),

    # Known school suppliers
    "NASHUA": ("Nashua", "expense"),
    "RENTOKIL": ("Pest control", "expense"),

    # Bank charges
    "BANK CHARGE": ("Bank Charges", "expense"),
    "MONTHLY FEE": ("Bank Charges", "expense"),
    "CASH WITHDRAWAL FEE": ("Bank Charges", "expense"),
    "FEE IMMEDIATE PAYMENT": ("Bank Charges", "expense"),
    "FEE-PIN RESET": ("Bank Charges", "expense"),
}


SCHOOL_SIGNALS = {
    "INK AND CATRIDGES": (
        "INK",
        "INK CARTRIDGE",
        "INK CARTRIDGES",
        "TONER",
        "TONER CARTRIDGE",
        "CARTRIDGE",
    ),

    "Local Purchases: Stationery": (
        "STATIONERY",
        "PAPER",
        "PENS",
        "PENCILS",
        "MARKERS",
        "FILES",
        "FOLDERS",
        "ENVELOPES",
    ),

    "Copy type": (
        "PHOTOCOPY",
        "PHOTOCOPYING",
        "COPY TYPE",
        "COPYING",
    ),

    "Local Purchases: Cleaning Material": (
        "CLEANING",
        "DETERGENT",
        "BLEACH",
        "SOAP",
        "SANITIZER",
        "DISINFECTANT",
        "MOP",
        "BROOM",
    ),

    "Security": (
        "SECURITY",
        "ALARM",
        "CCTV",
    ),

    "Gardening": (
        "GARDEN",
        "GARDENING",
        "LANDSCAPING",
        "LAWN",
    ),

    "Pest control": (
        "PEST",
        "PEST CONTROL",
        "FUMIGATION",
        "EXTERMINAT",
    ),

    "IT EQUIPMENT": (
        "LAPTOP",
        "COMPUTER",
        "MONITOR",
        "KEYBOARD",
        "MOUSE",
        "PRINTER",
        "PROJECTOR",
    ),

    "LTSM": (
        "TEXTBOOK",
        "TEXTBOOKS",
        "WORKBOOK",
        "WORKBOOKS",
        "LEARNING MATERIAL",
        "TEACHING MATERIAL",
    ),

    "Day To Day Maintenance": (
        "MAINTENANCE",
        "REPAIR",
        "REPAIRS",
        "PLUMBING",
        "ELECTRICAL",
        "HARDWARE",
    ),
}
def normalize_payee(raw: str) -> str:
    """
    Normalize a bank transaction description into a comparable string.
    """
    if not raw:
        return ""

    normalized = raw.strip().upper()
    normalized = re.sub(r"\s+", " ", normalized)

    for suffix in SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()

    return normalized


def _merchant_text(raw: str) -> str:
    """
    Extract useful merchant text from noisy bank descriptions.

    Examples:

        DL*UBER CPT ZAF 07-09-2026
        -> UBER

        PANAROTTIS PAROW
        -> PANAROTTIS

        WOOLWORTHS CAPE TOWN
        -> WOOLWORTHS
    """
    normalized = normalize_payee(raw)

    # Remove common bank/provider prefixes.
    normalized = re.sub(r"^[A-Z0-9]{1,4}\*", "", normalized)

    # Remove common transaction noise.
    normalized = re.sub(
        r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
        " ",
        normalized,
    )

    normalized = re.sub(
        r"\b\d{2}H\d{2}(?::\d{2})?\b",
        " ",
        normalized,
    )

    normalized = re.sub(
        r"\b(ZAF|RSA)\b",
        " ",
        normalized,
    )

    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized

def _school_signal_match(
    payee_raw: str,
    db: Session,
    category_type: str | None = None,
):
    merchant_text = _merchant_text(payee_raw)

    for category_name, signals in SCHOOL_SIGNALS.items():
        if any(signal in merchant_text for signal in signals):
            category = (
                db.query(Category)
                .filter(
                    Category.name == category_name,
                    Category.type == category_type,
                )
                .first()
            )

            if category is not None:
                return category.id, 0.90, "school_signal"

    return None
def _find_category(
    db: Session,
    category_name: str,
    category_type: str,
):
    """
    Find an existing category without creating database records.
    """
    return (
        db.query(Category)
        .filter(
            Category.name.ilike(category_name),
            Category.type == category_type,
        )
        .first()
    )


def _baseline_match(
    payee_raw: str,
    db: Session,
    category_type: str | None,
):
    """
    Match common merchants using conservative built-in patterns.
    """
    merchant = _merchant_text(payee_raw)

    if not merchant:
        return None

    for pattern, (category_name, expected_type) in MERCHANT_PATTERNS.items():
        if category_type is not None and expected_type != category_type:
            continue

        if pattern in merchant:
            category = _find_category(
                db,
                category_name,
                expected_type,
            )

            if category is not None:
                return category.id, 0.95, "merchant"

    return None


# ---------------------------------------------------------------------------
# Main categorization
# ---------------------------------------------------------------------------

def categorize(
    payee_raw: str,
    db: Session,
    fuzzy_threshold: int = 90,
    category_type: str | None = None,
):
    """
    Return:

        (category_id, confidence, method)

    Methods:

        exact
        merchant
        fuzzy
        no_rules
        unknown
    """

    normalized = merchant_key(payee_raw)

    if not normalized:
        return None, 0.0, "unknown"

    # ------------------------------------------------------------------
    # 1. Exact learned rule
    # ------------------------------------------------------------------

    exact_query = db.query(Rule).filter(
        Rule.payee_pattern == normalized
    )

    if category_type is not None:
        exact_query = (
            exact_query
            .join(Rule.category)
            .filter(Category.type == category_type)
        )

    exact_rules = exact_query.all()

    if exact_rules:
        best = max(
            exact_rules,
            key=lambda rule: (
                rule.confidence,
                rule.hit_count,
                -rule.id,
            ),
        )

        return (
            best.category_id,
            float(best.confidence),
            "exact",
        )

    # ------------------------------------------------------------------
    # 2. Known merchant baseline
    # ------------------------------------------------------------------

    baseline = _baseline_match(
        payee_raw,
        db,
        category_type,
    )

    if baseline is not None:
        return baseline

    school_match = _school_signal_match(
    payee_raw,
    db,
    category_type=category_type,
)

    if school_match is not None:
        return school_match

    # ------------------------------------------------------------------
    # 3. Fuzzy match against learned rules
    # ------------------------------------------------------------------

    all_rules_query = db.query(Rule)

    if category_type is not None:
        all_rules_query = (
            all_rules_query
            .join(Rule.category)
            .filter(Category.type == category_type)
        )

    all_rules = all_rules_query.all()

    if not all_rules:
        return None, 0.0, "no_rules"

    choices: dict[str, Rule] = {}

    for rule in all_rules:
        current = choices.get(rule.payee_pattern)

        if current is None or (
            rule.confidence,
            rule.hit_count,
            -rule.id,
        ) > (
            current.confidence,
            current.hit_count,
            -current.id,
        ):
            choices[rule.payee_pattern] = rule

    match = process.extractOne(
        normalized,
        choices.keys(),
        scorer=fuzz.token_sort_ratio,
    )

    if match and match[1] >= fuzzy_threshold:
        rule = choices[match[0]]

        confidence = (
            (match[1] / 100)
            * float(rule.confidence)
        )

        return (
            rule.category_id,
            confidence,
            "fuzzy",
        )

    return None, 0.0, "unknown"


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------


def refresh_pending_suggestions(db: Session) -> int:
    """
    Re-run categorization for all pending transactions.

    This only updates suggestions. It does not approve or correct
    any transaction.
    """
    from .models import Transaction

    transactions = (
        db.query(Transaction)
        .filter(Transaction.status == "pending")
        .all()
    )

    updated = 0

    for transaction in transactions:
        category_type = (
            "expense"
            if transaction.direction == "debit"
            else "income"
        )

        category_id, confidence, method = categorize(
            transaction.payee_raw,
            db,
            category_type=category_type,
        )

        transaction.suggested_category_id = category_id
        transaction.suggestion_confidence = confidence
        transaction.suggestion_method = method

        updated += 1

    db.flush()
    return updated

def _rebalance_rules(payee: str, db: Session) -> None:
    rules = (
        db.query(Rule)
        .filter(Rule.payee_pattern == payee)
        .all()
    )

    positive_rules = [
        rule
        for rule in rules
        if rule.hit_count > 0
    ]

    total = sum(
        rule.hit_count
        for rule in positive_rules
    )

    for rule in rules:
        if rule.hit_count <= 0:
            db.delete(rule)
            continue

        rule.confidence = (
            Decimal(rule.hit_count)
            / Decimal(total)
        )

    db.flush()


def learn_from_correction(
    payee_raw: str,
    category_id: int,
    db: Session,
    cashbook_narrative: str | None = None,
) -> Rule:
    """
    Add one learning vote for a bursar-approved classification.
    """

    payee = merchant_key(payee_raw)

    selected = (
        db.query(Rule)
        .filter(
            Rule.payee_pattern == payee,
            Rule.category_id == category_id,
        )
        .first()
    )

    if selected is None:
        selected = Rule(
            payee_pattern=payee,
            category_id=category_id,
            hit_count=0,
            confidence=0,
        )

        db.add(selected)
        db.flush()

    selected.hit_count += 1
    if cashbook_narrative:
        selected.cashbook_narrative = cashbook_narrative.strip()

    _rebalance_rules(
        payee,
        db,
    )

    return selected


def move_learning_vote(
    payee_raw: str,
    old_category_id: int,
    new_category_id: int,
    db: Session,
) -> None:
    """
    Move an existing transaction's learning vote without double-counting it.
    """

    if old_category_id == new_category_id:
        return

    payee = merchant_key(payee_raw)

    old_rule = (
        db.query(Rule)
        .filter(
            Rule.payee_pattern == payee,
            Rule.category_id == old_category_id,
        )
        .first()
    )

    if old_rule is not None and old_rule.hit_count > 0:
        old_rule.hit_count -= 1

    new_rule = (
        db.query(Rule)
        .filter(
            Rule.payee_pattern == payee,
            Rule.category_id == new_category_id,
        )
        .first()
    )

    if new_rule is None:
        new_rule = Rule(
            payee_pattern=payee,
            category_id=new_category_id,
            hit_count=0,
            confidence=0,
        )

        db.add(new_rule)
        db.flush()

    new_rule.hit_count += 1

    _rebalance_rules(
        payee,
        db,
    )
