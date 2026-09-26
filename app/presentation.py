"""Conservative, human-facing representations of bank transaction fields."""

from __future__ import annotations

import re
from .merchant_identity import payee_display

_CARD_NOISE = re.compile(r"\s+(?:\d{2}-\d{2}-\d{4}|\d{2} [A-Z]{3})(?:\s+\d{2}H\d{2}:\d{2})?.*$", re.IGNORECASE)
_CARD_PREFIX = re.compile(r"^(?:DL\*|S2S\*)", re.IGNORECASE)
_CARD_SUFFIX = re.compile(r"\s+\d{4}\*\d{4}.*$", re.IGNORECASE)


def display_payee(raw: str) -> str:
    """Remove only known Standard Bank card metadata; retain unknown text intact."""
    return payee_display(raw) or "Unknown payee"
    value = " ".join((raw or "").split())
    candidate = _CARD_PREFIX.sub("", value)
    candidate = _CARD_SUFFIX.sub("", candidate)
    candidate = _CARD_NOISE.sub("", candidate).strip(" -")
    if candidate and len(candidate) >= 2 and candidate != value:
        return candidate
    return value or "Unknown payee"


def display_reference(reference: str | None) -> str | None:
    """A dedicated bank reference is authoritative; never invent one."""
    value = " ".join((reference or "").split())
    return value or None
