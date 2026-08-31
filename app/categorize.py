import re
from decimal import Decimal

from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from .models import Category, Rule

SUFFIXES = (" LTD", " PTY", " (PTY)", " CC")


def normalize_payee(raw: str) -> str:
    normalized = raw.strip().upper()
    normalized = re.sub(r"\s+", " ", normalized)

    for suffix in SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()

    return normalized


def categorize(
    payee_raw: str,
    db: Session,
    fuzzy_threshold: int = 90,
    category_type: str | None = None,
):
    """Return (category_id, confidence, method), optionally scoped by category type."""
    normalized = normalize_payee(payee_raw)
    exact_query = db.query(Rule).filter(Rule.payee_pattern == normalized)
    if category_type is not None:
        exact_query = exact_query.join(Rule.category).filter(Category.type == category_type)
    exact_rules = exact_query.all()

    if exact_rules:
        best = max(exact_rules, key=lambda rule: (rule.confidence, rule.hit_count, -rule.id))
        return best.category_id, float(best.confidence), "exact"

    all_rules_query = db.query(Rule)
    if category_type is not None:
        all_rules_query = all_rules_query.join(Rule.category).filter(Category.type == category_type)
    all_rules = all_rules_query.all()
    if not all_rules:
        return None, 0.0, "no_rules"

    choices: dict[str, Rule] = {}
    for rule in all_rules:
        current = choices.get(rule.payee_pattern)
        if current is None or (rule.confidence, rule.hit_count, -rule.id) > (
            current.confidence,
            current.hit_count,
            -current.id,
        ):
            choices[rule.payee_pattern] = rule

    match = process.extractOne(normalized, choices.keys(), scorer=fuzz.token_sort_ratio)
    if match and match[1] >= fuzzy_threshold:
        rule = choices[match[0]]
        confidence = (match[1] / 100) * float(rule.confidence)
        return rule.category_id, confidence, "fuzzy"

    return None, 0.0, "unknown"


def _rebalance_rules(payee: str, db: Session) -> None:
    rules = db.query(Rule).filter(Rule.payee_pattern == payee).all()
    positive_rules = [rule for rule in rules if rule.hit_count > 0]
    total = sum(rule.hit_count for rule in positive_rules)

    for rule in rules:
        if rule.hit_count <= 0:
            db.delete(rule)
            continue

        rule.confidence = Decimal(rule.hit_count) / Decimal(total)

    db.flush()


def learn_from_correction(payee_raw: str, category_id: int, db: Session) -> Rule:
    """Add one learning vote for a bursar-approved expense classification."""
    payee = normalize_payee(payee_raw)
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
    _rebalance_rules(payee, db)
    return selected


def move_learning_vote(
    payee_raw: str,
    old_category_id: int,
    new_category_id: int,
    db: Session,
) -> None:
    """Move an existing transaction's learning vote without double-counting it."""
    if old_category_id == new_category_id:
        return

    payee = normalize_payee(payee_raw)

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
    _rebalance_rules(payee, db)
