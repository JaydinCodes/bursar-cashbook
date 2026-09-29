# Public test fixtures

`synthetic_wced_2020.xls` is generated exclusively by
`build_synthetic_wced_cashbook.py`. It contains invented labels and blank
capture rows; it must not be replaced with a school cashbook, bank statement,
or other financial record.

Never commit production accounting data, names, contact details, account
numbers, or statements. Use generated/sanitised fixtures and add their source
generator alongside the binary fixture.
