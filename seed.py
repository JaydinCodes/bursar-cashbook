"""Seed expense categories and payee rules from a historical WCED cashbook."""

from collections import Counter, defaultdict
from decimal import Decimal
import sys

from app.categorize import normalize_payee
from app.db import SessionLocal, init_db
from app.models import Category, Rule

HISTORICAL_XLS = sys.argv[1] if len(sys.argv) > 1 else "data/2020_cashbook.xls"


def _is_real_label(value: str) -> bool:
    if not value:
        return False
    try:
        float(value)
        return False
    except ValueError:
        return True


def pc_column_categories(sheet) -> dict[int, str]:
    categories: dict[int, str] = {}
    last_top: str | None = None

    for column in range(4, sheet.ncols):
        top = str(sheet.cell_value(4, column)).strip() if sheet.nrows > 4 else ""
        sub = str(sheet.cell_value(5, column)).strip() if sheet.nrows > 5 else ""

        if _is_real_label(top):
            last_top = top

        if not last_top:
            continue

        categories[column] = f"{last_top}: {sub}" if _is_real_label(sub) else last_top

    return categories


def extract_payee_category_counts(workbook) -> dict[str, Counter]:
    counts: dict[str, Counter] = defaultdict(Counter)
    payment_sheets = [
        name
        for name in workbook.sheet_names()
        if name.endswith("PC") and "Recon" not in name
    ]

    for name in payment_sheets:
        sheet = workbook.sheet_by_name(name)
        column_categories = pc_column_categories(sheet)

        for row in range(6, sheet.nrows):
            payee_raw = str(sheet.cell_value(row, 2)).strip()
            if not payee_raw:
                continue

            payee = normalize_payee(payee_raw)
            for column, category in column_categories.items():
                value = sheet.cell_value(row, column)
                if isinstance(value, (int, float)) and value > 0:
                    counts[payee][category] += 1

    return counts


def run_seed(path: str) -> None:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError("Seeding from legacy XLS requires xlrd. Install requirements.txt first.") from exc

    init_db()
    workbook = xlrd.open_workbook(path)
    db = SessionLocal()

    try:
        counts = extract_payee_category_counts(workbook)
        category_names = {category for counter in counts.values() for category in counter}
        category_objects: dict[str, Category] = {}

        for name in sorted(category_names):
            existing = db.query(Category).filter_by(name=name, type="expense").first()
            category = existing or Category(name=name, type="expense")
            if existing is None:
                db.add(category)
            category_objects[name] = category

        db.flush()

        created = 0
        for payee, category_counter in counts.items():
            total = sum(category_counter.values())

            for category_name, hits in category_counter.items():
                confidence = Decimal(hits) / Decimal(total)
                category = category_objects[category_name]
                existing_rule = (
                    db.query(Rule)
                    .filter_by(payee_pattern=payee, category_id=category.id)
                    .first()
                )

                if existing_rule:
                    existing_rule.hit_count = hits
                    existing_rule.confidence = confidence
                else:
                    db.add(
                        Rule(
                            payee_pattern=payee,
                            category_id=category.id,
                            confidence=confidence,
                            hit_count=hits,
                        )
                    )
                    created += 1

        db.commit()
        print(f"categories: {len(category_objects)}")
        print(f"payees seen: {len(counts)}")
        print(f"rules created: {created}")
    finally:
        db.close()


if __name__ == "__main__":
    run_seed(HISTORICAL_XLS)
