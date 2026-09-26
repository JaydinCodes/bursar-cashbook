# Accounting mapping and merchant learning

## Merchant identity

Every transaction keeps its exact `payee_raw` bank description for reconciliation,
fingerprints, audit, and diagnostics. `payee_display` is a conservative bursar-facing
representation. `merchant_key` is the stable learning key: recognised Standard Bank
card metadata (masked cards, dates/times, known processor prefixes, authorisation
text, and standalone country codes) is removed, but unknown merchant text is retained.

Rules are keyed by `merchant_key`. One or two consistent decisions produce a suggestion;
automatic approval remains limited to exact merchant matches with at least three hits
and 95% confidence. Fuzzy suggestions are never automatically approved. Existing rule
patterns are re-keyed and merged by category during SQLite startup migration.

## WCED adapter

The monthly adapter explicitly discovers each PC/RC sheet's day, payee, reference,
total, capture-start, and category columns. It refuses to write where a required header
is missing or ambiguous. Debit transactions use the matching month PC sheet; credits
use the matching month RC sheet. Category columns are discovered per target sheet.

The RC `Deposit Number` field receives a genuine bank reference only. It is left blank
when the bank supplied none; the historical `IMPORT/<id>` placeholder is not written.

Before replacement, the generated workbook is reopened and target values are checked;
the complete sheet-name sequence is also checked. Only supported monthly capture cells
are targeted.

## XLS preservation limitation

`xlrd` plus `xlutils.copy` is the current legacy-XLS mechanism. It preserves the tested
fixture's sheets, cells, formatting, and ordinary formulas, but cannot provide a
complete guarantee for every VBA object, embedded control, or unsupported BIFF feature.
For macro-heavy or unusually complex school workbooks, validate a copy in Excel before
pilot use. Excel COM would offer stronger native preservation on Windows, but should be
introduced only after compatibility testing and with explicit locked-workbook handling.
