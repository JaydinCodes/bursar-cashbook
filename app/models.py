from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("name", "type", name="uq_category_name_type"),
        CheckConstraint("type IN ('income', 'expense')", name="ck_category_type"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    cashbook_column = Column(Integer)


class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (
        UniqueConstraint("payee_pattern", "category_id", name="uq_rule_payee_category"),
        CheckConstraint("hit_count >= 0", name="ck_rule_hit_count"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_rule_confidence"),
    )

    id = Column(Integer, primary_key=True)
    payee_pattern = Column(String, nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    confidence = Column(Numeric(10, 8), nullable=False, default=1)
    hit_count = Column(Integer, nullable=False, default=0)
    cashbook_narrative = Column(String, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    category = relationship("Category")


class Statement(Base):
    __tablename__ = "statements"
    __table_args__ = (
        CheckConstraint("reconciliation_status = 'passed'", name="ck_statement_reconciliation_status"),
        CheckConstraint("source_transaction_count > 0", name="ck_statement_source_count"),
        CheckConstraint("imported_transaction_count > 0", name="ck_statement_imported_count"),
        CheckConstraint("duplicate_transaction_count >= 0", name="ck_statement_duplicate_count"),
    )

    id = Column(Integer, primary_key=True)
    bank = Column(String, nullable=False, default="Standard Bank")
    source_filename = Column(String, nullable=False)
    source_hash = Column(String(64), nullable=False, unique=True)
    uploaded_at = Column(DateTime, server_default=func.now(), nullable=False)

    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    financial_year = Column(Integer, nullable=True)

    opening_balance = Column(Numeric(16, 2), nullable=False)
    closing_balance = Column(Numeric(16, 2), nullable=False)
    total_debits = Column(Numeric(16, 2), nullable=False)
    total_credits = Column(Numeric(16, 2), nullable=False)
    reconciliation_difference = Column(Numeric(16, 2), nullable=False)
    reconciliation_status = Column(String, nullable=False)

    source_transaction_count = Column(Integer, nullable=False)
    imported_transaction_count = Column(Integer, nullable=False)
    duplicate_transaction_count = Column(Integer, nullable=False, default=0)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transaction_amount"),
        CheckConstraint("direction IN ('debit', 'credit')", name="ck_transaction_direction"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'corrected')",
            name="ck_transaction_status",
        ),
        CheckConstraint(
            "suggestion_confidence IS NULL OR "
            "(suggestion_confidence >= 0 AND suggestion_confidence <= 1)",
            name="ck_transaction_suggestion_confidence",
        ),
    )

    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("statements.id"), nullable=False, index=True)

    fingerprint = Column(String(64), nullable=False, unique=True, index=True)
    source_row = Column(Integer, nullable=False)

    txn_date = Column(Date, nullable=False, index=True)
    payee_raw = Column(String, nullable=False)
    payee_normalized = Column(String, nullable=False, index=True)
    merchant_key = Column(String, nullable=True, index=True)
    reference = Column(String, nullable=True)
    balance_after = Column(Numeric(16, 2), nullable=False)

    amount = Column(Numeric(16, 2), nullable=False)
    direction = Column(String, nullable=False)

    suggested_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    suggestion_confidence = Column(Numeric(10, 8), nullable=True)
    suggestion_method = Column(String, nullable=True)

    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    learned_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    cashbook_narrative = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")

    statement = relationship("Statement")
    suggested_category = relationship("Category", foreign_keys=[suggested_category_id])
    category = relationship("Category", foreign_keys=[category_id])
    learned_category = relationship("Category", foreign_keys=[learned_category_id])


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=True, index=True)
    entity_id = Column(Integer, nullable=True, index=True)
    details_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)


class CashbookProfile(Base):
    __tablename__ = "cashbook_profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, default="Active cashbook")
    adapter = Column(String, nullable=False)
    source_filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_hash = Column(String(64), nullable=False)
    # Explicitly confirmed accounting year.  Do not infer this at sync time
    # from a mutable filename.
    financial_year = Column(Integer, nullable=True, index=True)
    layout_json = Column(Text, nullable=False, default="{}")
    active = Column(Boolean, nullable=False, default=True, index=True)
    registered_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CashbookSync(Base):
    __tablename__ = "cashbook_syncs"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            "cashbook_profile_id",
            name="uq_cashbook_sync_transaction_profile",
        ),
    )

    id = Column(Integer, primary_key=True)
    transaction_id = Column(
        Integer,
        ForeignKey("transactions.id"),
        nullable=False,
        index=True,
    )
    cashbook_profile_id = Column(
        Integer,
        ForeignKey("cashbook_profiles.id"),
        nullable=False,
        index=True,
    )
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    sheet_name = Column(String, nullable=False)
    row_index = Column(Integer, nullable=False)
    category_column = Column(Integer, nullable=False)
    backup_filename = Column(String, nullable=True, index=True)
    synced_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    transaction = relationship("Transaction")
    cashbook_profile = relationship("CashbookProfile")
    category = relationship("Category")


class CashbookSyncBatch(Base):
    """An auditable, reversible unit of workbook synchronization."""

    __tablename__ = "cashbook_sync_batches"

    id = Column(Integer, primary_key=True)
    cashbook_profile_id = Column(Integer, ForeignKey("cashbook_profiles.id"), nullable=False, index=True)
    pre_sync_backup_filename = Column(String, nullable=False)
    pre_sync_file_hash = Column(String, nullable=False)
    post_sync_file_hash = Column(String, nullable=False)
    completed_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    undone_at = Column(DateTime, nullable=True, index=True)
    undo_safety_backup_filename = Column(String, nullable=True)

    cashbook_profile = relationship("CashbookProfile")
    entries = relationship("CashbookSyncBatchEntry", back_populates="batch", cascade="all, delete-orphan")


class CashbookSyncBatchEntry(Base):
    """The prior ledger state for one transaction touched by a sync batch."""

    __tablename__ = "cashbook_sync_batch_entries"
    __table_args__ = (UniqueConstraint("sync_batch_id", "transaction_id", name="uq_cashbook_sync_batch_entry"),)

    id = Column(Integer, primary_key=True)
    sync_batch_id = Column(Integer, ForeignKey("cashbook_sync_batches.id"), nullable=False, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False, index=True)
    operation = Column(String, nullable=False)  # insert or update
    # Deliberately not a foreign key: an undo deletes a newly-created ledger
    # row while retaining this audit snapshot for the batch history.
    resulting_sync_id = Column(Integer, nullable=True)
    previous_sync_id = Column(Integer, nullable=True)
    previous_category_id = Column(Integer, nullable=True)
    previous_sheet_name = Column(String, nullable=True)
    previous_row_index = Column(Integer, nullable=True)
    previous_category_column = Column(Integer, nullable=True)
    previous_backup_filename = Column(String, nullable=True)
    previous_synced_at = Column(DateTime, nullable=True)

    batch = relationship("CashbookSyncBatch", back_populates="entries")
    transaction = relationship("Transaction")
