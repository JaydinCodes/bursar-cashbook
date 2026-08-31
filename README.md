# Bursar Cashbook Automation — Phase 1

Single-bursar, Standard Bank prototype focused on financial correctness.

## Phase 1 guarantees

- Standard Bank only.
- Strict debit/credit validation.
- Running-balance reconciliation before import.
- Exact file duplicate protection plus transaction fingerprints for overlapping exports.
- Every imported transaction remains pending until a bursar reviews it.
- Review requests are idempotent and category corrections move the learning vote instead of double-counting it.
- Exports require a financial year and are blocked while that year has pending transactions.
- WCED HTTP export naming collision fixed.

## Setup

Python 3.11+ recommended.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

For the first Phase 1 run, recreate the prototype database because `create_all()` does not migrate the old schema:

```powershell
Remove-Item cashbook.db -ErrorAction SilentlyContinue
python seed.py data/2020_cashbook.xls
```

Configure the blank WCED template:

```powershell
$env:WCED_TEMPLATE_PATH = "C:\path\to\blank-WCED-cashbook.xls"
```

Run:

```powershell
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/>.

## Tests

```bash
python -m unittest discover -s tests -v
```

The legacy WCED workbook test is skipped automatically when its historical fixture or the `xlrd`/`xlutils` packages are not available.
