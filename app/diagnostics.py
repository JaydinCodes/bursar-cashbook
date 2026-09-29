import json
import platform
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy.orm import Session

from .audit import audit_event_to_dict
from .logging_config import LOG_FILE
from .models import AuditEvent, CashbookProfile, CashbookSync, Statement
from .version import APP_VERSION

MAX_LOG_BYTES = 500_000


def _read_log_tail(path: Path, max_bytes: int = MAX_LOG_BYTES) -> bytes:
    if not path.is_file():
        return b""

    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read()


def _statement_summary(statement: Statement) -> dict:
    return {
        "statement_id": statement.id,
        "bank": statement.bank,
        "uploaded_at": statement.uploaded_at.isoformat() if statement.uploaded_at else None,
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        "financial_year": statement.financial_year,
        "source_transaction_count": statement.source_transaction_count,
        "imported_transaction_count": statement.imported_transaction_count,
        "duplicate_transaction_count": statement.duplicate_transaction_count,
        "reconciliation_status": statement.reconciliation_status,
        "reconciliation_difference": str(statement.reconciliation_difference),
    }


def build_diagnostics_zip(db: Session) -> bytes:
    generated_at = datetime.now(timezone.utc)

    diagnostics = {
        "app_version": APP_VERSION,
        "generated_at": generated_at.isoformat(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
    }

    statements = (
        db.query(Statement)
        .order_by(Statement.uploaded_at.desc(), Statement.id.desc())
        .limit(50)
        .all()
    )

    events = (
        db.query(AuditEvent)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(250)
        .all()
    )

    profile = (
        db.query(CashbookProfile)
        .filter(CashbookProfile.active.is_(True))
        .order_by(CashbookProfile.id.desc())
        .first()
    )
    cashbook = {
        "registered": profile is not None,
        "adapter": profile.adapter if profile else None,
        "source_filename": profile.source_filename if profile else None,
        "registered_at": (
            profile.registered_at.isoformat() if profile and profile.registered_at else None
        ),
        "sync_record_count": (
            db.query(CashbookSync)
            .filter(CashbookSync.cashbook_profile_id == profile.id)
            .count()
            if profile
            else 0
        ),
    }

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "diagnostics.json",
            json.dumps(diagnostics, indent=2, sort_keys=True),
        )
        archive.writestr(
            "import-history.json",
            json.dumps([_statement_summary(item) for item in statements], indent=2),
        )
        archive.writestr(
            "cashbook-status.json",
            json.dumps(cashbook, indent=2, sort_keys=True),
        )
        archive.writestr(
            "audit-history.json",
            json.dumps([audit_event_to_dict(item) for item in events], indent=2),
        )

        log_tail = _read_log_tail(LOG_FILE)
        if log_tail:
            archive.writestr("application-log-tail.ndjson", log_tail)

        archive.writestr(
            "README.txt",
            "Ledgerly diagnostic package\n"
            "\n"
            "This package intentionally excludes full bank transaction descriptions, "
            "references, account numbers and complete statement data.\n",
        )

    return output.getvalue()
