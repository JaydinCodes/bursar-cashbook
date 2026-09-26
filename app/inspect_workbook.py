"""Developer CLI: python -m app.inspect_workbook path\\to\\cashbook.xls"""
import json, sys
from .workbook_inspector import inspect_workbook

if __name__ == "__main__":
    if len(sys.argv) != 2: raise SystemExit("Usage: python -m app.inspect_workbook <cashbook.xls>")
    print(json.dumps(inspect_workbook(sys.argv[1]), indent=2))
