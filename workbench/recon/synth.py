"""Synthetic stores with known truth, for scoring the reconciliation.

    python -m workbench.recon.synth --out demo --seed 7

Generates one month for a fictional store: card charges at common price points
(so amounts collide, as they do in real shops), Stripe fees, refunds, daily
automatic payouts two business days after each charge, and the bookkeeper's
QuickBooks deposits. Then it injects the faults bookkeepers actually make and
records the truth for every payout and deposit:

    unbooked payouts, deposits booked at gross instead of net, two payouts booked
    as one deposit, duplicate deposits, a mistyped amount on a deposit that cites
    its payout, deposits that don't name the processor, other income whose amount
    collides with a payout, itemized exports missing a row, and payouts that
    arrive at the period end and are deposited in the next period.

Weekends are not business days; bank holidays are ignored.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import string
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

PRICES = [Decimal(p) for p in ("12.00", "18.50", "19.99", "24.99", "29.99", "34.00", "39.00", "45.00", "49.99", "64.00", "79.00", "120.00")]
SHIPPING = [Decimal(s) for s in ("0.00", "0.00", "4.99", "5.99", "8.50")]
CENT = Decimal("0.01")

RATES = {"unbooked": 0.05, "gross_booked": 0.03, "combined": 0.06, "duplicate": 0.02, "typo_with_id": 0.015,
         "no_processor_name": 0.04, "itemized_missing_row": 0.02, "other_income": 4, "collision_share": 0.5}


def fee(amount):
    return (amount * Decimal("0.029") + Decimal("0.30")).quantize(CENT, ROUND_HALF_UP)


def business_days_after(day, n):
    while n > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def next_business_day(day):
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


@dataclass
class Store:
    period_start: date
    period_end: date
    payouts: list = field(default_factory=list)
    transactions: list = field(default_factory=list)
    deposits: list = field(default_factory=list)   # dicts with truth attached under "_truth"
    truth: dict = field(default_factory=dict)


def _id(rng, prefix, n=24):
    return prefix + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(n))


def generate(seed=0, year=2026, month=10, orders_per_day=(8, 45), rates=None):
    rng = random.Random(seed)
    rates = {**RATES, **(rates or {})}
    start = date(year, month, 1)
    end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    store = Store(start, end)
    lam = rng.uniform(*orders_per_day)

    # Charges from 12 days before the period, so early payouts settle earlier sales.
    pending = []  # (available_on, txn)
    day = start - timedelta(days=12)
    charges = []
    while day <= end + timedelta(days=3):
        for _ in range(max(0, int(rng.gauss(lam, lam ** 0.5)))):
            amount = sum(rng.choice(PRICES) * rng.choice([1, 1, 1, 2, 3]) for _ in range(rng.choice([1, 1, 2]))) + rng.choice(SHIPPING)
            created = datetime(day.year, day.month, day.day, rng.randrange(24), rng.randrange(60), rng.randrange(60))
            t = {"id": _id(rng, "txn_"), "category": "charge", "gross": amount, "fee": -fee(amount), "created": created}
            t["net"] = t["gross"] + t["fee"]
            charges.append(t)
            pending.append((business_days_after(day, 2), t))
            if rng.random() < 0.03:  # refunded later, fully or partly; Stripe keeps the fee
                back = amount if rng.random() < 0.7 else (amount * Decimal(rng.choice(["0.5", "0.25"]))).quantize(CENT)
                rday = day + timedelta(days=rng.randint(1, 10))
                r = {"id": _id(rng, "txn_"), "category": "refund", "gross": -back, "fee": Decimal("0.00"), "net": -back,
                     "created": datetime(rday.year, rday.month, rday.day, rng.randrange(24), rng.randrange(60))}
                pending.append((business_days_after(rday, 2), r))
        day += timedelta(days=1)

    # Daily automatic payouts on business days: everything available since the last payout.
    pending.sort(key=lambda x: x[0])
    day, i, carry = start - timedelta(days=9), 0, []
    while day <= end:
        if day.weekday() < 5:
            batch = carry
            while i < len(pending) and pending[i][0] <= day:
                batch.append(pending[i][1])
                i += 1
            total = sum((t["net"] for t in batch), Decimal(0))
            if batch and total > 0:
                pid = _id(rng, "po_")
                for t in batch:
                    t["payout_id"], t["payout_effective"] = pid, day
                store.transactions.extend(batch)
                store.payouts.append({"payout_id": pid, "amount": total, "arrival": day,
                                      "gross_charges": sum((t["gross"] for t in batch if t["category"] == "charge"), Decimal(0))})
                carry = []
            else:
                carry = batch
        day += timedelta(days=1)

    # The bookkeeper's deposits, with faults. Truth: payout -> expected outcome.
    deposits = []
    truth_payouts, i = {}, 0
    ps = store.payouts
    current = [p for p in ps if p["arrival"] >= start]
    while i < len(ps):
        p = ps[i]
        lag = rng.choice([0, 0, 1, 1, 2])
        dday = next_business_day(p["arrival"] + timedelta(days=lag))
        memo = rng.choice(["STRIPE TRANSFER", "Stripe payout", "Stripe", "Transfer from Stripe"])
        name = rng.choice(["Stripe", "", "Stripe, Inc."])
        if rng.random() < 0.3:
            memo = f"{memo} {p['payout_id']}"
        roll = rng.random()
        r = rates
        nxt = ps[i + 1] if i + 1 < len(ps) else None
        if nxt and roll < r["combined"] and nxt["arrival"] - p["arrival"] <= timedelta(days=3):
            dday = next_business_day(nxt["arrival"] + timedelta(days=rng.choice([0, 1])))
            dep = {"date": dday, "amount": p["amount"] + nxt["amount"], "name": name or "Stripe",
                   "memo": "STRIPE TRANSFER", "_truth": {"payouts": [p["payout_id"], nxt["payout_id"]]}}
            deposits.append(dep)
            truth_payouts[p["payout_id"]] = truth_payouts[nxt["payout_id"]] = {"expect": "matched", "deposit": dep}
            i += 2
            continue
        roll -= r["combined"]
        if roll < r["unbooked"]:
            truth_payouts[p["payout_id"]] = {"expect": "no_deposit"}
        elif (roll := roll - r["unbooked"]) < r["gross_booked"]:
            dep = {"date": dday, "amount": p["gross_charges"], "name": name, "memo": memo.split(" po_")[0],
                   "_truth": {"payouts": [], "gross_of": p["payout_id"]}}
            deposits.append(dep)
            truth_payouts[p["payout_id"]] = {"expect": "GROSS_BOOKED", "deposit": dep}
        elif (roll := roll - r["gross_booked"]) < r["typo_with_id"]:
            dep = {"date": dday, "amount": p["amount"] + Decimal(rng.choice(["9.00", "-9.00", "0.90", "90.00"])),
                   "name": name, "memo": f"STRIPE TRANSFER {p['payout_id']}", "_truth": {"payouts": [], "typo_of": p["payout_id"]}}
            deposits.append(dep)
            truth_payouts[p["payout_id"]] = {"expect": "AMOUNT_MISMATCH", "deposit": dep}
        else:
            roll -= r["typo_with_id"]
            no_name = roll < r["no_processor_name"]
            dep = {"date": dday, "amount": p["amount"], "name": "" if no_name else name,
                   "memo": f"DEPOSIT {rng.randint(100000, 999999)}" if no_name else memo, "_truth": {"payouts": [p["payout_id"]]}}
            deposits.append(dep)
            truth_payouts[p["payout_id"]] = {"expect": "matched", "deposit": dep}
            if rng.random() < r["duplicate"]:
                dup = {**dep, "date": next_business_day(dday + timedelta(days=rng.choice([0, 1, 3]))),
                       "_truth": {"payouts": [], "duplicate_of": p["payout_id"]}}
                deposits.append(dup)
        i += 1

    # Other income, some deliberately equal to a nearby payout's amount.
    for _ in range(rates["other_income"]):
        target = rng.choice(current) if current else None
        collide = target is not None and rng.random() < rates["collision_share"]
        amount = target["amount"] if collide else Decimal(rng.randint(5000, 250000)) / 100
        dday = next_business_day((target["arrival"] if collide else start + timedelta(days=rng.randint(0, 27)))
                                 + timedelta(days=rng.choice([0, 1, 2])))
        deposits.append({"date": dday, "amount": amount, "name": rng.choice(["Square", "Customer check", "Etsy", "Wholesale - Brightline"]),
                         "memo": rng.choice(["", "Invoice payment", "Mobile deposit"]), "_truth": {"payouts": [], "other": True}})

    # The books cover the period only: later deposits are in next month's books.
    store.deposits = [d for d in deposits if start <= d["date"] <= end]
    for pid, t in truth_payouts.items():
        if "deposit" in t and t["deposit"]["date"] > end:
            t["expect"] = "in_transit"
    # Itemized export missing a row for some payouts.
    broken = {p["payout_id"] for p in current if rng.random() < rates["itemized_missing_row"]}
    dropped = set()
    for t in store.transactions:
        if t["payout_id"] in broken and t["payout_id"] not in dropped:
            dropped.add(t["payout_id"])
            t["_drop"] = True
    store.truth = {"payouts": truth_payouts, "arithmetic": sorted(broken), "current": [p["payout_id"] for p in current]}
    return store


def write(store, folder):
    """Write the three exports in their source layouts. Returns their paths."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    money = lambda x: f"{x:.2f}"
    with open(folder / "stripe_payouts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "Amount", "Currency", "Arrival Date", "Status"])
        for p in store.payouts:
            w.writerow([p["payout_id"], money(p["amount"]), "usd", f"{p['arrival']} 00:00:00", "paid"])
    with open(folder / "stripe_itemized.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["automatic_payout_id", "automatic_payout_effective_at", "balance_transaction_id", "created_utc",
                    "reporting_category", "gross", "fee", "net", "currency", "description"])
        for t in store.transactions:
            if not t.get("_drop"):
                w.writerow([t["payout_id"], f"{t['payout_effective']} 00:00:00", t["id"], t["created"].strftime("%Y-%m-%d %H:%M:%S"),
                            t["category"], money(t["gross"]), money(t["fee"]), money(t["net"]), "usd", ""])
    # QuickBooks report layout: title rows, a blank first column, a group row, totals, other transaction types.
    rows = [["Northwind Goods (fictional)"], ["Transaction Detail by Account"],
            [f"{store.period_start:%B} {store.period_start.day}-{store.period_end.day}, {store.period_end.year}"], [],
            ["", "Date", "Transaction Type", "Num", "Name", "Memo/Description", "Split", "Amount", "Balance"],
            ["Checking (1010)"]]
    rng = random.Random(len(store.deposits))
    lines = [(d["date"], "Deposit", d) for d in store.deposits]
    lines += [(store.period_start + timedelta(days=rng.randint(0, 27)), kind, None)
              for kind in ("Expense", "Transfer", "Bill Payment (Check)", "Expense")]
    balance = Decimal("10000.00")
    for when, kind, d in sorted(lines, key=lambda x: (x[0], x[1])):
        amount = d["amount"] if d else -Decimal(rng.randint(2000, 90000)) / 100
        balance += amount
        rows.append(["", when.strftime("%m/%d/%Y"), kind, "" if d else str(rng.randint(1000, 9999)),
                     d["name"] if d else rng.choice(["Shopify", "Gusto", "Landlord LLC"]), d["memo"] if d else "",
                     "Undeposited Funds" if d else "-Split-", f"{amount:,.2f}", f"{balance:,.2f}"])
        if d is not None:
            d["source"] = f"quickbooks.csv:{len(rows)}"
    rows += [["Total for Checking (1010)", "", "", "", "", "", "", f"{sum(Decimal(r[7].replace(',', '')) for r in rows[6:]):,.2f}", ""],
             [], ["TOTAL", "", "", "", "", "", "", "", ""], [], [f"Accrual basis {datetime(2026, 11, 3, 9, 14):%A, %B %d, %Y %I:%M %p}"]]
    with open(folder / "quickbooks.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return folder / "stripe_payouts.csv", folder / "stripe_itemized.csv", folder / "quickbooks.csv"


def score(store, result):
    """Compare a reconciliation result with the truth. Wrong automatic matches must be zero."""
    by_source = {d["source"]: d for d in store.deposits}
    truth = store.truth["payouts"]
    wrong = [m for m in result.matches if any(sorted(by_source[d]["_truth"]["payouts"]) != sorted(m["payouts"]) for d in m["deposits"])]
    codes = {}
    for e in result.exceptions:
        for p in e["payouts"]:
            codes.setdefault(p, set()).add(e["code"])
    matched = {p for m in result.matches for p in m["payouts"]}
    expected_faults, found, reviewed, other_code, missed = 0, 0, 0, 0, []
    for pid in store.truth["current"]:
        t = truth.get(pid, {"expect": "no_deposit"})
        want = {"no_deposit": {"PAYOUT_NOT_DEPOSITED", "PAYOUT_IN_TRANSIT"}, "in_transit": {"PAYOUT_IN_TRANSIT"},
                "GROSS_BOOKED": {"GROSS_BOOKED"}, "AMOUNT_MISMATCH": {"AMOUNT_MISMATCH"}}.get(t["expect"])
        if want is None:
            continue
        expected_faults += 1
        got = codes.get(pid, set())
        if got & want:
            found += 1
        elif "AMBIGUOUS" in got:
            reviewed += 1
        elif got and pid not in matched:
            other_code += 1  # flagged, under another code (e.g. an incomplete itemized export hides a gross booking)
        else:
            missed.append((pid, t["expect"], sorted(got), pid in matched))
    arithmetic_found = {p for e in result.exceptions if e["code"] == "PAYOUT_ARITHMETIC" for p in e["payouts"]}
    duplicates = [d for d in store.deposits if "duplicate_of" in d["_truth"]]
    flagged = {d for e in result.exceptions for d in e["deposits"]}
    return {
        "payouts": len(store.truth["current"]), "deposits": len(store.deposits),
        "auto_matched_payouts": len(matched & set(store.truth["current"])),
        "wrong_matches": len(wrong), "wrong_detail": wrong[:3],
        "faults": expected_faults, "faults_found": found, "faults_in_review": reviewed, "faults_other_code": other_code,
        "faults_missed": missed,
        "arithmetic_expected": len(store.truth["arithmetic"]),
        "arithmetic_found": len(arithmetic_found & set(store.truth["arithmetic"])),
        "arithmetic_false": len(arithmetic_found - set(store.truth["arithmetic"])),
        "duplicates": len(duplicates), "duplicates_flagged": sum(d["source"] in flagged for d in duplicates),
        "false_duplicate_flags": sum(1 for e in result.exceptions if e["code"] == "POSSIBLE_DUPLICATE"
                                     for d in e["deposits"] if "duplicate_of" not in by_source[d]["_truth"]),
        "review_payouts": sum(len(e["payouts"]) for e in result.exceptions if e["code"] == "AMBIGUOUS"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    store = generate(args.seed)
    paths = write(store, args.out)
    (args.out / "truth.json").write_text(json.dumps(store.truth, default=str, indent=1))
    print("Wrote", ", ".join(str(p) for p in paths), "and truth.json")
