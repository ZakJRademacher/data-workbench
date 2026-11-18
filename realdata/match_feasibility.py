"""How well can payments be matched to orders without a shared ID? Measured on real Olist data.

    python -m realdata.match_feasibility --cache .realdata-cache

Each order's payments are treated as one processor charge dated at the order's
approval time, and the order IDs are hidden. A charge is matched to orders with
the same amount (to the cent) placed within the date window before it,
optionally also requiring the same customer postcode prefix. Because the true
order is known, every outcome is scored: the only candidate is correct, the
only candidate is WRONG (a silent error), several candidates (ambiguous), or
none. Orders whose payments don't equal their item total are left out.

Used as evidence in docs/RECONCILIATION_DESIGN.md.
"""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(folder):
    read = lambda name: list(csv.DictReader(open(folder / f"{name}.csv", encoding="utf-8", newline="")))
    orders = {o["order_id"]: o for o in read("olist_orders_dataset")}
    postcode = {c["customer_id"]: c["customer_zip_code_prefix"] for c in read("olist_customers_dataset")}
    totals, paid = defaultdict(Decimal), defaultdict(Decimal)
    for i in read("olist_order_items_dataset"):
        totals[i["order_id"]] += Decimal(i["price"]) + Decimal(i["freight_value"])
    for p in read("olist_order_payments_dataset"):
        paid[p["order_id"]] += Decimal(p["payment_value"])
    return orders, postcode, totals, paid


def evaluate(orders, postcode, totals, paid, window, use_postcode):
    day = lambda s: datetime.strptime(s[:10], "%Y-%m-%d").date()
    usable = [o for o in totals if o in paid and orders[o]["order_approved_at"] and abs(totals[o] - paid[o]) <= Decimal("0.01")]
    by_amount = defaultdict(list)
    for o in usable:
        by_amount[totals[o]].append(o)
    zip_of = lambda o: postcode[orders[o]["customer_id"]]
    outcome = Counter()
    for o in usable:
        charged = day(orders[o]["order_approved_at"])
        candidates = [c for c in by_amount[paid[o]]
                      if 0 <= (charged - day(orders[c]["order_purchase_timestamp"])).days <= window
                      and (not use_postcode or zip_of(c) == zip_of(o))]
        outcome["correct" if candidates == [o] else "wrong" if len(candidates) == 1
                else "ambiguous" if candidates else "none"] += 1
    return len(usable), outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=ROOT / ".realdata-cache")
    args = parser.parse_args()
    from realdata.fetch import fetch
    data = load(fetch(args.cache) / "olist")
    print("| Match on | Charges | Correct | Wrong | Ambiguous | No candidate |\n|---|---:|---:|---:|---:|---:|")
    for window in (1, 3, 7):
        for use_postcode in (False, True):
            n, c = evaluate(*data, window, use_postcode)
            label = f"amount + date (±{window}d)" + (" + postcode" if use_postcode else "")
            print(f"| {label} | {n:,} | " + " | ".join(f"{c[k] / n:.2%}" for k in ("correct", "wrong", "ambiguous", "none")) + " |")


if __name__ == "__main__":
    main()
