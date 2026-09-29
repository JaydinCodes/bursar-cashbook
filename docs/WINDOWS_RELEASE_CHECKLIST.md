# Ledgerly Windows release checklist

Run this on a clean Windows machine with no development tools and only copies
of cashbooks/statements.

## Clean install and first use

- [ ] Install Ledgerly, launch both shortcuts, and verify single-instance behaviour.
- [ ] Confirm `%LOCALAPPDATA%\\BursarCashbook` exists. This is a retained compatibility/data path.
- [ ] Connect a **copy** of the WCED `.xls` cashbook and explicitly confirm its accounting year.
- [ ] Import a Standard Bank statement, verify reconciliation, review entries, preview, then explicitly sync.

## Excel safety and persistence

- [ ] Open the workbook in Excel and attempt sync; Ledgerly must fail safely without changing it.
- [ ] Close Excel, retry sync, and verify the backup and resulting cells.
- [ ] Restart: database, learned rules and sync ledger persist.
- [ ] Re-import the same statement: no duplicate statement transactions appear.
- [ ] Verify a possible historical workbook match is offered for adoption, never silently appended.
- [ ] Sync #1, sync #2, undo #2, and verify #1 survives.

## Upgrade and removal

- [ ] Install a newer build over the existing installation and verify records remain.
- [ ] Normal uninstall removes the application but preserves `%LOCALAPPDATA%\\BursarCashbook`.
- [ ] Reinstall and verify records return.
- [ ] Test **Clear workspace** separately: it is deliberately destructive and requires explicit confirmation.
