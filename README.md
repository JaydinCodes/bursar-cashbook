# Bursar Cashbook Automation

Local Standard Bank prototype that reads bank statements and updates the bursar's existing Excel cashbook directly.

## Phase 6 workflow

```text
Standard Bank statement
        ↓
Parse transactions
        ↓
Validate debit / credit structure
        ↓
Reconcile running balances
        ↓
Deduplicate
        ↓
Classify
        ↓
Trusted exact match ──→ auto-approved
Uncertain match       ──→ bursar review
        ↓
Live cashbook sync
        ↓
Existing registered .xls workbook is updated in place
```

There is no generated/downloaded cashbook in Phase 6.

## Financial safety

- Standard Bank statement validation and running-balance reconciliation.
- Transaction fingerprinting prevents overlapping imports from duplicating bank transactions.
- Trusted automatic classification requires an exact normalized match, at least 95% confidence and at least 3 historical hits.
- Human review handles uncertain classifications.
- `cashbook_syncs` records the exact workbook sheet, row and category column used for each synchronized transaction.
- Retrying synchronization does not append a transaction twice.
- Correcting a synchronized classification updates the existing row instead of adding another row.
- The current workbook is backed up before every live write.
- Modified workbook bytes are reopened and validated before replacing the live file.
- If Excel locks the workbook, the accounting decision stays saved and the write can be retried safely.

## Cashbook structure

The first live adapter is `monthly_pc_rc_v1`. It supports the WCED-style monthly PC/RC workbook family while discovering category names and columns from the bursar's actual workbook.

This is deliberately adapter-based: a genuinely different cashbook family should be added as another cashbook adapter rather than changing statement parsing or classification logic.

## Handover

First time:

```powershell
SETUP CASHBOOK.bat
```

Normal use:

```powershell
START CASHBOOK.bat
```

Then:

1. Register the bursar's existing `.xls` cashbook in the browser.
2. Import a Standard Bank statement.
3. Review only exceptions.
4. Use **Open cashbook in Excel** to inspect the live file.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The XLS integration tests use `data/2020_cashbook.xls` when the fixture and legacy XLS dependencies are available.

## Upgrade from Phase 5

Do **not** delete `cashbook.db` and do **not** reseed. Starting Phase 6 creates two new tables via SQLAlchemy `create_all()`:

- `cashbook_profiles`
- `cashbook_syncs`

Existing categories, rules, statements, transactions, audit history and backups remain intact.

After applying the patch, register the actual current cashbook. Existing approved/corrected transactions can then be synchronized into it once.
