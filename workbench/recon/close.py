"""Evidence carried between adjacent payout closes."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json

from ..common import WorkbenchError, file_hash, write_json


def account_currency(payouts, itemized):
    currencies = {record['currency'].upper() for record in [*payouts, *(itemized or [])]}
    if len(currencies) != 1:
        raise WorkbenchError('A saved close requires a single currency across the payout and itemized exports.')
    return next(iter(currencies))


def read_prior(path, account, currency, settings):
    try:
        content = json.loads(path.read_text(encoding='utf-8'))
        previous_month = (settings.period_start - timedelta(days=1)).strftime('%Y-%m')
        if (content['version'] != 1 or content['account'] != account or content['currency'] != currency
                or content['period'] != previous_month or not isinstance(content['payouts'], dict)):
            raise ValueError('Use the preceding month’s close for the same bank account and currency.')
        for value in content['payouts'].values():
            if value['state'] not in {'booked', 'open', 'review'}:
                raise ValueError('Unknown payout state.')
            amount = Decimal(value['amount'])
            if not amount.is_finite() or amount <= 0 or date.fromisoformat(value['arrival']) >= settings.period_start:
                raise ValueError('Invalid payout amount or arrival date.')
    except (KeyError, TypeError, ValueError, InvalidOperation, OSError) as exc:
        raise WorkbenchError(f'Cannot use the prior close {path.name}: {exc}') from exc
    return content['payouts']


def prior_state(prior, payout):
    previous = prior.get(payout['payout_id'])
    if previous and Decimal(previous['amount']) == payout['amount'] and previous['arrival'] == payout['arrival'].isoformat():
        return previous['state']
    return None


def save_close(path, account, currency, settings, payouts, result, prior, inputs):
    booked = {pid for match in result.matches for pid in match['payouts']}
    open_payouts = {pid for exception in result.exceptions
                    if exception['code'] in ('PAYOUT_NOT_DEPOSITED', 'PAYOUT_IN_TRANSIT')
                    for pid in exception['payouts']}
    saved = {pid: state for pid, state in prior.items() if state['state'] != 'booked'}
    for payout in payouts:
        pid, arrival = payout['payout_id'], payout['arrival']
        if not settings.period_start <= arrival <= settings.period_end and pid not in booked:
            continue
        state = 'booked' if pid in booked else ('open' if pid in open_payouts else 'review')
        saved[pid] = {'state': state, 'arrival': arrival.isoformat(), 'amount': str(payout['amount'])}
    write_json(path, {'version': 1, 'account': account, 'currency': currency,
                      'period': settings.period_start.strftime('%Y-%m'), 'payouts': saved,
                      'input_sha256': {str(source): file_hash(source) for source in inputs}})
