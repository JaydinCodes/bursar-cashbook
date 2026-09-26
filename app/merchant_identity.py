"""Conservative, stable merchant identity for Standard Bank descriptions."""
from __future__ import annotations

import re


def merchant_key(payee_raw: str | None) -> str:
    """Return a stable merchant identity without altering the raw description.

    Only known bank metadata is removed.  Unknown descriptions remain intact
    (apart from whitespace/case normalization), so unrelated merchants are not
    accidentally merged.
    """
    text = re.sub(r"\s+", " ", (payee_raw or "").strip().upper())
    if not text:
        return ""
    text = re.sub(r"^(?:DL|S2S)\*", "", text)
    text = re.sub(r"\b(?:OUTSTANDING CARD AUTHORISATION|DEBIT CARD PURCHASE FROM)\b.*$", "", text)
    text = re.sub(r"\b\d{4}\*\d{4}\b.*$", "", text)
    text = re.sub(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", "", text)
    text = re.sub(r"\b\d{2}H\d{2}(?::\d{2})?\b", "", text)
    # ZAF/RSA is removed only as a standalone country metadata token.
    text = re.sub(r"\b(?:ZAF|RSA)\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def payee_display(payee_raw: str | None) -> str:
    """Bursar-facing text; never use it for fingerprints or reconciliation."""
    return merchant_key(payee_raw) or re.sub(r"\s+", " ", (payee_raw or "").strip())
