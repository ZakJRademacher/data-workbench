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
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..common import WorkbenchError, file_hash
from . import close, match, sources


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
    if earlier := result.counts.get('settled_from_prior_period'):
        lines += [f"{earlier} earlier payouts were settled in this run and are listed separately from the current month's totals.", ""]
    if still := result.counts.get('still_open_from_prior'):
        lines += [f"**{still} payout(s) from earlier months are still not deposited, totalling "
                  f"${t['still_open_from_prior_total']:,.2f}.** They are listed below until they're settled.", ""]
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
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'orders':
        from . import orders
        try:
            return orders.main(argv[1:])
        except WorkbenchError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--payouts", type=Path, required=True, help="Stripe payouts export")
    parser.add_argument("--itemized", type=Path, help="Stripe itemized payout reconciliation report (enables arithmetic checks)")
    parser.add_argument("--books", type=Path, required=True, help="QuickBooks transaction detail for the bank account")
    parser.add_argument("--period", required=True, help="YYYY-MM")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--window-after", type=int, default=5, help="days a deposit may post after the payout arrives")
    parser.add_argument("--processor", default="stripe", help="how deposits name the processor (regular expression)")
    parser.add_argument('--close-account', help='Bank account label used to save the close state.')
    parser.add_argument('--prior-close', type=Path, help='The preceding month’s close-state.json for this account.')
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}', args.period):
            raise ValueError()
        year, month = map(int, args.period.split("-"))
        period_start = date(year, month, 1)
    except ValueError:
        parser.error('--period must be a valid month in YYYY-MM form.')
    if args.prior_close and not args.close_account:
        parser.error('--prior-close requires the same --close-account label as the preceding month.')
    if args.prior_close and args.prior_close.resolve() == (args.out / 'close-state.json').resolve():
        parser.error('--out must be different from the prior close folder.')
    settings = match.Settings(window_after=args.window_after, processor=args.processor,
                              period_start=period_start, period_end=date(year, month, calendar.monthrange(year, month)[1]))
    prior = {}
    try:
        payouts = sources.load(args.payouts, "stripe_payouts")
        books = sources.load(args.books, "quickbooks_transaction_detail")
        itemized = sources.load(args.itemized, "stripe_itemized_payouts") if args.itemized else None
        if args.close_account:
            if len({p['payout_id'] for p in payouts.records}) != len(payouts.records):
                raise WorkbenchError('Remove repeated payout IDs before saving a close.')
            currency = close.account_currency(payouts.records, itemized.records if itemized else None)
            if args.prior_close:
                prior = close.read_prior(args.prior_close, args.close_account, currency, settings)
    except WorkbenchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    result = match.reconcile(payouts.records, books.records, itemized.records if itemized else None, settings, prior)
    inputs = [p for p in (args.payouts, args.itemized, args.books) if p]
    write_report(result, args.out, inputs, settings)
    if args.close_account and result.tieout['unexplained'] == 0:
        close.save_close(args.out / 'close-state.json', args.close_account, currency, settings, result.payouts, result, prior,
                         inputs + ([args.prior_close] if args.prior_close else []))
    print((args.out / "summary.md").read_text(encoding="utf-8"))
    return 0 if result.tieout["unexplained"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
