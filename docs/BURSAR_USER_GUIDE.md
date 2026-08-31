# Bursar Cashbook — Pilot User Guide

## Start the application

1. Double-click `START CASHBOOK.bat`.
2. Keep the black Cashbook window open while using the application.
3. The Cashbook opens in the browser automatically.

If the application says setup is incomplete, run `SETUP CASHBOOK.bat` or contact support.

## First-time setup

Open **Setup** in the top-right corner.

All checks should show a green tick. If the WCED template is missing, upload the blank WCED `.xls` cashbook template supplied for the pilot.

Do not use a historical cashbook containing real captured entries as the blank export template.

## Import a Standard Bank statement

1. Download the statement from Standard Bank as CSV, XLS or XLSX.
2. Under **Import statement**, choose the file.
3. Click **Upload statement**.
4. Wait for **Reconciliation: PASSED**.

If reconciliation fails, stop and contact support. Do not manually force the statement through.

## Review transactions

Every imported transaction requires review.

- Money out uses an **expense** category.
- Money in uses an **income** category.
- Review the suggested category and change it where necessary.
- Click **Save** for every transaction.

## Generate a cashbook

1. Choose the financial year.
2. Select **Generate reviewed cashbook** or **Generate WCED cashbook**.
3. Check the confirmation summary.
4. Confirm only when pending transactions are `0` and reconciliation has passed.

During the pilot, compare the generated output with the normal manual cashbook process before treating it as final.

## Backups

The app automatically creates local backups before imports and restores. You can also click **Create backup now**.

Only restore an older backup when you intentionally need to return to an earlier state. A safety backup of the current database is automatically created first.

## If something goes wrong

If an error ID appears, for example:

`ERR-20260831-12AB34CD`

1. Copy or photograph the error ID.
2. Click **Download support bundle**.
3. Send the ZIP and error ID to the developer.

The default support bundle does not include full transaction descriptions or bank references.
