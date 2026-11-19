"""Phase 1 reconciliation: Stripe payouts against QuickBooks deposits, and payout arithmetic."""
from datetime import date
from decimal import Decimal as D
import json

import pytest

from workbench.common import WorkbenchError
from workbench.recon import match, orders, sources, synth
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


def test_order_reconciliation_never_guesses_across_seeded_collisions():
    for seed in range(150):
        sales, payments, truth = synth.order_trial(seed)
        result = orders.reconcile(sales, payments, use_postcode=False)
        assert {m['order']: set(m['transactions']) for m in result['matches']} == truth['matches'], seed
        assert {m['transaction']: m['charge'] for m in result['refunds']} == truth['refunds'], seed
        assert {m['order']: m['status'] for m in result['orders']} == truth['statuses'], seed
        assert {m['code'] for m in result['exceptions']} == truth['exceptions'], seed


@pytest.mark.parametrize('platform,orders_csv,charges_csv,result', [
    ('shopify',
     'Name,Created at,Total,Currency,Lineitem name,Payment References\n'
     '#1001,2026-10-01 11:00:00 -0400,21.50,CAD,Item one,\n'
     '#1001,,,,Item two,\n'
     ',,,,Item three,\n',
     'Type,Transaction Date,Amount,Fee,Net,Currency,Order,Presentment Amount,Presentment Currency\n'
     'charge,2026-10-01 11:00:00 -0400,15.90,0.70,15.20,USD,#1001,21.50,CAD\n'
     'refund,2026-10-03 11:00:00 -0400,-4.00,0.00,-4.00,USD,#1001,-5.41,CAD\n',
     {'order': '#1001', 'amount': '21.50', 'currency': 'CAD'}),
    ('woocommerce-stripe',
     'order_number,order_date,order_total,order_currency,billing_email\n'
     '512,2026-10-01 11:00:00,51.00,USD,buyer@example.test\n',
     'reporting_category,created_utc,gross,currency,balance_transaction_id,charge_id,payment_metadata[order_id]\n'
     'charge,2026-10-01 15:00:00,51.00,USD,txn_charge,ch_512,512\n'
     'refund,2026-10-03 15:00:00,-11.00,USD,txn_refund,ch_512,\n',
     {'order': '512', 'amount': '51.00', 'currency': 'USD'}),
])
def test_order_cli_supported_profile_pairs(tmp_path, platform, orders_csv, charges_csv, result):
    order_csv, charge_csv = tmp_path / 'orders.csv', tmp_path / 'charges.csv'
    order_csv.write_text(orders_csv, encoding='utf-8')
    charge_csv.write_text(charges_csv, encoding='utf-8')
    out = tmp_path / 'out'
    assert cli(['orders', '--platform', platform, '--orders', str(order_csv),
                '--transactions', str(charge_csv), '--out', str(out)]) == 0
    report = json.loads((out / 'result.json').read_text(encoding='utf-8'))
    assert len(report['orders']) == len(report['matches']) == 1
    assert all(report['matches'][0][key] == value for key, value in result.items())
    if platform == 'shopify':
        assert {e['code'] for e in report['exceptions']} == {'REFUND_NO_CHARGE'}
    else:
        assert report['refunds'][0]['amount'] == '11.00'


