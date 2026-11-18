"""Read an export with a source profile into canonical records.

A profile (profiles/*.json) names, for each canonical field, the column names a
source may use, its type and date formats, and which fields are required. The
header row is the first row that contains every required column, so report
exports with title rows, group rows and total rows load without manual setup.
Rows without a value in every required field (titles, blanks, "Total for ..."
rows) are skipped and counted, never silently merged.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..common import WorkbenchError
from ..ingest import csv_settings, text

PROFILES = Path(__file__).resolve().parent / "profiles"
MAX_HEADER_SEARCH = 50


def profile(name):
    path = PROFILES / f"{name}.json"
    if not path.exists():
        raise WorkbenchError(f"Unknown source profile {name!r}. Available: {', '.join(sorted(p.stem for p in PROFILES.glob('*.json')))}")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Loaded:
    profile: str
    filename: str
    records: list
    skipped: int = 0
    excluded: int = 0
    header_row: int = 0
    columns: dict = field(default_factory=dict)


def parse_money(value):
    """'1,234.56', '$1,234.56', '(12.00)' and '-12.00' as exact decimals."""
    raw = value.strip().replace("$", "").replace(",", "").replace(" ", "")
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", raw):
        raise ValueError(f"not an amount: {value!r}")
    amount = Decimal(raw)
    return -amount if negative else amount


def parse_date(value, formats):
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date in {formats}: {value!r}")


def _rows(path):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        encoding, delimiter = csv_settings(path)
        with open(path, encoding=encoding, newline="") as stream:
            yield from csv.reader(stream, delimiter=delimiter)
    elif path.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            for row in book.worksheets[0].iter_rows(values_only=True):
                yield [text(v) for v in row]
        finally:
            book.close()
    else:
        raise WorkbenchError(f"{path.name}: use a .csv or .xlsx export.")


def load(path, profile_name):
    spec = profile(profile_name)
    fields = spec["fields"]
    required = [f for f, s in fields.items() if s.get("required")]
    rows = _rows(path)
    header, columns = None, {}
    for number, row in enumerate(rows, 1):
        names = {str(v).strip().lower(): i for i, v in enumerate(row) if str(v).strip()}
        found = {f: next((names[c.lower()] for c in s["columns"] if c.lower() in names), None) for f, s in fields.items()}
        if all(found[f] is not None for f in required):
            header, columns = number, {f: i for f, i in found.items() if i is not None}
            break
        if number >= MAX_HEADER_SEARCH:
            break
    if header is None:
        wanted = "; ".join(f"{f} ({' or '.join(fields[f]['columns'])})" for f in required)
        raise WorkbenchError(f"{Path(path).name} doesn't look like a {spec['name']} export: no row in the first "
                             f"{MAX_HEADER_SEARCH} has all required columns: {wanted}.")
    out = Loaded(profile=profile_name, filename=Path(path).name, records=[], header_row=header,
                 columns={f: fields[f]["columns"][0] for f in columns})
    keep = spec.get("keep")
    for number, row in enumerate(rows, header + 1):
        values = {f: (str(row[i]).strip() if i < len(row) and row[i] is not None else "") for f, i in columns.items()}
        if not all(values.get(f) for f in required):
            out.skipped += 1  # blank, group, title or total row
            continue
        if keep and values.get(keep["field"]) not in keep["values"]:
            out.excluded += 1
            continue
        record = {"source": f"{out.filename}:{number}"}
        for f, raw in values.items():
            kind = fields[f].get("type", "text")
            try:
                if not raw:
                    record[f] = None
                elif kind == "money":
                    record[f] = parse_money(raw)
                elif kind == "date":
                    record[f] = parse_date(raw, fields[f]["formats"])
                else:
                    record[f] = raw
            except (ValueError, InvalidOperation) as exc:
                raise WorkbenchError(f"{out.filename} row {number}, {f}: {exc}") from exc
        out.records.append(record)
    return out
