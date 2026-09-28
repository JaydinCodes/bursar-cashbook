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


def _rule_narrative(value: object) -> str | None:
    """Return a meaningful learned narrative without changing its wording."""
    if value is None:
        return None
    narrative = str(value).strip()
    return narrative or None


def _migrate_legacy_rules(connection, text) -> None:
    """Re-key only legacy rules, preserving learning data in place.

    Older releases keyed rules by the full bank wording.  New releases use a
    stable merchant key.  Unlike the original migration, this never rebuilds
    the entire table: canonical rules remain untouched and only legacy groups
    are merged.  A group with conflicting narratives is intentionally left
    unchanged so a bursar's wording is never selected arbitrarily.
    """
    rules = [
        dict(row._mapping)
        for row in connection.execute(
            text(
                "SELECT id, payee_pattern, category_id, hit_count, confidence, "
                "cashbook_narrative FROM rules ORDER BY id"
            )
        )
    ]
    if not rules:
        return

    keyed_rules: list[dict] = []
    legacy_keys: set[str] = set()
    for rule in rules:
        key = merchant_key(rule["payee_pattern"])
        if not key:
            # A non-empty payee_pattern should always have a merchant key, but
            # preserving an unexpected legacy value is safer than discarding it.
            continue
        rule["merchant_key"] = key
        keyed_rules.append(rule)
        if rule["payee_pattern"] != key:
            legacy_keys.add(key)

    migrated_rules = 0
    skipped_conflicts = 0
    for key in sorted(legacy_keys):
        key_rules = [rule for rule in keyed_rules if rule["merchant_key"] == key]
        by_category: dict[int, list[dict]] = {}
        for rule in key_rules:
            by_category.setdefault(rule["category_id"], []).append(rule)

        conflicting_categories = []
        for category_id, category_rules in by_category.items():
            narratives = {
                narrative
                for narrative in (_rule_narrative(rule["cashbook_narrative"]) for rule in category_rules)
                if narrative is not None
            }
            if len(narratives) > 1:
                conflicting_categories.append(category_id)

        if conflicting_categories:
            skipped_conflicts += 1
            logging.getLogger("cashbook").warning(
                "merchant_rule_migration_conflict_skipped",
                extra={
                    "merchant_key": key,
                    "category_ids": conflicting_categories,
                    "rule_count": len(key_rules),
                },
            )
            continue

        total_hits = sum(int(rule["hit_count"] or 0) for rule in key_rules)
        # Delete duplicate rows first, then update the surviving row in place.
        # This avoids uniqueness collisions while retaining a stable rule id.
        migrations: list[tuple[dict, int, str | None]] = []
        for category_id, category_rules in by_category.items():
            canonical = next(
                (rule for rule in category_rules if rule["payee_pattern"] == key),
                category_rules[0],
            )
            for duplicate in category_rules:
                if duplicate["id"] != canonical["id"]:
                    connection.execute(text("DELETE FROM rules WHERE id=:id"), {"id": duplicate["id"]})
            narrative = next(
                (
                    _rule_narrative(rule["cashbook_narrative"])
                    for rule in category_rules
                    if _rule_narrative(rule["cashbook_narrative"]) is not None
                ),
                None,
            )
            hits = sum(int(rule["hit_count"] or 0) for rule in category_rules)
            migrations.append((canonical, hits, narrative))

        for canonical, hits, narrative in migrations:
            connection.execute(
                text(
                    "UPDATE rules SET payee_pattern=:key, hit_count=:hits, "
                    "confidence=:confidence, cashbook_narrative=:narrative "
                    "WHERE id=:id"
                ),
                {
                    "id": canonical["id"],
                    "key": key,
                    "hits": hits,
                    "confidence": hits / total_hits if total_hits else 0,
                    "narrative": narrative,
                },
            )
        migrated_rules += len(migrations)

    if migrated_rules or skipped_conflicts:
        logging.getLogger("cashbook").info(
            "merchant_rule_migration_completed",
            extra={
                "merchant_rule_count": migrated_rules,
                "merchant_keys_skipped_for_narrative_conflict": skipped_conflicts,
            },
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
        _migrate_legacy_rules(connection, text)
