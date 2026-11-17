"""Prepare the input files for one run, in sample or full mode.

Sample mode (every push) takes a deterministic slice of each dataset and keeps
related tables consistent with it. Full mode (nightly) uses every row. The
Excel and Windows-1252 variants are always built from the real rows.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

SAMPLE = {"olist_orders": 5000, "flights": 30000, "retail": 60000, "excel_rows_per_month": 4000}
OLIST = {"orders": "olist_orders_dataset", "items": "olist_order_items_dataset",
         "payments": "olist_order_payments_dataset", "customers": "olist_customers_dataset",
         "products": "olist_products_dataset"}


def read(path, encoding="utf-8"):
    with open(path, encoding=encoding, newline="") as stream:
        rows = list(csv.reader(stream))
    return rows[0], rows[1:]


def write(path, header, rows, encoding="utf-8"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # A few names have characters Windows-1252 lacks; they become "?" as a real export would.
    with open(path, "w", encoding=encoding, errors="replace", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    return Path(path)


def olist(cache: Path, work: Path, full: bool) -> dict:
    source = {k: cache / "olist" / f"{v}.csv" for k, v in OLIST.items()}
    if full:
        return source
    out = {}
    header, orders = read(source["orders"])
    orders = orders[:SAMPLE["olist_orders"]]
    order_ids = {r[0] for r in orders}
    customer_ids = {r[1] for r in orders}
    out["orders"] = write(work / "olist/orders.csv", header, orders)
    header, items = read(source["items"])
    items = [r for r in items if r[0] in order_ids]
    product_ids = {r[2] for r in items}
    out["items"] = write(work / "olist/items.csv", header, items)
    header, rows = read(source["payments"])
    out["payments"] = write(work / "olist/payments.csv", header, [r for r in rows if r[0] in order_ids])
    header, rows = read(source["customers"])
    out["customers"] = write(work / "olist/customers.csv", header, [r for r in rows if r[0] in customer_ids])
    header, rows = read(source["products"])
    out["products"] = write(work / "olist/products.csv", header, [r for r in rows if r[0] in product_ids])
    return out


def flights(cache: Path, work: Path, full: bool) -> dict:
    source = {n: cache / "flights" / f"{n}.csv" for n in ("flights", "planes", "airports", "airlines", "weather")}
    if full:
        return source
    header, rows = read(source["flights"])
    return {**source, "flights": write(work / "flights/flights.csv", header, rows[:SAMPLE["flights"]])}


def retail(cache: Path, work: Path, full: bool) -> Path:
    source = cache / "retail/online-retail.csv"
    if full:
        return source
    header, rows = read(source)
    return write(work / "retail/online-retail.csv", header, rows[:SAMPLE["retail"]])


def retail_excel(cache: Path, work: Path, full: bool) -> list[Path]:
    """Two monthly .xlsx exports with real date cells, as Excel or pandas would save them.

    December keeps the time of day (timestamp cells); January holds date-only cells.
    """
    header, rows = read(cache / "retail/online-retail.csv")
    limit = None if full else SAMPLE["excel_rows_per_month"]
    months = {"2010-12": [], "2011-01": []}
    for r in rows:
        stamp = datetime.strptime(r[4], "%m/%d/%Y %H:%M")
        key = stamp.strftime("%Y-%m")
        if key in months and (limit is None or len(months[key]) < limit):
            if key == "2011-01":
                stamp = stamp.replace(hour=0, minute=0)
            months[key].append([r[0], r[1], r[2], int(r[3]), stamp, float(r[5]),
                                int(r[6]) if r[6] else None, r[7]])
    paths = []
    for key, data in months.items():
        book = Workbook(write_only=True)
        sheet = book.create_sheet(key)
        sheet.append(header)
        for row in data:
            sheet.append(row)
        path = work / f"excel/retail_{key}.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        book.save(path)
        paths.append(path)
    return paths


def us_airports_cp1252(cache: Path, work: Path) -> Path:
    """A real US airport list saved as Windows-1252, whose first accented name sits past 128 KB."""
    header, rows = read(cache / "airports/airport-codes.csv")
    us = [r for r in rows if r[5] == "US"]
    plain = [r for r in us if all(v.isascii() for v in r)]
    accented = [r for r in us if not all(v.isascii() for v in r)]
    ordered = plain[:3000] + accented + plain[3000:]
    path = write(work / "encoding/us-airports.csv", header, ordered, encoding="cp1252")
    first = next(i for i, b in enumerate(path.read_bytes()) if b > 127)
    assert first > 128 * 1024, "fixture must place the first accent beyond the sniffing sample"
    return path