def test_saved_close_distinguishes_previously_booked_and_open_payouts(tmp_path):
    payout_csv = tmp_path / 'payouts.csv'
    books_csv = tmp_path / 'books.csv'
    payout_csv.write_text(
        'id,Amount,Arrival Date,Currency\npo_OPENAAAA,100.00,2026-09-29,USD\n'
        'po_BOOKAAAA,95.00,2026-09-30,USD\n', encoding='utf-8')
    books_csv.write_text('Date,Transaction Type,Name,Memo,Amount\n'
                         '09/30/2026,Deposit,Stripe,po_BOOKAAAA,95.00\n', encoding='utf-8')

    def close_month(period, folder, account='Operating USD', prior=None):
        return cli(['--payouts', str(payout_csv), '--books', str(books_csv), '--period', period,
                    '--out', str(folder), '--close-account', account]
                   + (['--prior-close', str(prior)] if prior else []))

    september = tmp_path / 'sept'
    assert close_month('2026-09', september) == 0
    state = september / 'close-state.json'
    data = json.loads(state.read_text(encoding='utf-8'))
    assert {key: value['state'] for key, value in data['payouts'].items()} == {
        'po_OPENAAAA': 'open', 'po_BOOKAAAA': 'booked'}
    books_csv.write_text('Date,Transaction Type,Name,Memo,Amount\n'
                         '10/01/2026,Deposit,Stripe,po_BOOKAAAA,95.00\n'
                         '10/02/2026,Deposit,Stripe,po_OPENAAAA,100.00\n', encoding='utf-8')
    october = tmp_path / 'oct'
    assert close_month('2026-10', october, prior=state) == 0
    report = json.loads((october / 'result.json').read_text(encoding='utf-8'))
    assert [m['payouts'] for m in report['matches']] == [['po_OPENAAAA']]
    assert 'PRIOR_PERIOD_BOOKED' in {e['code'] for e in report['exceptions']}
    assert report['counts']['settled_from_prior_period'] == 1
    assert close_month('2026-10', tmp_path / 'wrong', account='Other account', prior=state) == 2


def _close(tmp_path, folder, period, payouts, books, prior=None):
    payout_csv, books_csv = tmp_path / f'{folder}-payouts.csv', tmp_path / f'{folder}-books.csv'
    payout_csv.write_text('id,Amount,Arrival Date,Currency\n' + payouts, encoding='utf-8')
    books_csv.write_text('Date,Transaction Type,Name,Memo,Amount\n' + books, encoding='utf-8')
    out = tmp_path / folder
    code = cli(['--payouts', str(payout_csv), '--books', str(books_csv), '--period', period, '--out', str(out),
                '--close-account', 'Operating USD'] + (['--prior-close', str(prior)] if prior else []))
    report = json.loads((out / 'result.json').read_text(encoding='utf-8'))
    saved = json.loads((out / 'close-state.json').read_text(encoding='utf-8')) if (out / 'close-state.json').exists() else None
    return code, report, saved, out


@pytest.mark.parametrize('still_exported', [True, False], ids=['in-export', 'only-in-close'])
def test_an_undeposited_payout_stays_visible_until_it_is_settled(tmp_path, still_exported):
    lost = 'po_LOSTAAAA,250.00,2026-09-29,USD\n'
    _, sept, sept_state, _ = _close(tmp_path, 'sep', '2026-09', lost, '09/15/2026,Deposit,Customer check,,40.00\n')
    assert [e['code'] for e in sept['exceptions']] == ['PAYOUT_IN_TRANSIT']
    code, octo, oct_state, out = _close(tmp_path, 'oct', '2026-10', (lost if still_exported else '') + 'po_OCTBAAAA,80.00,2026-10-06,USD\n',
                                        '10/07/2026,Deposit,Stripe,po_OCTBAAAA,80.00\n', tmp_path / 'sep' / 'close-state.json')
    assert code == 0
    assert [(e['code'], e['payouts']) for e in octo['exceptions']] == [('PAYOUT_STILL_OPEN', ['po_LOSTAAAA'])]
    assert octo['counts']['still_open_from_prior'] == 1 and D(octo['tieout']['still_open_from_prior_total']) == D('250.00')
    assert 'still not deposited, totalling $250.00' in (out / 'summary.md').read_text(encoding='utf-8')
    assert oct_state['payouts']['po_LOSTAAAA']['state'] == 'open'
    # Five weeks late, the deposit arrives and settles it.
    _, nov, nov_state, _ = _close(tmp_path, 'nov', '2026-11', 'po_NOVAAAAA,10.00,2026-11-02,USD\n',
                                  '11/03/2026,Deposit,Stripe,po_LOSTAAAA,250.00\n11/03/2026,Deposit,Stripe,po_NOVAAAAA,10.00\n',
                                  tmp_path / 'oct' / 'close-state.json')
    assert sorted(m['payouts'][0] for m in nov['matches']) == ['po_LOSTAAAA', 'po_NOVAAAAA'] and nov['exceptions'] == []
    assert nov_state['payouts']['po_LOSTAAAA']['state'] == 'booked'


