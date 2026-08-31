# Bursar Cashbook Automation

Single-bursar Standard Bank prototype for importing, reconciling, reviewing and exporting WCED cashbook transactions.

## Prototype guarantees

- Standard Bank CSV/XLS/XLSX only.
- Debit/credit structure is validated before import.
- Running balances must reconcile before import succeeds.
- Existing transactions are fingerprinted and skipped on overlapping statement imports.
- Every new transaction requires human review before export.
- Exports are financial-year scoped and blocked while that year has pending transactions.
- Review retries are idempotent and category corrections move, rather than duplicate, classifier learning votes.

## Supportability

Phase 2 adds local support tooling without adding multi-user production infrastructure:

- JSON application logs in `logs/cashbook.log` with rotation.
- Append-only application audit events stored in SQLite.
- Import history and audit history visible in the review screen.
- Unexpected failures return support-friendly IDs such as `ERR-20260831-A1B2C3D4`.
- `Download diagnostic report` creates a privacy-reduced ZIP containing app/system metadata, recent import summaries, audit history and the application log tail.
- Automatic SQLite backups are created at startup (once per day) and immediately before statement imports. The newest 10 are retained by default.
- App version is shown in the UI and `/health` response.

Diagnostics intentionally exclude full transaction descriptions, bank references and complete statement data. Application logs should likewise log IDs/counts rather than transaction descriptions.

## Install

```powershell
pip install -r requirements.txt
python seed.py data/2020_cashbook.xls
python -m unittest discover -s tests -v
```

Set the blank WCED template:

```powershell
$env:WCED_TEMPLATE_PATH = "C:\path\to\blank-WCED-cashbook.xls"
```

Start the prototype:

```powershell
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`.

## Phase 2 upgrade note

If you already applied Phase 1, **do not delete or recreate `cashbook.db`**. Starting the app after this patch adds the new `audit_events` table with `create_all()` while preserving the existing categories, rules, statements and transactions.

## Local support configuration

Optional environment variables:

```powershell
$env:CASHBOOK_LOG_DIR = "logs"
$env:CASHBOOK_BACKUP_DIR = "backups"
$env:CASHBOOK_BACKUP_RETENTION = "10"
```

## Phase 3 pilot handover

This build includes a local setup wizard, Windows setup/start scripts, export preflight confirmation, backup/restore controls, a Help page, support documentation, and the pilot checklist.

For a Windows handover:

1. Run `SETUP CASHBOOK.bat` once if `.venv` has not been created.
2. Run `START CASHBOOK.bat` for normal use.
3. Open **Setup** and ensure all readiness checks are green.
4. Upload the blank WCED `.xls` template through Setup if it is not already configured.

The local template is stored at `config/wced-template.xls` and is ignored by Git.

Do not delete `cashbook.db` when applying Phase 3 over the tested Phase 2 prototype.
