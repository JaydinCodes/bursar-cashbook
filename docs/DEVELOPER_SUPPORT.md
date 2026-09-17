# Developer Support — Live Cashbook Model

## Phase 6 architecture

The registered workbook is the destination. Phase 6 no longer generates or downloads a new WCED cashbook.

Pipeline:

`Standard Bank statement -> parse -> reconcile -> classify -> approve/review -> live cashbook sync`

## Important files

- `app/bank_parser.py` — Standard Bank import adapter.
- `app/reconciliation.py` — running-balance validation.
- `app/automation.py` — trusted automatic classification policy.
- `app/cashbook_sync.py` — live workbook inspection, registration, idempotent synchronization and correction.
- `app/wced_export.py` — shared monthly PC/RC structure helpers and post-write validation. The download endpoints are retired.

## Cashbook profiles

`cashbook_profiles` stores the registered workbook metadata and detected adapter. The current adapter is `monthly_pc_rc_v1`.

Category names and their Excel columns are discovered from the actual workbook. The adapter currently expects the monthly PC/RC workbook family; future workbook families should be implemented as additional adapters rather than adding conditionals to the statement importer.

## Synchronization ledger

`cashbook_syncs` links a transaction to its exact workbook placement:

- cashbook profile
- sheet name
- row index
- category column
- category ID

This is what prevents repeated syncs from duplicating transactions. If a classification changes after sync, the recorded row is validated and the category allocation is moved in place.

## File safety

Before each workbook mutation, the live `.xls` file is copied to `cashbook_backups/`. The modified workbook is produced in memory, re-opened and validated, then atomically replaces the live file.

If Excel locks the workbook on Windows, synchronization stops with a friendly error and can be retried after Excel is closed.

## Version

Phase 6: `0.6.0`.
