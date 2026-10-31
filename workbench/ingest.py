from __future__ import annotations

import csv
import codecs
import itertools
import json
import re
from datetime import date, datetime
from pathlib import Path
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .common import MAX_COLUMNS, WorkbenchError, digest, identifier

csv.field_size_limit(10 * 1024 * 1024)
META = ["__source_id", "__source_name", "__sheet", "__row", "__record_id", "__raw", "__formulas", "__ingest_issue"]


def text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def csv_settings(path):
    raw = Path(path).read_bytes()[:128 * 1024]
    try:
        encoding = "utf-16" if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else "utf-8-sig"
        sample = codecs.getincrementaldecoder(encoding)().decode(raw, final=False)
    except UnicodeDecodeError:
        sample, encoding = raw.decode("cp1252"), "cp1252"
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    return encoding, delimiter


def header_guess(rows):
    candidates = []
    for i, row in enumerate(rows[:20]):
        values = [str(v).strip() for v in row if v is not None and str(v).strip()]
        if len(values) < 2:
            continue
        # A header normally spans the table, is unique, and contains text labels.
        score = len(values) + sum(bool(re.search(r"[A-Za-z]", v)) for v in values) / len(values)
        if len(set(v.lower() for v in values)) != len(values):
            score -= 4
        candidates.append((score, -i, i + 1))
    return max(candidates)[2] if candidates else 1


def headers_from(row):
    headers = [text(v).strip() for v in row]
    while headers and not headers[-1]:
        headers.pop()
    if not headers or len(headers) > MAX_COLUMNS:
        raise WorkbenchError(f"Choose a header containing 1–{MAX_COLUMNS} columns.")
    if any(not h for h in headers) or len(set(headers)) != len(headers):
        raise WorkbenchError("Blank or repeated source headings need a different header row or a corrected source file.")
    return headers


