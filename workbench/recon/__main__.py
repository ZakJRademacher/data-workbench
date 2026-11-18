"""Reconcile Stripe payouts with bank deposits recorded in QuickBooks.

    python -m workbench.recon --payouts stripe_payouts.csv --itemized stripe_itemized.csv \\
        --books quickbooks.csv --period 2026-10 --out recon-2026-10

Export payouts from a week before the period start, so deposits early in the period
can be matched to payouts that arrived just before it. Writes summary.md,
matches.csv, exceptions.csv and result.json to the output folder.
"""
import argparse
import calendar
import csv
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..common import WorkbenchError, file_hash
from . import match, sources


def write_report(result, out, inputs, settings):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rule", "payouts", "deposits", "amount", "evidence"])
        for m in result.matches:
            w.writerow([m["rule"], " ".join(m["payouts"]), " ".join(m["deposits"]), f"{m['amount']:.2f}", m["evidence"]])
    with open(out / "exceptions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "payouts", "deposits", "amount", "detail", "action"])
        for e in result.exceptions:
            w.writerow([e["code"], " ".join(e["payouts"]), " ".join(e["deposits"]), f"{e['amount']:.2f}", e["detail"], e["action"]])
    t = result.tieout
    lines = [f"# Payout reconciliation, {settings.period_start} to {settings.period_end}", "",
             f"{result.counts['payouts']} Stripe payouts arrived in the period, totalling **${t['payouts_total']:,.2f}**.", "",
             "| Where the payouts went | Amount |", "|---|---:|"]
    lines += [f"| {k} | ${v:,.2f} |" for k, v in sorted(t["lines"].items(), key=lambda kv: -kv[1])]
    lines += [f"| **Unexplained** | **${t['unexplained']:,.2f}** |", ""]
    checks = [("Every payout is accounted for exactly once", t["every_payout_placed_once"]),
              ("Matched payouts equal matched deposits to the cent", t["matched_payouts_equal_matched_deposits"]),
              ("Nothing unexplained", t["unexplained"] == 0)]
    lines += [f"- {'✓' if ok else '✕'} {label}" for label, ok in checks] + [""]
    if result.exceptions:
        ref = lambda e: "<br>".join(e["payouts"] + [f"books row {d.rsplit(':', 1)[1]}" for d in e["deposits"]])
        lines += ["## Needs attention", "", "| Code | Records | Amount | Detail | What to do |", "|---|---|---:|---|---|"]
        lines += [f"| {e['code']} | {ref(e)} | ${e['amount']:,.2f} | {e['detail']} | {e['action']} |" for e in result.exceptions]
    else:
        lines += ["Nothing needs attention."]
    rules = {}
    for m in result.matches:
        rules[m["rule"]] = rules.get(m["rule"], 0) + 1
    lines += ["", "## How matches were made", "", "| Rule | Matches |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in rules.items()]
    lines += ["", f"Inputs: " + ", ".join(f"{Path(p).name} (sha256 {file_hash(p)[:12]}…)" for p in inputs)]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {"settings": {k: str(v) for k, v in vars(settings).items()}, "counts": result.counts,
               "tieout": t, "matches": result.matches, "exceptions": result.exceptions,
               "inputs": {Path(p).name: file_hash(p) for p in inputs}}
    (out / "result.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--payouts", type=Path, required=True, help="Stripe payouts export")
    parser.add_argument("--itemized", type=Path, help="Stripe itemized payout reconciliation report (enables arithmetic checks)")
    parser.add_argument("--books", type=Path, required=True, help="QuickBooks transaction detail for the bank account")
    parser.add_argument("--period", required=True, help="YYYY-MM")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--window-after", type=int, default=5, help="days a deposit may post after the payout arrives")
    parser.add_argument("--processor", default="stripe", help="how deposits name the processor (regular expression)")
    args = parser.parse_args(argv)
    year, month = map(int, args.period.split("-"))
    settings = match.Settings(window_after=args.window_after, processor=args.processor,
                              period_start=date(year, month, 1), period_end=date(year, month, calendar.monthrange(year, month)[1]))
    try:
        payouts = sources.load(args.payouts, "stripe_payouts")
        books = sources.load(args.books, "quickbooks_transaction_detail")
        itemized = sources.load(args.itemized, "stripe_itemized_payouts") if args.itemized else None
    except WorkbenchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    result = match.reconcile(payouts.records, books.records, itemized.records if itemized else None, settings)
    inputs = [p for p in (args.payouts, args.itemized, args.books) if p]
    write_report(result, args.out, inputs, settings)
    print((args.out / "summary.md").read_text(encoding="utf-8"))
    return 0 if result.tieout["unexplained"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
