"""Read-only, fail-closed understanding of legacy WCED workbooks."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MONTHS = {"JAN": 1, "JANUARY": 1, "FEB": 2, "FEBRUARY": 2, "MAR": 3, "MARCH": 3,
          "APR": 4, "APRIL": 4, "MAY": 5, "JUN": 6, "JUNE": 6, "JUL": 7, "JULY": 7,
          "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9, "SEPTEMBER": 9, "OCT": 10,
          "OCTOBER": 10, "NOV": 11, "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12}
FIELD_SYNONYMS = {
    "date": {"DAY", "DATE", "TRANSACTION DATE"},
    "description": {"DETAILS", "DESCRIPTION", "PARTICULARS", "PAYEE", "FROM"},
    "reference": {"REFERENCE", "BANK REFERENCE", "DEPOSIT NUMBER", "RECEIPT NUMBER", "DOCUMENT NUMBER", "PAYMENT NUMBER", "CHEQUE NUMBER"},
    "total_amount": {"TOTAL AMOUNT", "TOTAL PAYMENT", "TOTAL PAYMENTS", "AMOUNT", "TOTAL RECEIPT", "TOTAL RECEIPTS"},
}


def column_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def _ledger_sheet(name: str) -> tuple[int, str] | None:
    bits = _text(name).split()
    if len(bits) != 2 or bits[0] not in MONTHS:
        return None
    if bits[1] in {"PC", "PAYMENT", "PAYMENTS"}: return MONTHS[bits[0]], "payment"
    if bits[1] in {"RC", "RECEIPT", "RECEIPTS"}: return MONTHS[bits[0]], "receipt"
    return None


def _merged_parent(sheet, row: int, col: int) -> str:
    for rlo, rhi, clo, chi in getattr(sheet, "merged_cells", []):
        if rlo <= row < rhi and clo <= col < chi:
            return _text(sheet.cell_value(rlo, clo))
    return ""


def _field_candidates(sheet, header_rows: range) -> dict[str, list[dict]]:
    result = {field: [] for field in FIELD_SYNONYMS}
    for row in header_rows:
        for col in range(sheet.ncols):
            label = _text(sheet.cell_value(row, col))
            if not label: continue
            for field, synonyms in FIELD_SYNONYMS.items():
                if label in synonyms:
                    result[field].append({"column": col, "row": row, "label": label,
                                          "confidence": "CONFIRMED", "evidence": [f"exact header {label}"]})
    return result


def _capture_range(sheet, header_row: int, date_column: int) -> tuple[int, int | None]:
    start = header_row + 1
    # A subheader row immediately beneath a parent header is not capture data.
    while start < sheet.nrows and not str(sheet.cell_value(start, date_column)).strip(): start += 1
    end = None
    for row in range(start, sheet.nrows):
        if _text(sheet.cell_value(row, date_column)).startswith("TOTAL"):
            end = row - 1; break
    return start, end


def _sheet_report(sheet, visibility: str) -> dict:
    ledger = _ledger_sheet(sheet.name)
    candidates = _field_candidates(sheet, range(min(12, sheet.nrows)))
    confirmed = {}
    ambiguities = []
    for field, choices in candidates.items():
        unique = {item["column"] for item in choices}
        if len(unique) == 1: confirmed[field] = choices[0]
        elif len(unique) > 1: ambiguities.append({"field": field, "status": "AMBIGUOUS", "candidates": sorted(unique)})
    # The date header anchors the ledger header band; receipt "From" labels
    # commonly live on a lower sub-header row and must not move the band.
    header_row = confirmed.get("date", {}).get("row")
    categories = []
    if ledger and header_row is not None:
        start_col = (confirmed.get("total_amount", {"column": sheet.ncols})["column"] + 1)
        parent = ""
        for col in range(start_col, sheet.ncols):
            top = _text(sheet.cell_value(header_row, col))
            child = _text(sheet.cell_value(header_row + 1, col)) if header_row + 1 < sheet.nrows else ""
            merged = _merged_parent(sheet, header_row, col)
            if top: parent = merged or top
            label = child or (top if top and not merged else "")
            if not label or label in FIELD_SYNONYMS["total_amount"]: continue
            path = [part for part in (parent, label) if part]
            if len(path) == 2 and path[0] == path[1]: path = [label]
            categories.append({"direction": "debit" if ledger[1] == "payment" else "credit", "column": col,
                               "column_letter": column_letter(col), "parent_path": path[:-1], "label": path[-1],
                               "display_name": " > ".join(path), "writable": True, "confidence": "CONFIRMED"})
    capture = None
    if header_row is not None and "date" in confirmed:
        capture = _capture_range(sheet, header_row, confirmed["date"]["column"])
    columns = []
    for col in range(sheet.ncols):
        semantic = next((field for field, item in confirmed.items() if item["column"] == col), None)
        category = next((item for item in categories if item["column"] == col), None)
        kind = "ALLOCATION" if category else "INPUT" if semantic else "UNKNOWN"
        columns.append({"index": col, "letter": column_letter(col), "semantic_field": semantic,
                        "classification": kind, "writable": kind in {"INPUT", "ALLOCATION"}})
    return {"sheet_name": sheet.name, "rows": sheet.nrows, "columns": sheet.ncols, "visibility": visibility,
            "ledger": {"month": ledger[0], "type": ledger[1]} if ledger else None,
            "merged_cells": [list(item) for item in getattr(sheet, "merged_cells", [])],
            "header_row": header_row, "fields": confirmed, "ambiguities": ambiguities,
            "capture_start_row": capture[0] if capture else None, "capture_end_row": capture[1] if capture else None,
            "categories": categories, "columns_detail": columns}


def inspect_workbook(path_or_content: str | Path | bytes, filename: str | None = None) -> dict:
    import xlrd
    if isinstance(path_or_content, bytes): workbook = xlrd.open_workbook(file_contents=path_or_content, formatting_info=True)
    else: workbook = xlrd.open_workbook(str(path_or_content), formatting_info=True)
    visibility = getattr(workbook, "_sheet_visibility", [0] * workbook.nsheets)
    sheets = [_sheet_report(workbook.sheet_by_index(i), "hidden" if visibility[i] else "visible") for i in range(workbook.nsheets)]
    structural = [{key: item[key] for key in ("sheet_name", "visibility", "ledger", "merged_cells", "header_row", "fields", "ambiguities", "capture_start_row", "capture_end_row", "categories")} for item in sheets]
    fingerprint = hashlib.sha256(json.dumps(structural, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"filename": filename or (Path(path_or_content).name if not isinstance(path_or_content, bytes) else "workbook.xls"),
            "sheet_count": workbook.nsheets, "visible_sheets": [x["sheet_name"] for x in sheets if x["visibility"] == "visible"],
            "hidden_sheets": [x["sheet_name"] for x in sheets if x["visibility"] == "hidden"],
            "schema_fingerprint": fingerprint, "sheets": sheets,
            "features": {"merged_cells": "SUPPORTED", "hidden_sheets": "SUPPORTED", "formulas": "PRESERVATION_UNVERIFIED", "vba": "PRESERVATION_UNVERIFIED"}}
