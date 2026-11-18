"""Phase 1 reconciliation: Stripe payouts against QuickBooks deposits, and payout arithmetic."""
from datetime import date
from decimal import Decimal as D

import pytest

from workbench.common import WorkbenchError
from workbench.recon import match, sources, synth
from workbench.recon.__main__ import main as cli

OCT = match.Settings(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31))
HOSTILE = {"unbooked": 0.15, "gross_booked": 0.1, "combined": 0.15, "duplicate": 0.1, "typo_with_id": 0.05,
           "no_processor_name": 0.15, "itemized_missing_row": 0.1, "other_income": 12, "collision_share": 0.8}


def payout(pid, amount, day):
    return {"payout_id": pid, "amount": D(amount), "arrival": date(2026, 10, day)}


def deposit(row, amount, day, name="Stripe", memo="STRIPE TRANSFER"):
    return {"source": f"books:{row}", "amount": D(amount), "date": date(2026, 10, day), "name": name, "memo": memo}


def codes(result):
    return sorted(e["code"] for e in result.exceptions)


# --- Rules -------------------------------------------------------------------------------

def test_payout_id_in_the_memo_is_an_exact_match():
    r = match.reconcile([payout("po_AAAAAAAA", "100.00", 6)], [deposit(1, "100.00", 9, memo="Stripe po_AAAAAAAA")], settings=OCT)
    assert [m["rule"] for m in r.matches] == ["exact_id"] and codes(r) == []


def test_same_amount_named_processor_in_window_is_corroborated():
    r = match.reconcile([payout("po_AAAAAAAA", "512.37", 6)], [deposit(1, "512.37", 7)], settings=OCT)
    assert [m["rule"] for m in r.matches] == ["corroborated"]


def test_a_deposit_that_does_not_name_the_processor_goes_to_review():
    r = match.reconcile([payout("po_AAAAAAAA", "512.37", 6)], [deposit(1, "512.37", 7, name="", memo="DEPOSIT 88121")], settings=OCT)
    assert r.matches == [] and codes(r) == ["AMBIGUOUS"]


def test_two_payouts_with_the_same_amount_are_never_guessed():
    r = match.reconcile([payout("po_AAAAAAAA", "250.00", 6), payout("po_BBBBBBBB", "250.00", 7)],
                        [deposit(1, "250.00", 8)], settings=OCT)
    assert r.matches == [] and codes(r) == ["AMBIGUOUS"]
    assert r.tieout["every_payout_placed_once"]


def test_consecutive_payouts_combined_into_one_deposit():
    r = match.reconcile([payout("po_AAAAAAAA", "100.10", 5), payout("po_BBBBBBBB", "200.20", 6)],
                        [deposit(1, "300.30", 7)], settings=OCT)
    assert [(m["rule"], m["payouts"]) for m in r.matches] == [("combined", ["po_AAAAAAAA", "po_BBBBBBBB"])]


def test_outside_the_date_window_is_not_matched():
    r = match.reconcile([payout("po_AAAAAAAA", "512.37", 6)], [deposit(1, "512.37", 20)], settings=OCT)
    assert r.matches == [] and codes(r) == ["DEPOSIT_NO_PAYOUT", "PAYOUT_NOT_DEPOSITED"]


# --- Exceptions --------------------------------------------------------------------------

def test_deposit_booked_at_gross_is_recognised():
    txns = [{"payout_id": "po_AAAAAAAA", "category": "charge", "gross": D("100.00"), "fee": D("-3.20"), "net": D("96.80")},
            {"payout_id": "po_AAAAAAAA", "category": "charge", "gross": D("50.00"), "fee": D("-1.75"), "net": D("48.25")}]
    r = match.reconcile([payout("po_AAAAAAAA", "145.05", 6)], [deposit(1, "150.00", 7)], txns, settings=OCT)
    assert codes(r) == ["GROSS_BOOKED"]


def test_a_mistyped_amount_on_a_deposit_citing_its_payout():
    r = match.reconcile([payout("po_AAAAAAAA", "100.00", 6)], [deposit(1, "109.00", 7, memo="po_AAAAAAAA")], settings=OCT)
    assert codes(r) == ["AMOUNT_MISMATCH"] and r.matches == []


def test_two_deposits_fitting_one_payout_both_go_to_review():
    r = match.reconcile([payout("po_AAAAAAAA", "512.37", 6)], [deposit(1, "512.37", 7), deposit(2, "512.37", 9)], settings=OCT)
    assert r.matches == [] and [(e["code"], e["deposits"]) for e in r.exceptions] == [("AMBIGUOUS", ["books:1", "books:2"])]


def test_a_second_booking_of_a_matched_payout_is_a_possible_duplicate():
    r = match.reconcile([payout("po_AAAAAAAA", "512.37", 6)],
                        [deposit(1, "512.37", 7, memo="Stripe po_AAAAAAAA"), deposit(2, "512.37", 20)], settings=OCT)
    assert [m["rule"] for m in r.matches] == ["exact_id"] and codes(r) == ["POSSIBLE_DUPLICATE"]
    assert r.tieout["unexplained"] == 0


