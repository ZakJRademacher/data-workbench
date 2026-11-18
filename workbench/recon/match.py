"""Phase 1: Stripe payouts against bank deposits in the books, and payout arithmetic.

Rules run in order of strength; each match records the rule and its evidence.
Nothing is matched automatically unless it is the only candidate on both sides.

1. exact_id      the payout ID appears in the deposit's memo, number or name
2. corroborated  same amount, deposit dated within the window, and the deposit names the processor
3. combined      2-4 payouts arriving consecutively, summing exactly to one processor deposit
Anything else that has candidates goes to review as AMBIGUOUS; the tool never picks.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

PAYOUT_ID = re.compile(r"\bpo_[A-Za-z0-9]{6,}\b")


def _money(x):
    return f"${x:,.2f}" if x >= 0 else f"-${-x:,.2f}"


@dataclass
class Settings:
    window_before: int = 1        # a deposit may be dated this many days before the payout's arrival
    window_after: int = 5         # ...or this many days after (bank posting, weekends, holidays)
    processor: str = r"stripe"    # how the processor is named in deposit names and memos
    combine_max: int = 4          # most payouts one deposit may combine
    period_start: date | None = None
    period_end: date | None = None

    def in_window(self, deposit_date, arrival):
        return arrival - timedelta(days=self.window_before) <= deposit_date <= arrival + timedelta(days=self.window_after)


@dataclass
class Result:
    matches: list = field(default_factory=list)      # {"rule", "payouts": [ids], "deposits": [sources], "amount", "evidence"}
    exceptions: list = field(default_factory=list)   # {"code", "payouts", "deposits", "amount", "detail", "action"}
    tieout: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)


ACTIONS = {
    "PAYOUT_NOT_DEPOSITED": "Find the deposit in the bank feed and record it, or confirm the payout failed.",
    "PAYOUT_IN_TRANSIT": "Arrived near the period end; expect the deposit in the next period.",
    "DEPOSIT_NO_PAYOUT": "Check where this deposit came from; it names the processor but matches no payout.",
    "GROSS_BOOKED": "The deposit was recorded at the payout's gross sales, not the net amount paid. Record fees and refunds separately.",
    "AMOUNT_MISMATCH": "The deposit references this payout but the amounts differ. Correct the deposit amount.",
    "AMBIGUOUS": "Several records fit equally well. Choose the right pairing in review; the decision is logged.",
    "PAYOUT_ARITHMETIC": "The payout doesn't equal the sum of its transactions. Re-export the itemized report for a wider date range.",
    "POSSIBLE_DUPLICATE": "Another deposit already matched this payout. Check whether it was recorded twice.",
    "EARLIER_PAYOUT": "This deposit fits a payout from before the period. Confirm it wasn't already recorded in last period's books.",
}


def _mentions_processor(deposit, settings):
    text = " ".join(str(deposit.get(k) or "") for k in ("name", "memo", "num"))
    return re.search(settings.processor, text, re.I) is not None


def _ids_in(deposit):
    text = " ".join(str(deposit.get(k) or "") for k in ("name", "memo", "num"))
    return set(PAYOUT_ID.findall(text))


def reconcile(payouts, deposits, transactions=None, settings=None):
    """payouts: [{"payout_id", "amount", "arrival", ...}]; deposits: [{"source", "date", "amount", "name", "memo", ...}]."""
    s = settings or Settings()
    res = Result()
    # Payouts that arrived shortly before the period may be deposited inside it: they can be claimed
    # by a deposit, but they don't count toward this period and aren't reported if unclaimed.
    lo = s.period_start - timedelta(days=s.window_after) if s.period_start else None
    payouts = [p for p in payouts if (lo is None or p["arrival"] >= lo) and (s.period_end is None or p["arrival"] <= s.period_end)]
    current = {p["payout_id"] for p in payouts if s.period_start is None or p["arrival"] >= s.period_start}
    # Deposits up to the window after the period end can still settle payouts that arrived in it.
    late = s.period_end + timedelta(days=s.window_after) if s.period_end else None
    early = s.period_start - timedelta(days=s.window_before) if s.period_start else None
    deposits = [d for d in deposits if d["amount"] > 0 and (early is None or d["date"] >= early) and (late is None or d["date"] <= late)]
    by_id = {p["payout_id"]: p for p in payouts}
    open_p = {p["payout_id"] for p in payouts}
    open_d = {d["source"] for d in deposits}
    dep = {d["source"]: d for d in deposits}

    def match(rule, pids, dsrcs, evidence):
        res.matches.append({"rule": rule, "payouts": pids, "deposits": dsrcs,
                            "amount": sum((by_id[p]["amount"] for p in pids), Decimal(0)), "evidence": evidence})
        open_p.difference_update(pids)
        open_d.difference_update(dsrcs)

    def exception(code, pids, dsrcs, detail):
        amount = sum((by_id[p]["amount"] for p in pids), Decimal(0)) if pids else sum((dep[d]["amount"] for d in dsrcs), Decimal(0))
        res.exceptions.append({"code": code, "payouts": pids, "deposits": dsrcs, "amount": amount,
                               "detail": detail, "action": ACTIONS[code]})

    # 1. Exact payout ID in the deposit.
    for d in deposits:
        refs = [r for r in _ids_in(d) if r in by_id]
        if len(refs) != 1:
            continue
        p = by_id[refs[0]]
        if p["payout_id"] not in open_p or d["source"] not in open_d:
            continue
        if d["amount"] == p["amount"]:
            match("exact_id", [p["payout_id"]], [d["source"]], f"deposit references {p['payout_id']}")
        else:
            exception("AMOUNT_MISMATCH", [p["payout_id"]], [d["source"]],
                      f"deposit {_money(d['amount'])} references {p['payout_id']} for {_money(p['amount'])}")
            open_p.discard(p["payout_id"])
            open_d.discard(d["source"])

    # 2. Same amount, in the window, and the deposit names the processor; unique on both sides.
    def candidates_for_payout(pid):
        p = by_id[pid]
        return [x for x in open_d if dep[x]["amount"] == p["amount"] and s.in_window(dep[x]["date"], p["arrival"])
                and _mentions_processor(dep[x], s)]

    def candidates_for_deposit(src):
        d = dep[src]
        return [x for x in open_p if by_id[x]["amount"] == d["amount"] and s.in_window(d["date"], by_id[x]["arrival"])]

    for pid in sorted(open_p, key=lambda x: (by_id[x]["arrival"], x)):
        if pid not in open_p:
            continue
        cands = candidates_for_payout(pid)
        if len(cands) == 1 and candidates_for_deposit(cands[0]) == [pid]:
            d = dep[cands[0]]
            match("corroborated", [pid], cands, f"same amount, deposit {(d['date'] - by_id[pid]['arrival']).days:+d} days "
                                                 f"from arrival, deposit names the processor")

    # 3. Several consecutive payouts in one processor deposit.
    ordered = sorted(open_p, key=lambda x: (by_id[x]["arrival"], x))
    for src in sorted(open_d):
        d = dep[src]
        if not _mentions_processor(d, s):
            continue
        runs = []
        for i in range(len(ordered)):
            for n in range(2, s.combine_max + 1):
                run = ordered[i:i + n]
                if len(run) < n or any(r not in open_p for r in run):
                    break
                if all(s.in_window(d["date"], by_id[r]["arrival"]) for r in run) \
                        and sum(by_id[r]["amount"] for r in run) == d["amount"]:
                    runs.append(run)
        if len(runs) == 1:
            match("combined", runs[0], [src], f"{len(runs[0])} consecutive payouts sum to the deposit")
        elif len(runs) > 1:
            involved = sorted({r for run in runs for r in run})
            exception("AMBIGUOUS", involved, [src], f"{len(runs)} different runs of payouts sum to this deposit")
            open_d.discard(src)
            open_p.difference_update(involved)
            ordered = [r for r in ordered if r in open_p]

    # 4. Remaining candidates go to review, never matched.
    for pid in sorted(open_p, key=lambda x: (by_id[x]["arrival"], x)):
        p = by_id[pid]
        cands = sorted(x for x in open_d if dep[x]["amount"] == p["amount"] and s.in_window(dep[x]["date"], p["arrival"]))
        if cands:
            rivals = sorted({y for x in cands for y in candidates_for_deposit(x)})
            exception("AMBIGUOUS", rivals, cands,
                      "same amount within the window, but " + ("several records fit" if len(cands) > 1 or len(rivals) > 1
                                                               else "the deposit doesn't name the processor"))
            open_p.difference_update(rivals)
            open_d.difference_update(cands)

    # A deposit that fits a payout from before the period could be its first booking or a duplicate of a
    # booking in last period's books, which this run can't see: ask for confirmation instead of matching.
    for m in [m for m in res.matches if not set(m["payouts"]) <= current]:
        res.matches.remove(m)
        exception("EARLIER_PAYOUT", m["payouts"], m["deposits"], f"fits {', '.join(m['payouts'])} "
                  f"(arrived {min(by_id[x]['arrival'] for x in m['payouts'])}) by {m['rule'].replace('_', ' ')}")

    # Explain what's left.
    matched_payout_of = {d: m["payouts"] for m in res.matches for d in m["deposits"]}
    gross_by_payout = defaultdict(Decimal)
    for t in transactions or []:
        if t["category"] in ("charge", "payment"):
            gross_by_payout[t["payout_id"]] += t["gross"]
    for src in sorted(open_d):
        d = dep[src]
        # Exactly the amount of a payout that's already matched or in review, anywhere in the period:
        # a second booking of the same payout is likelier than a coincidence to the cent.
        twin = [(m["payouts"][0], m["deposits"][0], "matched to") for m in res.matches
                if len(m["payouts"]) == 1 and by_id[m["payouts"][0]]["amount"] == d["amount"]]
        twin += [(x, e["deposits"][0] if e["deposits"] else "", "in review with") for e in res.exceptions
                 if e["code"] in ("AMBIGUOUS", "EARLIER_PAYOUT") for x in e["payouts"] if by_id[x]["amount"] == d["amount"]]
        if not _mentions_processor(d, s):
            # Only an anonymous deposit could be an unlabelled second booking; one naming another
            # payer (Square, a customer) is other income even if its amount happens to match.
            if twin and not str(d.get("name") or "").strip():
                exception("POSSIBLE_DUPLICATE", [twin[0][0]], [src], f"same amount as {twin[0][0]}, already {twin[0][2]} {twin[0][1]}")
                open_d.discard(src)
            continue  # other income: out of scope, counted separately
        gross_hit = [p for p in sorted(open_p) if gross_by_payout.get(p) == d["amount"] and s.in_window(d["date"], by_id[p]["arrival"])]
        if len(gross_hit) == 1:
            exception("GROSS_BOOKED", gross_hit, [src],
                      f"deposit {_money(d['amount'])} equals the gross charges of {gross_hit[0]}, which paid out {_money(by_id[gross_hit[0]]['amount'])}")
            open_p.discard(gross_hit[0])
            open_d.discard(src)
            continue
        if twin:
            exception("POSSIBLE_DUPLICATE", [twin[0][0]], [src], f"same amount as {twin[0][0]}, already {twin[0][2]} {twin[0][1]}")
        else:
            exception("DEPOSIT_NO_PAYOUT", [], [src], f"processor deposit of {_money(d['amount'])} on {d['date']} matches no payout")
        open_d.discard(src)
    for pid in sorted(open_p & current, key=lambda x: (by_id[x]["arrival"], x)):
        p = by_id[pid]
        near_end = s.period_end is not None and p["arrival"] > s.period_end - timedelta(days=s.window_after)
        exception("PAYOUT_IN_TRANSIT" if near_end else "PAYOUT_NOT_DEPOSITED", [pid], [],
                  f"{_money(p['amount'])} arrived {p['arrival']}; no deposit found")
    open_p.clear()

    # Payout arithmetic: each payout equals the sum of its transactions' nets.
    if transactions is not None:
        nets = defaultdict(Decimal)
        seen = set()
        for t in transactions:
            if t["category"] == "payout":
                continue
            nets[t["payout_id"]] += t["net"]
            seen.add(t["payout_id"])
        for p in payouts:
            if p["payout_id"] not in current:
                continue
            if p["payout_id"] not in seen:
                res.exceptions.append({"code": "PAYOUT_ARITHMETIC", "payouts": [p["payout_id"]], "deposits": [],
                                       "amount": p["amount"], "detail": "no itemized transactions for this payout",
                                       "action": ACTIONS["PAYOUT_ARITHMETIC"], "arithmetic": True})
            elif nets[p["payout_id"]] != p["amount"]:
                res.exceptions.append({"code": "PAYOUT_ARITHMETIC", "payouts": [p["payout_id"]], "deposits": [],
                                       "amount": p["amount"], "arithmetic": True, "action": ACTIONS["PAYOUT_ARITHMETIC"],
                                       "detail": f"transactions net to {_money(nets[p['payout_id']])}, payout is {_money(p['amount'])} "
                                                 f"(difference {_money(p['amount'] - nets[p['payout_id']])})"})

    res.tieout = tie_out([p for p in payouts if p["payout_id"] in current], deposits, res, s)
    res.counts = {"payouts": len(current), "processor_deposits": sum(_mentions_processor(d, s) for d in deposits),
                  "other_deposits": sum(not _mentions_processor(d, s) for d in deposits),
                  "matched_payouts": sum(len(m["payouts"]) for m in res.matches),
                  "earlier_payouts_to_confirm": sum(len(e["payouts"]) for e in res.exceptions if e["code"] == "EARLIER_PAYOUT"),
                  "review": sum(len(e["payouts"]) for e in res.exceptions if e["code"] == "AMBIGUOUS")}
    return res


def tie_out(payouts, deposits, res, s):
    """Every payout lands in exactly one line; matched payouts and deposits agree to the cent."""
    dep = {d["source"]: d for d in deposits}
    line = defaultdict(Decimal)
    placed = set()
    ours = {p["payout_id"]: p for p in payouts}
    for m in res.matches:
        for p in m["payouts"]:
            if p in ours:
                line["matched to deposits"] += ours[p]["amount"]
                placed.add(p)
    for e in res.exceptions:
        if e.get("arithmetic"):
            continue
        for p in e["payouts"]:
            if p in placed or p not in ours:
                continue  # e.g. a duplicate deposit pointing at an already matched payout
            line[{"PAYOUT_IN_TRANSIT": "in transit at period end", "PAYOUT_NOT_DEPOSITED": "not recorded as a deposit",
                  "AMBIGUOUS": "awaiting review", "GROSS_BOOKED": "booked at gross instead of net",
                  "AMOUNT_MISMATCH": "deposit amount differs"}.get(e["code"], e["code"])] += \
                next(x["amount"] for x in payouts if x["payout_id"] == p)
            placed.add(p)
    total = sum((p["amount"] for p in payouts), Decimal(0))
    matched_deposits = sum((dep[x]["amount"] for m in res.matches for x in m["deposits"]), Decimal(0))
    matched_payouts = sum((m["amount"] for m in res.matches), Decimal(0))
    explained = sum(line.values(), Decimal(0))
    return {"payouts_total": total, "lines": dict(line), "unexplained": total - explained,
            "every_payout_placed_once": placed == set(ours),
            "matched_payouts_equal_matched_deposits": matched_deposits == matched_payouts}
