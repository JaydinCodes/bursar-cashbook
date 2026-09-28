# Windows Clean-Machine Release Checklist

Use a clean Windows VM or machine with **no Python, pip, Git, virtual environment, or development tools**. Keep test data non-sensitive.

## Installation and basic launch

- [ ] Run `BursarCashbook-Setup.exe` successfully.
- [ ] Confirm Start Menu and Desktop shortcuts named **Bursar Cashbook**.
- [ ] Launch from each shortcut; no Command Prompt or PowerShell window appears.
- [ ] Confirm the browser opens automatically.
- [ ] Confirm `http://127.0.0.1:<chosen-port>/health` returns `status: ok` while running.
- [ ] Confirm `%LOCALAPPDATA%\\BursarCashbook\\data\\cashbook.db` is created or loaded and categories are available.

## Accounting workflow

- [ ] Register a supported live `.xls` cashbook.
- [ ] Import a known Standard Bank statement and confirm reconciliation passes.
- [ ] Review exceptions and confirm approved transactions are visible in the review flow.
- [ ] Synchronize, open the cashbook in Excel, and verify expected rows/categories.
- [ ] Synchronize again and confirm no duplicates are written.
- [ ] Confirm cashbook and database backups are created before their respective writes.
- [ ] Restart and confirm the same database and live cashbook remain available.

## Upgrade preservation

1. Install 0.8.0-rc1 and create recognisable test content and a registered cashbook.
2. Install the next release (currently 0.8.0-rc2) over it; the installer AppId must remain unchanged.
3. Launch the application.

- [ ] Confirm categories, statements, learned rules, sync history, `cashbooks\\active-cashbook.xls`, backups, logs, and configuration still exist.
- [ ] Confirm no seed/import step ran automatically.

## Uninstall preservation

- [ ] Uninstall and confirm binaries and shortcuts are removed.
- [ ] Confirm `%LOCALAPPDATA%\\BursarCashbook` still exists with the database, transaction history, learned rules, sync history, cashbooks, backups, logs, and configuration intact.
- [ ] Record that data must be manually removed only with bursar approval.
