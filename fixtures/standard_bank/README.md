# Synthetic Standard Bank fixtures

These files contain invented names, dates, account details, and amounts. They
are for parser development only and are not copies of a customer statement.

Variants represented:

- `standard_bank_business_csv_v1.csv`: Business Online-style transaction
  listing with a report title, debit/credit columns, and a closing balance row.
- `standard_bank_personal_csv_v1.csv`: signed amount transaction-history CSV.
- `standard_bank_business_xlsx_v1.xlsx`: Excel export with leading report rows
  and debit/credit columns.

The importer must identify the header row rather than depend on a fixed row or
column position. A real, redacted school export remains necessary before any
variant is labelled production-validated.
