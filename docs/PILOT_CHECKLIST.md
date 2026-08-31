# Pilot Checklist

## Before handover

- [ ] Full automated test suite passes.
- [ ] `SETUP CASHBOOK.bat` works on the bursar's Windows machine.
- [ ] `START CASHBOOK.bat` opens the application.
- [ ] Setup screen shows all green checks.
- [ ] Correct blank WCED `.xls` template is configured.
- [ ] Manual backup can be created.
- [ ] Support bundle downloads successfully.

## First controlled statement

- [ ] Use one known Standard Bank statement.
- [ ] Confirm imported transaction count against the bank statement.
- [ ] Confirm money in total.
- [ ] Confirm money out total.
- [ ] Confirm running-balance reconciliation passes with R0.00 difference.
- [ ] Bursar reviews every transaction.
- [ ] Pending count reaches zero.
- [ ] Generate reviewed XLSX.
- [ ] Generate WCED XLS.
- [ ] Compare both outputs with the bursar's normal manual process.
- [ ] Record every mismatch or confusing interaction.

## During pilot

- [ ] Keep automatic backups enabled.
- [ ] Capture error IDs when failures occur.
- [ ] Ask for a support bundle when diagnosing errors.
- [ ] Do not manually edit the SQLite database unless recovering under developer supervision.
- [ ] Review logs and audit history after meaningful incidents.

## Exit criteria for the one-bursar prototype

- [ ] Several real accounting periods processed successfully.
- [ ] No unexplained reconciliation differences.
- [ ] No duplicate financial entries from overlapping statements.
- [ ] WCED output repeatedly matches the accepted manual workflow.
- [ ] Bursar can operate the application without developer assistance for normal usage.
