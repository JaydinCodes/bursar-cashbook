import os
import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .models import Base
from .config import DATABASE_PATH, ensure_application_directories
from .merchant_identity import merchant_key

ensure_application_directories()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH.as_posix()}")
CONNECT_ARGS = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=CONNECT_ARGS,
    pool_pre_ping=True,
)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # SQLite's create_all does not add columns to an installed database.
    if not DATABASE_URL.startswith("sqlite"):
        return
    from sqlalchemy import text
    with engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(transactions)"))}
        if "merchant_key" not in columns:
            connection.execute(text("ALTER TABLE transactions ADD COLUMN merchant_key VARCHAR"))
        if "cashbook_narrative" not in columns:
            connection.execute(text("ALTER TABLE transactions ADD COLUMN cashbook_narrative VARCHAR"))
        rule_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(rules)"))}
        if "cashbook_narrative" not in rule_columns:
            connection.execute(text("ALTER TABLE rules ADD COLUMN cashbook_narrative VARCHAR"))
        sync_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(cashbook_syncs)"))}
        if "backup_filename" not in sync_columns:
            connection.execute(text("ALTER TABLE cashbook_syncs ADD COLUMN backup_filename VARCHAR"))
        rows = connection.execute(text("SELECT id, payee_raw FROM transactions WHERE merchant_key IS NULL OR merchant_key = ''")).all()
        for row in rows:
            connection.execute(text("UPDATE transactions SET merchant_key=:key WHERE id=:id"), {"key": merchant_key(row.payee_raw), "id": row.id})
        # Rules historically used normalized raw descriptions. Re-key them by
        # merchant identity, merging only equivalent category votes. This is
        # idempotent: subsequent runs derive the same grouped rows.
        rules = connection.execute(text("SELECT payee_pattern, category_id, hit_count FROM rules")).all()
        if rules:
            grouped: dict[tuple[str, int], int] = {}
            for rule in rules:
                key = merchant_key(rule.payee_pattern) or rule.payee_pattern
                grouped[(key, rule.category_id)] = grouped.get((key, rule.category_id), 0) + rule.hit_count
            connection.execute(text("DELETE FROM rules"))
            totals: dict[str, int] = {}
            for (key, _category_id), hits in grouped.items():
                totals[key] = totals.get(key, 0) + hits
            for (key, category_id), hits in grouped.items():
                connection.execute(
                    text("INSERT INTO rules (payee_pattern, category_id, hit_count, confidence) VALUES (:key,:category,:hits,:confidence)"),
                    {"key": key, "category": category_id, "hits": hits, "confidence": hits / totals[key] if totals[key] else 0},
                )
            logging.getLogger("cashbook").info(
                "merchant_rule_migration_completed",
                extra={"legacy_rule_count": len(rules), "merchant_rule_count": len(grouped)},
            )
