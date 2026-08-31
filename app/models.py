from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, Date, DateTime, func
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # income | expense
    cashbook_column = Column(Integer)  # original xls column index, for re-export


class Rule(Base):
    __tablename__ = "rules"
    id = Column(Integer, primary_key=True)
    payee_pattern = Column(String, nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    confidence = Column(Numeric, default=1.0)
    hit_count = Column(Integer, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    category = relationship("Category")


class Statement(Base):
    __tablename__ = "statements"
    id = Column(Integer, primary_key=True)
    bank = Column(String)
    source_filename = Column(String, nullable=False)
    source_hash = Column(String(64), nullable=False, unique=True)
    uploaded_at = Column(DateTime, server_default=func.now())
    period_start = Column(Date)
    period_end = Column(Date)


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("statements.id"))
    txn_date = Column(Date)
    payee_raw = Column(String)
    payee_normalized = Column(String, index=True)
    amount = Column(Numeric(12, 2))
    direction = Column(String)  # debit | credit
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    confidence = Column(Numeric, nullable=True)
    status = Column(String, default="pending")  # pending | auto | vetted | manual

    statement = relationship("Statement")
    category = relationship("Category")
