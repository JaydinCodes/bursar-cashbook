# Ledgerly rollback

Stop syncing if the workbook, category mapping or accounting year is uncertain.

1. Use **Undo last sync** to restore the pre-sync backup and reverse only that batch.
2. Verify earlier syncs remain; do not repeatedly undo without checking each result.
3. If the backup is missing, do not edit Ledgerly's sync ledger. Restore the
   identified workbook backup manually and collect diagnostics for support.
4. Normal uninstall preserves `%LOCALAPPDATA%\\BursarCashbook`; a compatible
   previous installer can be installed over it.
5. **Clear workspace** is deliberately destructive, not a rollback mechanism.
   Use it only after a retained database backup and explicit bursar approval.
