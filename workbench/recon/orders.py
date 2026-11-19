"""Reconcile exported store orders with processor charges and referenced refunds."""

from collections import defaultdict
from decimal import Decimal
from itertools import combinations
from pathlib import Path
import argparse
import csv
import json
import re

from ..common import WorkbenchError, csv_safe_text, file_hash
from .sources import load


PROFILES = {
    'shopify': ('shopify_orders', 'shopify_payments'),
    'woocommerce-stripe': ('woocommerce_orders', 'stripe_order_payments'),
}
MATCH_DAYS = 7
HANDLED = {'charge', 'payment', 'sale', 'capture', 'refund'}
DISPUTE = re.compile(r'dispute|chargeback', re.I)


def order_key(value):
    return (value or '').strip().removeprefix('#').casefold()


def signal(record, field):
    value = (record.get(field) or '').strip().casefold()
    return re.sub(r'\s+', '', value) if field == 'postcode' else value


def payment_amount(payment, order):
    currency = order['currency'].upper()
    if (payment.get('presentment_currency') or '').upper() == currency and payment.get('presentment_amount') is not None:
        return payment['presentment_amount']
    if payment['currency'].upper() == currency:
        return payment['amount']
    return None


def can_compare(payment, order):
    amount = payment_amount(payment, order)
    return (amount is not None and 0 < amount <= order['amount']
            and abs((payment['date'] - order['date']).days) <= MATCH_DAYS)


def same_customer(payment, order, field):
    value = signal(payment, field)
    return bool(value and value == signal(order, field))


def reference_tokens(value):
    return {part.strip() for part in (value or '').split('+') if part.strip()}


