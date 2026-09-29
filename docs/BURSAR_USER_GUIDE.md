# Ledgerly User Guide

## First time

1. Open **Ledgerly** from the Desktop or Start Menu.
2. Follow the on-screen setup guide: connect the current WCED `.xls` cashbook, then import a Standard Bank statement when one is available.
3. The application confirms the connected file and its cashbook layout before it can be synchronized.
4. Keep a normal school backup of the original cashbook as well.

## Daily workflow

1. Open **Ledgerly** from the desktop.
2. Close the cashbook in Excel before synchronizing it.
3. Select **Import statement** from Overview or Transactions and choose your Standard Bank statement.
4. Open **Transactions** and review any items that need a category.
5. Open **Cashbook** and select **Sync now**. Check the preview, then explicitly confirm the sync.
6. Select **Open in Excel** to inspect the updated live cashbook.

The application checks that the statement reconciles before importing it. Importing and reviewing only save information in Ledgerly; they never change Excel. The cashbook changes only after you preview and explicitly confirm **Sync now**. It does not import a transaction twice, and it does not write an approved transaction twice. Use the bank's downloaded CSV, XLS, XLSX, or text-searchable PDF statement. A scanned/image-only PDF cannot be imported because its transaction details cannot be safely verified.

## If Excel says the cashbook is open

Your classification is still saved. Close the workbook in Excel and select **Sync cashbook now**. The application will retry safely.

## Safety and support

A cashbook backup is made before every workbook update. If something goes wrong, use **Support bundle** and send the downloaded file to support. Include any Error ID shown on screen.

Your Ledgerly database (including transactions, learned rules, and sync history), managed cashbook, backups, logs, and settings stay on this computer when the application is upgraded or normally uninstalled. **Clear workspace** is separate: it deliberately removes Ledgerly database records and the cashbook connection, but does not alter the Excel cashbook file or remove backups, logs, or settings. Ask support before manually deleting any application data.
