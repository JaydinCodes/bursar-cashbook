import re
from decimal import Decimal
from rapidfuzz import process, fuzz
from sqlalchemy.orm import Session

from .models import Rule

SUFFIXES = (" LTD", " PTY", " (PTY)", " CC")


def normalize_payee(raw: str) -> str:
    s = raw.strip().upper()
    s = re.sub(r"\s+", " ", s)
    for suffix in SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def categorize(payee_raw: str, db: Session, fuzzy_threshold: int = 90):
    """
    Returns (category_id, confidence, method).
    method is one of: exact | fuzzy | unknown | no_rules
    A payee can have multiple historical rules (paid for different things at
    different times) -- exact match picks the highest-confidence rule, but
    confidence itself reflects how often that category actually applied.
    """
    norm = normalize_payee(payee_raw)
    exact_rules = db.query(Rule).filter(Rule.payee_pattern == norm).all()

    if exact_rules:
        best = max(exact_rules, key=lambda r: (r.confidence, r.hit_count, -r.id))
        return best.category_id, float(best.confidence), "exact"

    all_rules = db.query(Rule).all()
    if not all_rules:
        return None, 0.0, "no_rules"

    # A payee may legitimately have several categories.  Keep the best rule
    # for each payee before fuzzy matching; a dictionary comprehension would
    # otherwise keep whichever row happened to be returned last.
    choices = {}
    for rule in all_rules:
        current = choices.get(rule.payee_pattern)
        if current is None or (rule.confidence, rule.hit_count, -rule.id) > (
            current.confidence,
            current.hit_count,
            -current.id,
        ):
            choices[rule.payee_pattern] = rule
    match = process.extractOne(norm, choices.keys(), scorer=fuzz.token_sort_ratio)
    if match and match[1] >= fuzzy_threshold:
        rule = choices[match[0]]
        return rule.category_id, (match[1] / 100) * float(rule.confidence), "fuzzy"

    return None, 0.0, "unknown"


def learn_from_correction(payee_raw: str, category_id: int, db: Session) -> Rule:
    """Record one bursar-approved classification and rebalance its rules."""
    payee = normalize_payee(payee_raw)
    rules = db.query(Rule).filter(Rule.payee_pattern == payee).all()
    selected = next((rule for rule in rules if rule.category_id == category_id), None)
    if selected is None:
        selected = Rule(payee_pattern=payee, category_id=category_id, hit_count=0, confidence=0)
        db.add(selected)
        rules.append(selected)
    selected.hit_count += 1
    db.flush()

    total = sum(rule.hit_count for rule in rules)
    for rule in rules:
        rule.confidence = Decimal(rule.hit_count) / Decimal(total)
    return selected
