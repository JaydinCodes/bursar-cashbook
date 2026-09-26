# Bursar Cashbook

Bursar Cashbook imports Standard Bank statements, reconciles them, helps the bursar classify exceptions, and safely synchronizes approved transactions to the existing WCED-style `.xls` cashbook.

## Installed bursar application

The bursar does not need Python, a terminal, or a batch file.

1. Run `BursarCashbook-Setup.exe`.
2. Open **Bursar Cashbook** from the Desktop or Start Menu.
3. Register the current cashbook, import a Standard Bank statement, review exceptions, and open the cashbook in Excel.

Application records are kept in `%LOCALAPPDATA%\\BursarCashbook`, not in Program Files. This includes the database, live cashbook, backups, logs, and configuration. Upgrades and normal uninstalls preserve these records.

See [the bursar guide](docs/BURSAR_USER_GUIDE.md) and [Windows release checklist](docs/WINDOWS_RELEASE_CHECKLIST.md).

## Financial safety

- Statement validation and running-balance reconciliation happen before import.
- Transaction fingerprints prevent duplicate imports.
- Only trusted exact matches are automatically approved; uncertain items require review.
- A synchronization ledger prevents duplicate workbook rows and makes corrections update the original row.
- The live workbook is backed up, validated, and safely replaced only after a successful write.
- A locked Excel workbook fails safely and can be retried without losing the accounting decision.

## Developer setup

For development only, install Python dependencies and run the service as appropriate for your environment. The legacy `START CASHBOOK.bat` and `SETUP CASHBOOK.bat` remain developer/legacy workflows; they are not part of the installed application.

Run tests:

```powershell
python -m unittest discover -s tests -v
```

Build a Windows release:

```powershell
./scripts/build_windows.ps1
```

See [Windows build instructions](docs/WINDOWS_BUILD.md) and [developer support notes](docs/DEVELOPER_SUPPORT.md).
