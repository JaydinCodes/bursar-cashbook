# Ledgerly bursar pilot checklist

Use a copy of the cashbook, never the production workbook on the first pilot.

- [ ] Confirm the accounting year from the workbook, not only its filename.
- [ ] Import a known statement and verify opening balance, debits, credits, closing balance and R0.00 difference.
- [ ] Review every transaction; debit previews to PC and credit previews to RC.
- [ ] Assign categories and clean narratives; preview before choosing **Sync now**.
- [ ] Check Excel day, narrative, reference, total and category-allocation cells after sync.
- [ ] Resolve a historical-match prompt by selecting the actual row and choosing **Already in cashbook**.
- [ ] Re-import once to check duplicates, then exercise sync #1 / sync #2 / undo #2.

XLS read/write validation and selected sync safeguards are automated. Installer,
single-instance, Excel locking and a live parallel period remain manual Windows checks.
