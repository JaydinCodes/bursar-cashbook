# Bursar cashbook automation

Parses a bank statement and auto-fills a WCED-style cashbook, with the
bursar vetting anything the categorization engine isn't confident about.

## Status

Built so far (runs against the real 2020 cashbook already):

- `app/models.py` -- schema: categories, rules, statements, transactions
- `app/categorize.py` -- payee -> category matching (exact, then fuzzy, confidence-scored)
- `seed.py` -- derives categories AND initial rules directly from a historical
  cashbook xls, rather than a hardcoded category list. Re-run against a
  different school's workbook and it adapts to that school's columns.
- `app/main.py` -- FastAPI skeleton (health check only so far)
- `app/bank_parser.py` -- generic CSV / legacy XLS statement parser; uploads
  import transactions into a review queue

## Run it

```bash
pip install -r requirements.txt
python3 seed.py data/2020_cashbook.xls
```

That builds `cashbook.db` (SQLite for now, swap `DATABASE_URL` env var for
Postgres later -- models don't use anything sqlite-specific).

Quick sanity check:

```python
from app.db import SessionLocal
from app.categorize import categorize
db = SessionLocal()
categorize("NASHUA", db)          # -> exact match, high confidence
categorize("MR A VISAGIE", db)    # -> unknown, needs vetting
```

Start the API and upload a CSV statement (a bank's usual export format):

```bash
uvicorn app.main:app --reload
curl -F "bank=Example Bank" -F "file=@statement.csv" http://127.0.0.1:8000/statements/upload
```

The importer detects common Date, Description, Debit/Credit or signed Amount
columns. It includes format-tolerant profiles for FNB, ABSA, Standard Bank,
Nedbank and Capitec and supports CSV, `.xls`, and `.xlsx` exports. Open
`http://127.0.0.1:8000/` for the bursar's review screen. PDF imports remain
pending real bank-statement samples.

From the review screen, the bursar can add income or expense categories,
approve/correct transactions, and download `reviewed-cashbook.xlsx`. The
export includes only transactions explicitly reviewed by the bursar; automatic
suggestions and pending entries are deliberately excluded.

## WCED template export

To create a WCED-compatible `.xls` export, configure a **blank** workbook
template before starting the API:

```powershell
$env:WCED_TEMPLATE_PATH = "C:\path\to\blank-WCED-cashbook.xls"
uvicorn app.main:app --reload
```

The exporter writes approved debits into the relevant monthly `PC` sheet and
approved credits into the matching `RC` sheet. Category names must match the
template's column labels. Do not use a historical workbook with real entries as
the configured template.

## Not built yet

1. PDF parser (blocked on a sample bank statement, expected Monday).
   Interface it as an adapter so swapping banks doesn't touch anything else:
   `class BankParser(Protocol): def extract(pdf_path) -> list[RawTxn]`
2. Ingest endpoint: upload statement -> parse -> categorize() every txn -> insert
3. Vetting queue: API + frontend, list `status='pending'` transactions sorted
   by confidence, bursar approves/corrects, correction upserts a `Rule`
4. Income-side categorization -- different problem to expenditure. Income
   rows (school fees, WCED transfers) don't really have a "payee" the same
   way; likely amount/reference-pattern matching instead of payee lookup.
   Deferred until expenditure side is solid.
5. Cashbook view + term rollup (043 forms, income statement) once
   `transactions` has enough vetted data to roll up