def test_a_payout_export_that_disagrees_with_the_prior_close_is_flagged(tmp_path):
    _close(tmp_path, 'sep', '2026-09', 'po_LOSTAAAA,250.00,2026-09-29,USD\n', '09/15/2026,Deposit,Customer check,,40.00\n')
    _, octo, _, _ = _close(tmp_path, 'oct', '2026-10', 'po_LOSTAAAA,205.00,2026-09-29,USD\n', '10/12/2026,Deposit,Customer check,,5.00\n',
                           tmp_path / 'sep' / 'close-state.json')
    assert [(e['code'], e['payouts']) for e in octo['exceptions']] == [('PRIOR_CLOSE_MISMATCH', ['po_LOSTAAAA'])]
    assert '$250.00' in octo['exceptions'][0]['detail'] and '$205.00' in octo['exceptions'][0]['detail']


# --- Phase 2 rules ------------------------------------------------------------------------

def _order(name, amount='50.00', day=5, **evidence):
    return {'order_id': name, 'source': f'orders:{name}', 'date': date(2026, 10, day), 'amount': D(amount),
            'currency': 'USD', **evidence}


def _txn(n, kind='charge', amount='50.00', day=5, **fields):
    return {'source': f'txns:{n}', 'transaction_id': f'txn_{n}', 'kind': kind, 'amount': D(amount),
            'currency': 'USD', 'date': date(2026, 10, day), **fields}


def test_a_dispute_is_tied_to_the_order_whose_payment_it_reverses():
    result = orders.reconcile([_order('#1001')], [_txn(1, order_id='#1001'), _txn(2, 'dispute', '-50.00', 12, payment_reference='txn_1')])
    assert [m['order'] for m in result['matches']] == ['#1001']
    assert [(e['code'], e['orders']) for e in result['exceptions']] == [('DISPUTE', ['#1001'])]
    assert result['orders'][0] == {'order': '#1001', 'source': 'orders:#1001', 'status': 'matched', 'disputed': True}


def test_a_chargeback_naming_its_order_and_an_untraceable_dispute_are_both_reported():
    result = orders.reconcile([_order('#1002')], [_txn(1, order_id='#1002'), _txn(2, 'chargeback', '-50.00', order_id='#1002'),
                                                 _txn(3, 'dispute', '-20.00', payment_reference='ch_unknown')])
    assert sorted((e['code'], tuple(e['orders'])) for e in result['exceptions']) == [('DISPUTE', ()), ('DISPUTE', ('#1002',))]


def test_other_transaction_types_are_summarised_not_dropped():
    result = orders.reconcile([_order('#1003')], [_txn(1, order_id='#1003'), _txn(2, 'fee', '-1.75'), _txn(3, 'fee', '-0.30'),
                                                 _txn(4, 'payout', '-48.00')])
    summary = {e['detail'].split("'")[1]: e for e in result['exceptions'] if e['code'] == 'UNHANDLED_TRANSACTIONS'}
    assert set(summary) == {'fee', 'payout'}
    assert len(summary['fee']['transactions']) == 2 and '-2.05 USD' in summary['fee']['detail']


def test_postcode_evidence_is_used_only_when_switched_on():
    sales = [_order('#2001', postcode='K1A 0B1')]
    charge = [_txn(1, postcode='k1a0b1')]
    assert [m['rule'] for m in orders.reconcile(sales, charge, use_postcode=True)['matches']] == ['corroborated']
    off = orders.reconcile(sales, charge, use_postcode=False)
    assert off['matches'] == [] and {e['code'] for e in off['exceptions']} == {'AMBIGUOUS'}


def test_a_referenced_charge_in_another_currency_is_not_matched():
    result = orders.reconcile([_order('#3001')], [{**_txn(1, order_id='#3001'), 'currency': 'CAD'}])
    assert result['matches'] == [] and [e['code'] for e in result['exceptions']] == ['AMOUNT_MISMATCH']


def test_refunds_cannot_exceed_their_charge():
    result = orders.reconcile([_order('#4001')], [_txn(1, order_id='#4001'), _txn(2, 'refund', '-30.00', 9, parent_id='txn_1'),
                                                 _txn(3, 'refund', '-30.00', 10, parent_id='txn_1')])
    assert [r['transaction'] for r in result['refunds']] == ['txns:2']
    assert [(e['code'], e['transactions']) for e in result['exceptions']] == [('REFUND_MISMATCH', ['txns:3'])]