def infer_field(header, values):
    name = identifier(header)
    values = [str(v).strip() for v in values if v not in (None, "")]
    field = {"name": name, "type": "text", "required": False, "trim": True,
             "missing": [], "mapping": {}, "allowed": [], "date_format": "%Y-%m-%d",
             "decimal_separator": ".", "thousands_separator": "", "strip_symbols": "", "inferred": True}
    note = "Text preserves identifiers and unrecognized values."
    if not values:
        return field, "No populated sample values; retained as text."
    heading = identifier(re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', header))
    if re.search(r"(^|_)(id|code|zip|postal|phone|account|sku)(_|$)", heading):
        return field, "Identifier-like heading; kept as text to preserve leading zeros."
    if any(re.fullmatch(r'[+-]?0[0-9]+', v) for v in values):
        return field, "Leading-zero values in the sample; retained as text."
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in values):
        field["type"], note = "date", "Sample uses ISO dates; full-file validation follows."
    elif all(re.fullmatch(r"[+-]?\d+", v) for v in values) and not any(re.match(r"^[+-]?0\d", v) for v in values):
        field["type"], note = "integer", "Sample contains whole numbers; full-file validation follows."
    elif all(re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)", v) for v in values):
        field["type"], note = "decimal", "Sample contains dot-decimal numbers; no currency or units inferred."
    elif any(re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", v) for v in values):
        note = "Date order needs review; choose Date and an explicit format to convert."
    return field, note


def _inspect(path, filename, source_id, settings=None):
    settings = settings or {}
    extension = Path(filename).suffix.lower()
    sheets = []
    if extension == ".csv":
        encoding, delimiter = csv_settings(path)
        encoding = settings.get("encoding", encoding)
        delimiter = settings.get("delimiter", delimiter)
        if encoding not in ("utf-8-sig", "cp1252", "utf-16") or delimiter not in (",", ";", "\t", "|"):
            raise WorkbenchError("Choose a supported CSV encoding and delimiter.")
        with open(path, encoding=encoding, newline="") as stream:
            rows = list(itertools.islice(csv.reader(stream, delimiter=delimiter, strict=True), 220))
        selected = ""
    elif extension == ".xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            sheets = workbook.sheetnames
            selected = settings.get("sheet", sheets[0])
            if selected not in sheets:
                raise WorkbenchError("The selected worksheet no longer exists.")
            workbook[selected].reset_dimensions()
            rows = [[text(c) for c in r] for r in itertools.islice(workbook[selected].iter_rows(values_only=True), 220)]
        finally:
            workbook.close()
        encoding, delimiter = "", ""
    else:
        raise WorkbenchError("Use a .csv or .xlsx file. Legacy .xls and macro workbooks are not supported.")
    if not rows:
        raise WorkbenchError("This file or sheet is empty.")
    header_row = settings.get("header_row", header_guess(rows))
    if not isinstance(header_row, int) or not 1 <= header_row <= min(len(rows), 200):
        raise WorkbenchError("Choose an existing header row between 1 and 200.")
    headers = headers_from(rows[header_row - 1])
    end_row = settings.get("end_row")
    if end_row is not None and (not isinstance(end_row, int) or end_row <= header_row):
        raise WorkbenchError("The last data row must be after the header, or left blank for the whole table.")
    sample = [r[:len(headers)] for r in rows[header_row:header_row + 100] if any(v not in (None, "") for v in r)]
    fields, notes = [], []
    for i, header in enumerate(headers):
        field, note = infer_field(header, [r[i] if i < len(r) else "" for r in sample])
        base = field["name"]
        while field["name"] in [f["name"] for f in fields]:
            field["name"] += "_2"
        fields.append(field)
        notes.append({"field": field["name"], "reason": note})
    return {"id": source_id, "filename": filename, "settings": {"sheet": selected, "header_row": header_row,
            "encoding": encoding, "delimiter": delimiter, "end_row": end_row}, "sheets": sheets, "headers": headers,
            "sample": sample, "fields": fields, "notes": notes, "header_candidates": rows[:20]}


def inspect(path, filename, source_id, settings=None):
    try:
        return _inspect(path, filename, source_id, settings)
    except (BadZipFile, InvalidFileException, ParseError, csv.Error, UnicodeError, OSError) as exc:
        raise WorkbenchError(f"Cannot inspect {filename}: {exc}. Check the file format, encoding, and workbook integrity.") from exc


def iter_records(path, source, expected_headers):
    """Yield physical Excel rows / logical CSV records, preserving original values."""
    settings = source["settings"]
    header_row = settings["header_row"]
    filename = source["filename"]
    if Path(filename).suffix.lower() == ".csv":
        with open(path, encoding=settings["encoding"], newline="") as stream:
            rows = csv.reader(stream, delimiter=settings["delimiter"], strict=True)
            for number, values in enumerate(rows, 1):
                if settings.get('end_row') is not None and number > settings['end_row']:
                    break
                if number < header_row:
                    continue
                if number == header_row:
                    if headers_from(values) != expected_headers:
                        raise WorkbenchError("Source headings changed; inspect and map this file again.")
                    continue
                if not values:
                    continue
                if len(values) != len(expected_headers):
                    raise WorkbenchError(f"Record {number} has {len(values)} fields; expected {len(expected_headers)}. No rows from this file were accepted.")
                yield number, values, "", {}
    else:
        values_book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        formula_book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            sheet = settings["sheet"]
            if sheet not in values_book.sheetnames:
                raise WorkbenchError("Selected worksheet is missing.")
            values_book[sheet].reset_dimensions()
            formula_book[sheet].reset_dimensions()
            pairs = zip(values_book[sheet].iter_rows(), formula_book[sheet].iter_rows())
            for number, (cells, formula_cells) in enumerate(pairs, 1):
                if settings.get('end_row') is not None and number > settings['end_row']:
                    break
                values = [text(c.value) for c in cells]
                if number < header_row:
                    continue
                if number == header_row:
                    if headers_from(values) != expected_headers:
                        raise WorkbenchError("Source headings changed; inspect and map this file again.")
                    continue
                formulas = [c for c in formula_cells if c.data_type == "f"]
                if not any(values) and not formulas:
                    continue
                if any(values[len(expected_headers):]):
                    raise WorkbenchError(f"Worksheet row {number} contains values beyond the mapped headings.")
                values = (values + [""] * len(expected_headers))[:len(expected_headers)]
                issues = []
                provenance = {}
                for i, cell in enumerate(formula_cells[:len(expected_headers)]):
                    if cell.data_type == "f":
                        provenance[expected_headers[i]] = {"formula": cell.value, "cached_value": values[i], "freshness_verified": False}
                    if cell.data_type == "f" and not values[i]:
                        issues.append(f"Missing cached formula result: {expected_headers[i]}")
                    elif cell.data_type == "e":
                        issues.append(f"Excel cell error: {expected_headers[i]}")
                    elif isinstance(cell.value, (int, float)) and re.fullmatch(r"0{2,}", cell.number_format or ""):
                        # Excel stores this as a number but displays a padded identifier.
                        provenance[expected_headers[i]] = {"stored_value": text(cell.value), "number_format": cell.number_format}
                        values[i] = str(int(cell.value)).zfill(len(cell.number_format)) if float(cell.value).is_integer() else values[i]
                yield number, values, "; ".join(issues), provenance
        finally:
            values_book.close()
            formula_book.close()


def stage_source(path, source, mapping, output, field_names, row_limit):
    count = 0
    with open(output, "w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(META + field_names)
        for row_number, values, issue, formulas in iter_records(path, source, source["headers"]):
            count += 1
            if count > row_limit:
                raise WorkbenchError("Project exceeds the one-million-row limit; split it into smaller projects.")
            raw = dict(zip(source["headers"], values))
            record_id = digest([source["id"], source["sha256"], source["settings"], row_number])[:32]
            writer.writerow([source["id"], source["filename"], source["settings"]["sheet"], row_number,
                             record_id, json.dumps(raw, ensure_ascii=False), json.dumps(formulas, ensure_ascii=False), issue] +
                            [raw.get(mapping.get(f, ""), "") for f in field_names])
    return count