def reconcile(orders, payments, use_postcode=True):
    """Require a unique order and charge set before marking an automatic match."""
    by_order = {}
    exported_references = defaultdict(set)
    for order in orders:
        key = order_key(order['order_id'])
        if not key or key in by_order or order['amount'] < 0:
            raise WorkbenchError(f"{order['source']}: order ID must be unique and order amount nonnegative.")
        by_order[key] = order
        for ref in reference_tokens(order.get('references')):
            exported_references[ref].add(key)

    matches, refunds, exceptions = [], [], []
    order_status = {}
    charge_matches = {}

    def flag(code, keys=(), records=(), detail=''):
        exceptions.append({'code': code, 'orders': [by_order[k]['order_id'] for k in keys],
                           'transactions': [p['source'] for p in records], 'detail': detail})
        for key in keys:
            order_status.setdefault(key, 'review' if code != 'ORDER_UNPAID' else 'unpaid')

    def accept(key, charges, rule):
        order = by_order[key]
        matches.append({'order': order['order_id'], 'order_source': order['source'],
                        'transactions': [p['source'] for p in charges], 'rule': rule,
                        'amount': str(order['amount']), 'currency': order['currency'].upper()})
        order_status[key] = 'matched'
        for charge in charges:
            charge_matches[charge['source']] = key

    ids, unidentified = set(), set()
    explicit, fuzzy, reversed_items = defaultdict(list), [], []
    disputes, unhandled = [], defaultdict(list)
    for payment in payments:
        kind = payment['kind'].casefold()
        if kind not in HANDLED:
            # Nothing is dropped silently: disputes are tied to their orders below, and any other
            # transaction type is summarised so the export's full contents stay accounted for.
            (disputes.append(payment) if DISPUTE.search(kind) else unhandled[kind].append(payment))
            continue
        transaction_id = payment.get('transaction_id')
        signature = tuple(sorted((field, value) for field, value in payment.items() if field != 'source'))
        if (transaction_id and transaction_id in ids) or (not transaction_id and signature in unidentified):
            raise WorkbenchError(f"{payment['source']}: repeated transaction; provide distinguishable export rows.")
        if transaction_id:
            ids.add(transaction_id)
        else:
            unidentified.add(signature)
        if kind == 'refund':
            reversed_items.append(payment)
            continue
        if payment['amount'] <= 0:
            flag('CHARGE_NO_ORDER', records=[payment], detail='Charge amount must be positive.')
            continue
        direct = order_key(payment.get('order_id'))
        references = set().union(*(exported_references[ref] for ref in (
            transaction_id, payment.get('payment_reference')) if ref))
        if direct and direct not in by_order:
            flag('CHARGE_NO_ORDER', records=[payment], detail='Referenced order is absent from the export.')
            continue
        keys = references | ({direct} if direct else set())
        if len(keys) > 1:
            flag('AMBIGUOUS', sorted(keys), [payment], 'The transaction points to multiple orders.')
        elif keys:
            explicit[next(iter(keys))].append(payment)
        else:
            fuzzy.append(payment)

    for key, charges in explicit.items():
        if key in order_status:
            flag('AMBIGUOUS', [key], charges, 'This order already has conflicting references.')
            continue
        amounts = [payment_amount(p, by_order[key]) for p in charges]
        if len(charges) <= 2 and all(a is not None and a > 0 for a in amounts) and sum(amounts) == by_order[key]['amount']:
            accept(key, charges, 'order_reference' if len(charges) == 1 else 'referenced_split_tender')
        else:
            flag('AMOUNT_MISMATCH', [key], charges, 'Referenced charges do not settle the order total in its currency, or more than two charges need review.')

    owners = defaultdict(set)
    candidates = {}
    signals = ('email', 'postcode') if use_postcode else ('email',)
    by_customer = defaultdict(dict)
    for payment in fuzzy:
        for field in signals:
            if value := signal(payment, field):
                by_customer[field, value][payment['source']] = payment
    for key, order in by_order.items():
        if key in order_status or not order['amount']:
            continue
        nearby = {}
        for field in signals:
            nearby.update(by_customer.get((field, signal(order, field)), {}))
        eligible = [p for p in nearby.values() if can_compare(p, order)]
        exact = [(p,) for p in eligible if payment_amount(p, order) == order['amount']]
        split = [pair for pair in combinations([p for p in eligible if same_customer(p, order, 'email')
                 and payment_amount(p, order) < order['amount']], 2)
                 if sum(payment_amount(p, order) for p in pair) == order['amount']]
        candidates[key] = exact + split
        for group in candidates[key]:
            for payment in group:
                owners[payment['source']].add(key)

    reviewed_charges = set()
    for key, groups in candidates.items():
        if len(groups) == 1 and all(owners[p['source']] == {key} for p in groups[0]):
            group = groups[0]
            accept(key, group, 'corroborated' if len(group) == 1 else 'split_tender')
        elif groups:
            records = {p['source']: p for group in groups for p in group}
            reviewed_charges.update(records)
            flag('AMBIGUOUS', [key], records.values(), 'More than one order or charge combination fits.')
    open_orders = defaultdict(list)
    for key, order in by_order.items():
        if key not in order_status:
            open_orders[order['currency'].upper(), order['amount']].append(key)
    for payment in fuzzy:
        source = payment['source']
        if source in charge_matches or source in reviewed_charges:
            continue
        pairs = {(payment['currency'].upper(), payment['amount'])}
        if payment.get('presentment_currency'):
            pairs.add((payment['presentment_currency'].upper(), payment.get('presentment_amount')))
        plausible = sorted(key for pair in pairs for key in open_orders.get(pair, ())
                           if can_compare(payment, by_order[key])
                           and payment_amount(payment, by_order[key]) == by_order[key]['amount'])
        if plausible:
            flag('AMBIGUOUS', plausible, [payment], 'Amount and date fit but customer evidence is missing or differs.')
        else:
            flag('CHARGE_NO_ORDER', records=[payment], detail='No supported order match.')
    for key, order in by_order.items():
        if key not in order_status:
            if order['amount']:
                flag('ORDER_UNPAID', [key], detail='No supported charge appeared in the supplied exports.')
            else:
                order_status[key] = 'no_payment_required'

    charge_refs = defaultdict(list)
    for payment in payments:
        if payment['source'] in charge_matches:
            for ref in {payment.get('transaction_id'), payment.get('payment_reference')} - {None, ''}:
                charge_refs[ref].append(payment)
    refunded = defaultdict(Decimal)
    for payment in reversed_items:
        ref = payment.get('parent_id') or payment.get('payment_reference')
        parents = charge_refs.get(ref, [])
        if len(parents) != 1:
            flag('REFUND_NO_CHARGE', records=[payment], detail='An explicit original charge reference is missing or ambiguous.')
            continue
        parent = parents[0]
        key = charge_matches[parent['source']]
        order = by_order[key]
        amount = payment_amount(payment, order)
        if (amount in (None, 0) or (payment.get('order_id') and order_key(payment['order_id']) != key)
                or refunded[parent['source']] + abs(amount) > payment_amount(parent, order)):
            flag('REFUND_MISMATCH', [key], [payment], 'Refund conflicts with the referenced charge, order, or currency.')
            continue
        refunded[parent['source']] += abs(amount)
        refunds.append({'order': order['order_id'], 'transaction': payment['source'], 'charge': parent['source'],
                        'amount': str(abs(amount)), 'currency': order['currency'].upper()})

    disputed = set()
    for payment in disputes:
        direct = order_key(payment.get('order_id'))
        parents = charge_refs.get(payment.get('parent_id') or payment.get('payment_reference'), [])
        key = direct if direct in by_order else charge_matches[parents[0]['source']] if len(parents) == 1 else None
        amount = f"{payment['amount']} {payment['currency'].upper()}"
        if key:
            disputed.add(key)
            exceptions.append({'code': 'DISPUTE', 'orders': [by_order[key]['order_id']], 'transactions': [payment['source']],
                               'detail': f"{payment['kind']} of {amount} against this order's payment. The order stays "
                                         'matched, but the money may be reversed: check the dispute outcome.'})
        else:
            exceptions.append({'code': 'DISPUTE', 'orders': [], 'transactions': [payment['source']],
                               'detail': f"{payment['kind']} of {amount} that can't be traced to a matched order's charge."})
    for kind, items in sorted(unhandled.items()):
        totals = defaultdict(Decimal)
        for item in items:
            totals[item['currency'].upper()] += item['amount']
        exceptions.append({'code': 'UNHANDLED_TRANSACTIONS', 'orders': [], 'transactions': [p['source'] for p in items],
                           'detail': f"{len(items)} '{kind}' row(s) totalling "
                                     + ', '.join(f"{v} {c}" for c, v in sorted(totals.items()))
                                     + " aren't order payments, refunds or disputes, so they were not matched to orders."})

    return {'matches': matches, 'refunds': refunds, 'exceptions': exceptions,
            'orders': [{'order': o['order_id'], 'source': o['source'], 'status': order_status[k],
                        **({'disputed': True} if k in disputed else {})}
                       for k, o in by_order.items()]}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, prog='python -m workbench.recon orders')
    parser.add_argument('--platform', choices=PROFILES, default='shopify')
    parser.add_argument('--orders', type=Path, required=True)
    parser.add_argument('--transactions', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--match-postcode', action='store_true', help='Use postcode only when both files use the same address convention.')
    args = parser.parse_args(argv)
    order_profile, payment_profile = PROFILES[args.platform]
    orders = load(args.orders, order_profile)
    payments = load(args.transactions, payment_profile)
    result = reconcile(orders.records, payments.records, args.match_postcode)
    result['inputs'] = {str(path): file_hash(path) for path in (args.orders, args.transactions)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    for name, fields in {
        'matches': ['order', 'order_source', 'transactions', 'rule', 'amount', 'currency'],
        'refunds': ['order', 'transaction', 'charge', 'amount', 'currency'],
        'exceptions': ['code', 'orders', 'transactions', 'detail'],
    }.items():
        with (args.out / f'{name}.csv').open('w', encoding='utf-8', newline='') as file:
            writer = csv.DictWriter(file, fields)
            writer.writeheader()
            writer.writerows({field: csv_safe_text(' | '.join(row[field]) if isinstance(row[field], list) else row[field])
                              for field in fields} for row in result[name])
    statuses = [o['status'] for o in result['orders']]
    print(f"{len(statuses)} orders. {statuses.count('matched')} matched. "
          f"{statuses.count('review')} in review. {statuses.count('unpaid')} without a charge. "
          f"{sum(o.get('disputed', False) for o in result['orders'])} disputed. "
          f"{len(result['exceptions'])} exceptions.")
    return 0
