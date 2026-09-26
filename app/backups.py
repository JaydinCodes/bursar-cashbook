import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import Engine
from .config import DATABASE_BACKUP_DIR

BACKUP_DIR = DATABASE_BACKUP_DIR
BACKUP_RETENTION = max(1, int(os.getenv("CASHBOOK_BACKUP_RETENTION", "10")))
BACKUP_NAME_RE = re.compile(r"^cashbook-\d{8}-\d{6}-\d{6}-[A-Za-z0-9_-]+\.db$")


def _safe_reason(reason: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", reason.strip()).strip("-")
    return cleaned or "backup"


def sqlite_file_from_engine(engine: Engine) -> Path | None:
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
    source_path = sqlite_file_from_engine(engine)
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


def list_sqlite_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []

    items = []
    for path in sorted(
        BACKUP_DIR.glob("cashbook-*.db"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        stat = path.stat()
        items.append(
            {
                "filename": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            }
        )
    return items


def _resolve_backup(filename: str) -> Path:
    if not BACKUP_NAME_RE.fullmatch(filename):
        raise ValueError("Invalid backup filename.")

    backup_root = BACKUP_DIR.resolve()
    path = (BACKUP_DIR / filename).resolve()
    if path.parent != backup_root or not path.is_file():
        raise ValueError("Backup was not found.")
    return path


def _validate_sqlite_backup(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("Backup failed SQLite integrity validation.")

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"categories", "rules", "statements", "transactions"}
        if not required.issubset(tables):
            raise ValueError("Backup is not a valid Bursar Cashbook database.")
    finally:
        connection.close()


def restore_sqlite_backup(engine: Engine, filename: str) -> Path:
    target_path = sqlite_file_from_engine(engine)
    if target_path is None:
        raise ValueError("Backup restore is available only for the local SQLite prototype.")

    backup_path = _resolve_backup(filename)
    _validate_sqlite_backup(backup_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    engine.dispose()

    source = sqlite3.connect(str(backup_path))
    target = sqlite3.connect(str(target_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    engine.dispose()
    return backup_path
