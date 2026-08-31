"""
Bootstraps categories + rules from a historical WCED cashbook workbook,
instead of hardcoding one school's column list. Point HISTORICAL_XLS at
whatever cashbook the bursar hands you (2020, 2021, ...) and re-run --
categories and rules merge/update rather than duplicate.
"""
import sys
import xlrd
from collections import defaultdict, Counter

from app.db import SessionLocal, init_db
from app.models import Category, Rule
from app.categorize import normalize_payee

HISTORICAL_XLS = sys.argv[1] if len(sys.argv) > 1 else "data/2020_cashbook.xls"


def _is_real_label(v: str) -> bool:
    if not v:
        return False
    try:
        float(v)
        return False  # stray numeric artifact, not a label
    except ValueError:
        return True


def pc_column_categories(sheet) -> dict[int, str]:
    """Row 4 holds top-level column labels (forward-filled across a group,
    e.g. 'Other WCED Transfer Payments' spans several columns). Row 5 holds
    a sub-label for some of those columns (e.g. LTSM: Stationery). Combine
    both -- some columns (LTSM's breakdown) only carry a row 5 label.
    Columns 0-3 are Day/Cheque/Details/Total, not categories."""
    cats = {}
    last_top = None
    for c in range(4, sheet.ncols):
        top = str(sheet.cell_value(4, c)).strip() if sheet.nrows > 4 else ""
        sub = str(sheet.cell_value(5, c)).strip() if sheet.nrows > 5 else ""
        if _is_real_label(top):
            last_top = top
        if not last_top:
            continue
        if _is_real_label(sub):
            cats[c] = f"{last_top}: {sub}"
        else:
            cats[c] = last_top
    return cats


def extract_payee_category_counts(wb) -> dict[str, Counter]:
    counts: dict[str, Counter] = defaultdict(Counter)
    pc_sheets = [n for n in wb.sheet_names() if n.endswith("PC") and "Recon" not in n]

    for name in pc_sheets:
        sh = wb.sheet_by_name(name)
        col_cat = pc_column_categories(sh)
        for r in range(6, sh.nrows):
            payee_raw = str(sh.cell_value(r, 2)).strip()
            if not payee_raw:
                continue
            payee = normalize_payee(payee_raw)
            for c, cat in col_cat.items():
                v = sh.cell_value(r, c)
                if isinstance(v, float) and v > 0:
                    counts[payee][cat] += 1
    return counts


def run_seed(path: str):
    init_db()
    wb = xlrd.open_workbook(path)
    db = SessionLocal()

    counts = extract_payee_category_counts(wb)

    cat_names = {cat for c in counts.values() for cat in c}
    cat_objs = {}
    for cname in sorted(cat_names):
        existing = db.query(Category).filter_by(name=cname, type="expense").first()
        cat_objs[cname] = existing or Category(name=cname, type="expense")
        if not existing:
            db.add(cat_objs[cname])
    db.flush()

    rule_count = 0
    for payee, cat_counter in counts.items():
        total = sum(cat_counter.values())
        for cat_name, hits in cat_counter.items():
            confidence = hits / total
            existing_rule = (
                db.query(Rule)
                .filter_by(payee_pattern=payee, category_id=cat_objs[cat_name].id)
                .first()
            )
            if existing_rule:
                # This script derives totals from the complete workbook, so
                # re-running it must replace those totals rather than add a
                # second copy of the same historical data.
                existing_rule.hit_count = hits
                existing_rule.confidence = confidence
            else:
                db.add(Rule(
                    payee_pattern=payee,
                    category_id=cat_objs[cat_name].id,
                    confidence=confidence,
                    hit_count=hits,
                ))
                rule_count += 1

    db.commit()
    print(f"categories: {len(cat_objs)}")
    print(f"payees seen: {len(counts)}")
    print(f"rules created: {rule_count}")


if __name__ == "__main__":
    run_seed(HISTORICAL_XLS)
