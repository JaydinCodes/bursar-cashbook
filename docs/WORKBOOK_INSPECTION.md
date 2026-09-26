# Workbook inspection and preservation

`python -m app.inspect_workbook path\to\cashbook.xls` emits a privacy-safe structural
report: sheet visibility, merged ranges, detected monthly ledgers, semantic fields,
capture ranges, category paths, and a schema fingerprint. It excludes transaction values.

Only confirmed mappings are eligible for the current WCED writer. Before sync the current
workbook schema fingerprint must equal the registered profile fingerprint; otherwise sync
stops. The profile is re-baselined only after an application-owned workbook replacement
has passed target-cell validation.

`xlrd` can inspect merged cells and sheet visibility. Formula/VBA/object preservation is
reported as preservation-unverified: `xlutils.copy` is retained for the validated legacy
fixture, but Excel COM should be evaluated on a copy of each real school workbook before
being adopted for macro-heavy workbooks.
