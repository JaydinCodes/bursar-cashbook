# Bursar Cashbook — Pilot User Guide

## What the application does

The application reads a Standard Bank statement, checks that the statement reconciles, classifies transactions, asks you only about uncertain classifications, and writes approved transactions directly into your registered cashbook workbook.

There is no separate generated cashbook. The registered workbook is the live accounting file.

## First-time setup

1. Run `SETUP CASHBOOK.bat` if this is a new installation.
2. Start the application with `START CASHBOOK.bat`.
3. Under **Live cashbook**, register the existing `.xls` cashbook that you normally capture transactions into.
4. The application checks the workbook structure and stores it as the active managed cashbook.

## Daily workflow

1. Make sure the live cashbook is closed in Excel.
2. Export the statement from Standard Bank.
3. Upload the statement under **Import Standard Bank statement**.
4. The application validates the transaction rows and running balances.
5. Trusted classifications are approved automatically and written into the live cashbook.
6. Review only the transactions shown under **Exceptions requiring review**.
7. Saving a review automatically writes that transaction into the live cashbook.
8. Use **Open cashbook in Excel** when you want to inspect the result.

## If the cashbook was open in Excel

Windows may prevent the application from updating a workbook while Excel has it open. Your classification is still saved. Close Excel and click **Sync cashbook now**.

## Safety

- The same bank transaction is not imported twice.
- The same approved transaction is not written into the cashbook twice.
- Corrections update the existing cashbook row instead of creating another row.
- A cashbook backup is created before every live workbook write.
- The application validates the cells it changed before replacing the live workbook.

## If something goes wrong

Use **Support bundle** and send the downloaded diagnostic ZIP to the developer. If an Error ID is shown, include that ID as well.
