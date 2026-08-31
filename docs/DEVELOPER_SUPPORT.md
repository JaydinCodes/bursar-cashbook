# Developer Support Guide

## Start locally

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Run tests

```powershell
python -m unittest discover -s tests -v
```

## Important locations

- Database: `cashbook.db`
- Logs: `logs/cashbook.log`
- Backups: `backups/`
- Local WCED template: `config/wced-template.xls`
- App version: `app/version.py` and `version.txt`

## Error IDs

Unexpected exceptions are assigned IDs such as `ERR-YYYYMMDD-XXXXXXXX`. Search `logs/cashbook.log` for the ID. The corresponding audit event is `application.error`.

## Support bundles

The UI downloads `/diagnostics/export`. It contains runtime metadata, a log tail, import summaries and audit events. It intentionally excludes full transaction descriptions and bank references.

## Backup restore

The UI calls `POST /backups/{filename}/restore` with `{ "confirm": true }`.

Before restoration, the application creates a `before-restore` backup. Restore is SQLite-only and validates the selected database with `PRAGMA integrity_check` plus required table checks.

## Updating the pilot installation

1. Ask the bursar to close the app.
2. Create a manual backup.
3. Apply the tested patch/update.
4. Run the full test suite.
5. Start the app and check `/setup/status`.
6. Confirm the displayed version changed.

Do not delete `cashbook.db` during Phase 3 updates unless intentionally rebuilding a test installation.

## Version bump

Update both:

- `app/version.py`
- `version.txt`

Keep support bundles and UI version labels aligned with the installed build.
