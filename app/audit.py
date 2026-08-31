import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def record_audit_event(
    db: Session,
    event_type: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        details_json=json.dumps(details or {}, sort_keys=True, default=str),
    )
    db.add(event)
    return event


def audit_event_to_dict(event: AuditEvent) -> dict[str, Any]:
    try:
        details = json.loads(event.details_json or "{}")
    except json.JSONDecodeError:
        details = {"raw": event.details_json}

    return {
        "id": event.id,
        "event_type": event.event_type,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "details": details,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
