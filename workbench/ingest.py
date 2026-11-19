from __future__ import annotations

import csv
import codecs
import itertools
import json
import random
import re
from collections import deque
from datetime import date, datetime
from pathlib import Path
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .common import MAX_COLUMNS, MAX_ROWS, WorkbenchError, digest, identifier

csv.field_size_limit(10 * 1024 * 1024)
META = ["__source_id", "__source_name", "__sheet", "__row", "__record_id", "__raw", "__formulas", "__ingest_issue"]
MALFORMED_ROW = 'Malformed row. '


def text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def is_utf8(path, chunk=1024 * 1024):
    """Check the whole file, so a non-UTF-8 byte deep inside is not missed."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        with open(path, "rb") as stream:
            while block := stream.read(chunk):
                decoder.decode(block, final=False)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def csv_settings(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(128 * 1024)
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = "utf-16"
        sample = codecs.getincrementaldecoder(encoding)().decode(raw, final=False)
    elif is_utf8(path):
        encoding = "utf-8-sig"
        sample = codecs.getincrementaldecoder(encoding)().decode(raw, final=False)
    else:
        sample, encoding = raw.decode("cp1252", errors="replace"), "cp1252"
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


def _sample_types(first_rows, remaining, header, width, end_row):
    """Include the start, a deterministic cross-file sample, and the last complete rows."""
    rng = random.Random(0)
    head, reservoir, tail = [], [], deque(maxlen=64)
    scanned = 0
    rows = itertools.islice(itertools.chain(first_rows, remaining), end_row or MAX_ROWS + header)
    try:
        for row in itertools.islice(rows, header, None):
            if len(row) < width or any(text(cell).strip() for cell in row[width:]):
                continue
            row = [text(cell) for cell in row[:width]]
            if not any(row):
                continue
            tail.append(row)
            if len(head) < 100:
                head.append(row)
            scanned += 1
            if len(reservoir) < 512:
                reservoir.append(row)
            elif (index := rng.randrange(scanned)) < len(reservoir):
                reservoir[index] = row
    except (csv.Error, UnicodeDecodeError):
        # Preserve the readable preview when a later record fails. Import reports the file error.
        pass
    return head + reservoir + list(tail)


def _inspect_rows(stream, settings):
    rows = [[text(c) for c in row] for row in itertools.islice(stream, 220)]
    if not rows:
        raise WorkbenchError('This file or sheet is empty.')
    header = settings.get('header_row', header_guess(rows))
    if not isinstance(header, int) or not 1 <= header <= min(len(rows), 200):
        raise WorkbenchError('Choose an existing header row between 1 and 200.')
    headings = headers_from(rows[header - 1])
    end = settings.get('end_row')
    if end is not None and (not isinstance(end, int) or end <= header):
        raise WorkbenchError('The last data row must be after the header, or left blank for the whole table.')
    sample = [r[:len(headings)] for r in rows[header: min(header + 100, end) if end else header + 100] if any(r)]
    type_rows = _sample_types(rows, stream, header, len(headings), end)
    return rows, header, headings, end, sample, type_rows


def _inspect(path, filename, source_id, settings=None):
    settings = settings or {}
    extension = Path(filename).suffix.lower()
    sheets = []
    row_errors = settings.get('row_errors', 'reject')
    if row_errors not in {'reject', 'review'}:
        raise WorkbenchError('Choose Reject file or Send malformed rows to review.')
    if extension == ".csv":
        encoding, delimiter = csv_settings(path)
        encoding = settings.get("encoding", encoding)
        delimiter = settings.get("delimiter", delimiter)
        if encoding not in ("utf-8-sig", "cp1252", "utf-16") or delimiter not in (",", ";", "\t", "|"):
            raise WorkbenchError("Choose a supported CSV encoding and delimiter.")
        with open(path, encoding=encoding, newline="") as stream:
            details = _inspect_rows(csv.reader(stream, delimiter=delimiter, strict=True), settings)
        selected = ""
    elif extension == ".xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            sheets = workbook.sheetnames
            selected = settings.get("sheet", sheets[0])
            if selected not in sheets:
                raise WorkbenchError("The selected worksheet no longer exists.")
            workbook[selected].reset_dimensions()
            details = _inspect_rows(workbook[selected].iter_rows(values_only=True), settings)
        finally:
            workbook.close()
        encoding, delimiter = "", ""
    else:
        raise WorkbenchError("Use a .csv or .xlsx file. Legacy .xls and macro workbooks are not supported.")
    rows, header_row, headers, end_row, sample, type_rows = details
    fields, notes = [], []
    for i, header in enumerate(headers):
        field, note = infer_field(header, [r[i] for r in type_rows])
        base = field["name"]
        while field["name"] in [f["name"] for f in fields]:
            field["name"] += "_2"
        fields.append(field)
        notes.append({"field": field["name"], "reason": note})
    return {"id": source_id, "filename": filename, "settings": {"sheet": selected, "header_row": header_row,
            "encoding": encoding, "delimiter": delimiter, "end_row": end_row,
            **({'row_errors': 'review'} if row_errors == 'review' else {})}, "sheets": sheets, "headers": headers,
            "sample": sample, "fields": fields, "notes": notes, "header_candidates": rows[:20]}


def inspect(path, filename, source_id, settings=None):
    try:
        return _inspect(path, filename, source_id, settings)
    except UnicodeDecodeError as exc:
        chosen = (settings or {}).get("encoding", "the detected encoding")
        other = "Windows-1252 (cp1252)" if chosen != "cp1252" else "UTF-8"
        raise WorkbenchError(f"{filename} is not valid {chosen} text. Choose {other} as the encoding in this source's settings.") from exc
    except (BadZipFile, InvalidFileException, ParseError, csv.Error, UnicodeError, OSError) as exc:
        raise WorkbenchError(f"Cannot inspect {filename}: {exc}. Check the file format, encoding, and workbook integrity.") from exc


def iter_records(path, source, expected_headers):
    """Yield physical Excel rows / logical CSV records, preserving original values."""
    settings = source["settings"]
    header_row = settings["header_row"]
    filename = source["filename"]
    if Path(filename).suffix.lower() == ".csv":
        try:
            yield from _csv_records(path, settings, header_row, expected_headers)
        except UnicodeDecodeError as exc:
            other = "Windows-1252 (cp1252)" if settings["encoding"] != "cp1252" else "UTF-8"
            raise WorkbenchError(
                f"{filename} is not valid {settings['encoding']} text (byte {exc.object[exc.start:exc.start + 1]!r}). "
                f"Choose {other} as the encoding in this source's settings. No rows from this file were accepted.") from exc
    else:
        yield from _excel_records(path, settings, header_row, expected_headers)


def _csv_records(path, settings, header_row, expected_headers):
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
                reason = f'Record {number} has {len(values)} fields, expected {len(expected_headers)}.'
                if settings.get('row_errors') != 'review':
                    raise WorkbenchError(reason + ' No rows from this file were accepted.')
                yield number, values, MALFORMED_ROW + reason + ' Replace the source with a corrected file or explicitly exclude this record.', {}
                continue
            yield number, values, "", {}


def _excel_records(path, settings, header_row, expected_headers):
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
            extra = values[len(expected_headers):]
            if any(extra) and settings.get('row_errors') != 'review':
                raise WorkbenchError(f"Worksheet row {number} contains values beyond the mapped headings.")
            values = (values + [""] * len(expected_headers))[:len(expected_headers)]
            issues = []
            if any(extra):
                issues.append(MALFORMED_ROW + 'Worksheet row contains values beyond the mapped headings. Replace the source or explicitly exclude this record.')
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
            yield number, values + (extra if any(extra) else []), "; ".join(issues), provenance
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
            if len(values) > len(source['headers']):
                key = '__unmapped_values'
                while key in raw:
                    key += '_'
                raw[key] = values[len(source['headers']):]
            record_id = digest([source["id"], source["sha256"], source["settings"], row_number])[:32]
            writer.writerow([source["id"], source["filename"], source["settings"]["sheet"], row_number,
                             record_id, json.dumps(raw, ensure_ascii=False), json.dumps(formulas, ensure_ascii=False), issue] +
                            [raw.get(mapping.get(f, ""), "") for f in field_names])
    return count
