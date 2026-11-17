"""Independent expected results, computed straight from the raw files.

Only the standard library is used here: csv, decimal, datetime and collections.
Nothing is imported from the workbench or DuckDB, so a defect in the app cannot
also hide in the answer it is checked against.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Published or long-established facts about the full datasets, checked nightly.
FULL_FACTS = {
    "olist_rows": {"orders": 99441, "items": 112650, "payments": 103886, "customers": 99441, "products": 32951},
    "olist_payment_total": Decimal("16008872.12"),
    "olist_late_orders": 7827,
    "flights_rows": 336776,
    "flights_without_plane": 52606,
    "flights_without_destination_airport": 7602,
    "retail_rows": 541909,
    "retail_exact_repeat_copies": 5268,
}

# What the app's documented Decimal rule accepts: plain dot decimals, at most 12 places.
PLAIN_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")


def rows(path, encoding="utf-8"):
    with open(path, encoding=encoding, newline="") as stream:
        return list(csv.DictReader(stream))


def is_plain_decimal(text: str) -> bool:
    return bool(PLAIN_DECIMAL.fullmatch(text)) and len(text.partition(".")[2].rstrip("0")) <= 12


def olist(files: dict) -> dict:
    orders, items = rows(files["orders"]), rows(files["items"])
    payments, customers, products = rows(files["payments"]), rows(files["customers"]), rows(files["products"])
    stamp = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    late = {o["order_id"] for o in orders if o["order_delivered_customer_date"]
            and stamp(o["order_delivered_customer_date"]) > stamp(o["order_estimated_delivery_date"])}
    per_order = defaultdict(lambda: {"item_count": 0, "items_price": Decimal(0), "items_freight": Decimal(0), "paid": None})
    for i in items:
        agg = per_order[i["order_id"]]
        agg["item_count"] += 1
        agg["items_price"] += Decimal(i["price"])
        agg["items_freight"] += Decimal(i["freight_value"])
    for p in payments:
        agg = per_order[p["order_id"]]
        agg["paid"] = (agg["paid"] or Decimal(0)) + Decimal(p["payment_value"])
    accepted = [o for o in orders if o["order_id"] not in late]
    summary = {}
    for o in accepted:
        agg = per_order.get(o["order_id"])
        summary[o["order_id"]] = {
            "item_count": agg["item_count"] if agg and agg["item_count"] else None,
            "items_price": agg["items_price"] if agg and agg["item_count"] else None,
            "items_freight": agg["items_freight"] if agg and agg["item_count"] else None,
            "paid": agg["paid"] if agg else None,
        }
    return {
        "rows": {"orders": len(orders), "items": len(items), "payments": len(payments),
                 "customers": len(customers), "products": len(products)},
        "price_total": sum((Decimal(i["price"]) for i in items), Decimal(0)),
        "freight_total": sum((Decimal(i["freight_value"]) for i in items), Decimal(0)),
        "payment_total": sum((Decimal(p["payment_value"]) for p in payments), Decimal(0)),
        "late": late,
        "summary": summary,
        "orders_by_id": {o["order_id"]: {k: v.strip() for k, v in o.items()} for o in orders},
    }


def flights(files: dict) -> dict:
    value = lambda s: None if s in ("", "NA") else s
    flights = rows(files["flights"])
    planes = {p["tailnum"] for p in rows(files["planes"])}
    faa = {a["faa"] for a in rows(files["airports"])}
    weather = rows(files["weather"])
    decimal_fields, integer_fields = ("temp", "dewp", "humid", "precip", "pressure", "visib"), ("wind_dir",)
    held = set()
    for w in weather:
        bad = any(value(w[f]) is not None and not is_plain_decimal(w[f]) for f in decimal_fields)
        bad = bad or any(value(w[f]) is not None and not re.fullmatch(r"[+-]?[0-9]+", w[f]) for f in integer_fields)
        if bad:
            held.add((w["origin"], w["time_hour"]))
    keys = Counter((w["origin"], w["time_hour"]) for w in weather)
    usable_weather = {k for k, n in keys.items() if n == 1 and k not in held}

    def total(field):
        present = [int(f[field]) for f in flights if value(f[field]) is not None]
        return sum(present), len(present)

    return {
        "rows": len(flights),
        "totals": {f: total(f) for f in ("distance", "arr_delay", "dep_delay", "air_time")},
        "weather_held": held,
        "without_plane": sum(1 for f in flights if value(f["tailnum"]) is None or f["tailnum"] not in planes),
        "without_destination_airport": sum(1 for f in flights if f["dest"] not in faa),
        "without_weather": sum(1 for f in flights if (f["origin"], f["time_hour"]) not in usable_weather),
    }


def retail_key(r: dict) -> tuple:
    """A row's business identity: trimmed text, whole-number quantity, exact price."""
    return (r["InvoiceNo"].strip(), r["StockCode"].strip(), r["Description"].strip(), int(r["Quantity"]),
            r["InvoiceDate"].strip(), Decimal(r["UnitPrice"]), r["CustomerID"].strip(), r["Country"].strip())


def retail(path) -> dict:
    data = rows(path)
    seen, kept = set(), []
    for r in data:
        key = retail_key(r)
        if key not in seen:
            seen.add(key)
            kept.append(r)
    return {
        "rows": len(data),
        "repeat_copies": len(data) - len(kept),
        "quantity_total": sum(int(r["Quantity"]) for r in kept),
        "price_total": sum((Decimal(r["UnitPrice"]) for r in kept), Decimal(0)),
    }


def retail_excel(paths) -> dict:
    """Expected Date conversion for the Excel exports, read back with openpyxl only."""
    from openpyxl import load_workbook
    accepted, held = Counter(), 0
    for path in paths:
        book = load_workbook(path, read_only=True)
        for row in list(book.active.iter_rows(values_only=True))[1:]:
            stamp = row[4]
            if (stamp.hour, stamp.minute, stamp.second) == (0, 0, 0):
                accepted[(str(row[0]), str(row[1]), stamp.date())] += 1
            else:
                held += 1
        book.close()
    return {"accepted": accepted, "held": held}


def airports(path) -> dict:
    data = rows(path)
    return {"rows": len(data), "unique_idents": len({r["ident"] for r in data}),
            "by_ident": {r["ident"]: {k: v.strip() for k, v in r.items()} for r in data}}


def cp1252_airports(path) -> dict:
    data = rows(path, encoding="cp1252")
    return {"rows": len(data), "names": sorted(r["name"].strip() for r in data)}
