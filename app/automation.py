"""Conservative automation policy for trusted learned classifications."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .merchant_identity import merchant_key
from .models import Category, Rule

AUTO_APPROVE_MIN_CONFIDENCE = 0.95
AUTO_APPROVE_MIN_HITS = 3


@dataclass(frozen=True)
class TrustedCategoryMatch:
    category_id: int
    confidence: float
    hit_count: int


def trusted_exact_match(
    payee_raw: str,
    db: Session,
    *,
    min_confidence: float = AUTO_APPROVE_MIN_CONFIDENCE,
    min_hits: int = AUTO_APPROVE_MIN_HITS,
    category_type: str | None = None,
) -> TrustedCategoryMatch | None:
    """Return a trusted exact historical rule, otherwise None.

    Automatic approval is intentionally limited to exact normalized payee
    matches with strong historical consistency and enough prior observations.
    Fuzzy matches are never auto-approved.
    """
    payee = merchant_key(payee_raw)
    query = db.query(Rule).filter(Rule.payee_pattern == payee)
    if category_type is not None:
        query = query.join(Rule.category).filter(Category.type == category_type)
    rules = query.all()
    if not rules:
        return None

    best = max(rules, key=lambda rule: (rule.confidence, rule.hit_count, -rule.id))
    confidence = float(best.confidence)

    if confidence < min_confidence or best.hit_count < min_hits:
        return None

    return TrustedCategoryMatch(
        category_id=best.category_id,
        confidence=confidence,
        hit_count=best.hit_count,
    )
