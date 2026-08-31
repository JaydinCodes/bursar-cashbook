import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import Engine

BACKUP_DIR = Path(os.getenv("CASHBOOK_BACKUP_DIR", "backups"))
BACKUP_RETENTION = max(1, int(os.getenv("CASHBOOK_BACKUP_RETENTION", "10")))


def _safe_reason(reason: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", reason.strip()).strip("-")
    return cleaned or "backup"


def _sqlite_file_from_engine(engine: Engine) -> Path | None:
    if engine.url.get_backend_name() != "sqlite":
        return None

    database = engine.url.database
    if not database or database == ":memory:":
        return None

    return Path(database).resolve()


def _prune_backups() -> None:
    backups = sorted(
        BACKUP_DIR.glob("cashbook-*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in backups[BACKUP_RETENTION:]:
        path.unlink(missing_ok=True)


def create_sqlite_backup(
    engine: Engine,
    reason: str,
    *,
    once_per_day: bool = False,
) -> Path | None:
    source_path = _sqlite_file_from_engine(engine)
    if source_path is None or not source_path.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    safe_reason = _safe_reason(reason)

    if once_per_day:
        pattern = f"cashbook-{now:%Y%m%d}-*-{safe_reason}.db"
        existing = sorted(BACKUP_DIR.glob(pattern))
        if existing:
            return existing[-1]

    destination = BACKUP_DIR / (
        f"cashbook-{now:%Y%m%d-%H%M%S-%f}-{safe_reason}.db"
    )

    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    _prune_backups()
    return destination