def test_a_payout_from_before_the_period_needs_confirmation_not_a_match():
    early = {"payout_id": "po_EARLIER1", "amount": D("700.00"), "arrival": date(2026, 9, 30)}
    r = match.reconcile([early], [deposit(1, "700.00", 1)], settings=OCT)
    assert r.matches == [] and codes(r) == ["EARLIER_PAYOUT"]
    assert r.tieout["payouts_total"] == 0  # it belongs to September's total


def test_unbooked_payouts_near_the_period_end_are_in_transit():
    r = match.reconcile([payout("po_AAAAAAAA", "10.00", 12), payout("po_BBBBBBBB", "20.00", 29)], [], settings=OCT)
    assert codes(r) == ["PAYOUT_IN_TRANSIT", "PAYOUT_NOT_DEPOSITED"]
    assert r.tieout["lines"] == {"not recorded as a deposit": D("10.00"), "in transit at period end": D("20.00")}


def test_payout_arithmetic():
    txns = [{"payout_id": "po_AAAAAAAA", "category": "charge", "gross": D("100.00"), "fee": D("-3.20"), "net": D("96.80")}]
    r = match.reconcile([payout("po_AAAAAAAA", "96.80", 6), payout("po_BBBBBBBB", "50.00", 6)], [], txns, settings=OCT)
    arithmetic = [e for e in r.exceptions if e["code"] == "PAYOUT_ARITHMETIC"]
    assert [e["payouts"] for e in arithmetic] == [["po_BBBBBBBB"]]


# --- Loading exports ---------------------------------------------------------------------

def test_quickbooks_report_layout_and_money_formats(tmp_path):
    path = tmp_path / "qb.csv"
    path.write_text("Acme (fictional)\nTransaction Detail by Account\n\n"
                    ",Date,Transaction Type,Num,Name,Memo/Description,Split,Amount,Balance\n"
                    "Checking\n"
                    ',10/02/2026,Deposit,,Stripe,STRIPE TRANSFER,Undeposited Funds,"1,234.56","1,234.56"\n'
                    ",10/03/2026,Expense,1001,Gusto,,-Split-,(250.00),984.56\n"
                    ',Total for Checking,,,,,,"984.56",\n', encoding="utf-8")
    loaded = sources.load(path, "quickbooks_transaction_detail")
    assert loaded.header_row == 4 and loaded.excluded == 1
    assert loaded.records[0]["amount"] == D("1234.56") and loaded.records[0]["date"] == date(2026, 10, 2)
    assert sources.parse_money("(250.00)") == D("-250.00") and sources.parse_money("$1,000") == D("1000")


def test_a_missing_required_column_names_what_is_missing(tmp_path):
    path = tmp_path / "payouts.csv"
    path.write_text("id,Currency,Arrival Date\npo_1,usd,2026-10-01\n", encoding="utf-8")
    with pytest.raises(WorkbenchError, match="amount"):
        sources.load(path, "stripe_payouts")


# --- Synthetic stores with known truth ---------------------------------------------------

def evaluate(seeds, rates=None, tmp_path=None):
    totals = {}
    for seed in seeds:
        store = synth.generate(seed=seed, rates=rates)
        pay, item, qb = synth.write(store, tmp_path / str(seed))
        result = match.reconcile(sources.load(pay, "stripe_payouts").records, sources.load(qb, "quickbooks_transaction_detail").records,
                                 sources.load(item, "stripe_itemized_payouts").records,
                                 match.Settings(period_start=store.period_start, period_end=store.period_end))
        t = result.tieout
        assert t["unexplained"] == 0 and t["every_payout_placed_once"] and t["matched_payouts_equal_matched_deposits"], seed
        score = synth.score(store, result)
        assert score["faults_missed"] == [], (seed, score["faults_missed"])
        for k, v in score.items():
            if isinstance(v, int):
                totals[k] = totals.get(k, 0) + v
    return totals


@pytest.mark.parametrize("rates,seeds", [(None, range(40)), (HOSTILE, range(1000, 1020))], ids=["normal", "hostile"])
def test_no_wrong_matches_and_every_fault_surfaces(tmp_path, rates, seeds):
    t = evaluate(seeds, rates, tmp_path)
    assert t["wrong_matches"] == 0
    assert t["faults_found"] + t["faults_in_review"] + t["faults_other_code"] == t["faults"] > 0
    assert t["arithmetic_found"] == t["arithmetic_expected"] and t["arithmetic_false"] == 0
    assert t["duplicates_flagged"] == t["duplicates"] and t["false_duplicate_flags"] == 0


def test_command_line_writes_the_report(tmp_path):
    store = synth.generate(seed=3)
    pay, item, qb = synth.write(store, tmp_path / "in")
    code = cli(["--payouts", str(pay), "--itemized", str(item), "--books", str(qb), "--period", "2026-10", "--out", str(tmp_path / "out")])
    assert code == 0
    summary = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "**Unexplained** | **$0.00**" in summary
    assert {p.name for p in (tmp_path / "out").iterdir()} == {"summary.md", "matches.csv", "exceptions.csv", "result.json"}
