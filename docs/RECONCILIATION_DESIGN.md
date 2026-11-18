# Design: payment reconciliation for small businesses

**Status:** proposal · **Builds on:** the Data Workbench engine (ingest, cleaning rules, review queue, audit, hash manifest)

## Summary

Small online businesses take orders in one system (Shopify), get paid through a processor (Stripe or Shopify Payments), and keep their books in another (QuickBooks). The three rarely agree, and the person closing the month reconciles them by hand with spreadsheets and lookups. This design adds a reconciliation layer to Data Workbench that matches records across the three, proves that every dollar is accounted for, and lists every exception with a reason, without ever guessing a match.

The central rule comes from measurement on real data (§5.2): **a match needs corroborating evidence beyond the amount, and a tie goes to a person, never to the first candidate.** On 98,285 real payments, matching on amount and date alone picked a single wrong order 1.40% of the time, silently. Adding one independent signal removed every wrong match.

## 1. Who it's for and what they need

- **The user:** a bookkeeper, operations lead or freelance analyst (the Upwork client's contractor) closing a month for a store doing tens to a few thousand orders a month.
- **What they ask:** "Why doesn't the money in the bank match sales?" "Which orders were never paid, or paid twice?" "Which payouts never reached QuickBooks?" "What's still in transit at month end?"
- **What they need from the tool:** a tie-out that explains the whole difference, a short exception list with a suggested action for each, and an audit trail they can hand to an accountant.

### Goals
- Match orders to processor transactions, transactions to payouts, and payouts to bank deposits recorded in the books.
- Explain the full difference between sales and deposits for a period as a tie-out statement whose reconciling items sum to zero.
- Never auto-match on weak evidence; measure match correctness on real data before shipping a rule.
- Run locally on exported files, like the rest of Data Workbench; no connection to anyone's live accounts.

### Non-goals (for now)
- Posting entries to QuickBooks or syncing continuously. SaaS connectors already do that; this tool is for cleanup, audits and one-off reconciliations of messy history.
- Multi-currency conversion, sales-tax calculation, inventory or cost of goods.
- Live API connections. Files only, which keeps credentials out of the tool.

## 2. How the money moves

```
 Shopify order ──(1)──▶ processor charge ──(2)──▶ payout ──(3)──▶ bank deposit in QuickBooks
   gross amount         gross, fee, net           sum of nets       one deposit per payout
                        refunds, disputes          on a date         (or several combined)
```

Each arrow is a **leg**, reconciled separately and then chained:

| Leg | Left side | Right side | Usual join | Typical breaks |
|---|---|---|---|---|
| 1. Order ↔ charge | Paid orders | Charges and refunds | Order reference in the charge's description or metadata; otherwise amount + date + customer | Unpaid "paid" orders, charges with no order, partial refunds, split tenders, test orders |
| 2. Charge ↔ payout | Processor transactions | Payouts | Payout ID on each transaction | Transactions not yet paid out (in transit), fees and adjustments, reserves, disputes |
| 3. Payout ↔ deposit | Payouts | Deposits in the books | Amount + arrival date; payout ID if the bookkeeper recorded it in the memo | Payouts never booked, deposits combining several payouts, gross booked instead of net |

Leg 2 is mostly arithmetic once the processor's own payout link is present: a payout must equal the sum of its transactions' nets to the cent. Legs 1 and 3 are where matching judgment is needed.

## 3. Sources and the canonical model

### 3.1 Source profiles are data, not code

Each supported export gets a **profile**: a versioned JSON file that maps its columns to canonical fields, sets types, date formats, sign conventions and time zone, and lists known quirks. Export formats change and vary by account settings, so profiles must be easy to inspect, edit and add without touching the engine. They reuse the Workbench recipe format (mappings, types, missing markers, header-row detection).

| Profile | What the export looks like | Quirks the profile handles |
|---|---|---|
| Shopify orders | One row per line item; later rows of the same order leave order-level fields blank ([Shopify help](https://help.shopify.com/en/manual/orders/manage-orders/exporting-orders)) | Collapse to one row per order before matching, or totals double count |
| Shopify Payments transactions | One row per balance transaction with amount, fee and net, and payout date ([Shopify help](https://help.Shopify.com/en/manual/payments/shopify-payments/getting-paid-with-shopify-payments/view-payouts/view-details)) | Columns vary by export; the profile must be confirmed against a client sample |
| Stripe itemized payout reconciliation | One row per balance transaction, with `balance_transaction_id`, `reporting_category` and `automatic_payout_id` linking it to its payout ([Stripe docs](https://docs.stripe.com/reports/payout-reconciliation)) | Only for automatic payouts; columns are customizable, so required columns are validated on import |
| QuickBooks transaction detail | Report export with title rows above the header; deposits, fees and refunds as journal lines | Header-row detection (already in Workbench); sign conventions per account |

Column names in the table above are taken from the cited documentation where it gives them. Every profile ships with a sample header and a validation check that fails clearly when a client's export differs, rather than mapping the wrong column.

### 3.2 Canonical records

| Record | Key fields |
|---|---|
| `order` | order ref, created at (UTC), customer (email, postcode), gross, currency, financial status, source row |
| `transaction` | processor txn id, kind (charge, refund, dispute, fee, adjustment, payout), created at, gross, fee, net, order ref if present, payout id, customer evidence |
| `payout` | payout id, arrival date, amount, status |
| `deposit` | ledger line id, date, amount, account, memo |

Every canonical record keeps its source file, row and raw values, as Workbench records already do, so any match can be traced back to the exact cells.

**Money:** exact decimals in the account currency, never floats. Sign conventions are normalised on import (refunds negative, fees negative). **Time:** stored in UTC, with the store's time zone in the profile, because a 9pm Pacific order is the next day in Stripe's UTC.

## 4. Pipeline

1. **Ingest and clean** each export with its profile, using the existing Workbench engine. Bad rows go to the existing review queue.
2. **Normalise** to canonical records (collapse Shopify line items into orders, split Stripe rows by category).
3. **Match** each leg with ordered rules (§5).
4. **Check arithmetic:** each payout equals the sum of its transactions' nets to the cent.
5. **Tie out** the period (§7) and **list exceptions** (§6).
6. **Publish** a package: tie-out, matches with the rule and evidence behind each, exceptions, review decisions, and a hash manifest. All of these are Workbench outputs today.

## 5. Matching

### 5.1 Rules run in order of strength

1. **Exact identifiers:** Stripe's `automatic_payout_id`, an order number in the charge description or metadata, a payout ID in a deposit memo. Accepted automatically.
2. **Tolerant evidence:** same amount to the cent, a date inside the leg's window, **and at least one independent corroborating signal**: customer email, postcode, card last four, or a reference fragment. Accepted automatically only if exactly one candidate qualifies, on both sides.
3. **Everything else** goes to review with its candidates ranked and the evidence shown. The tool never picks among ties.

Matching is **one-to-one by default**. A record used by one match can't be used by another. If one charge could pair with two orders, both pairs go to review.

### 5.2 Evidence: amount and date are not enough

Measured on the real Olist e-commerce data (98,285 orders whose payments equal their item total). Each order's payments were treated as one charge dated at approval, and the order IDs were hidden. Reproduce with `python -m realdata.match_feasibility`.

| Match on | Correct | **Wrong** | Ambiguous | No candidate |
|---|---:|---:|---:|---:|
| amount + date (±1 day) | 73.51% | **1.40%** | 13.43% | 11.66% |
| amount + date (±1 day) + postcode | 86.09% | 0.01% | 0.25% | 13.66% |
| amount + date (±3 days) | 75.84% | 0.28% | 22.16% | 1.72% |
| amount + date (±3 days) + postcode | 97.50% | 0 | 0.32% | 2.18% |
| amount + date (±7 days) | 68.09% | 0.01% | 31.58% | 0.31% |
| **amount + date (±7 days) + postcode** | **99.31%** | **0** | 0.36% | 0.33% |

What this decides:
- **Amount alone is nearly useless.** Only 12.6% of order totals are unique in this data, because common prices and shipping fees collide.
- **"Only one candidate" is not proof.** Narrow windows produce single candidates that are wrong, because the true match fell outside the window. That's the failure a spreadsheet lookup makes silently.
- **A corroborating signal changes everything.** It lets the window widen, recovering late charges, while removing wrong matches.
- **The window is a per-leg setting, chosen by measurement.** The defaults come from this table; each client's run reports its own review and no-candidate rates.

Caveats: Olist's approval time stands in for the charge date, and its postcode is a five-digit prefix, coarser than an email address. Real Shopify-to-Stripe data should do at least this well with email, but that must be measured on client data (§8), not assumed.

### 5.3 One-to-many cases

- **Split tender:** one order paid by two charges (3.0% of Olist orders). Matched when the charges share an order reference, or when exactly two charges from the same customer inside the window sum to the order total. A wider search would risk subset-sum coincidences, so anything else goes to review.
- **Combined deposit:** one bank deposit covering several payouts. Accepted only for consecutive payouts on adjacent days whose sum equals the deposit; otherwise review.
- **Partial refunds:** linked to their charge through the processor's reference, never by amount.

## 6. Exceptions

| Code | Meaning | Usual cause | Suggested action |
|---|---|---|---|
| `ORDER_UNPAID` | Order marked paid, no charge found | Manual payment, test order, other gateway | Confirm payment method; exclude test orders |
| `CHARGE_NO_ORDER` | Charge with no matching order | Invoice or manual charge, deleted order | Match by hand or book as other income |
| `AMOUNT_MISMATCH` | Matched by reference, amounts differ | Edited order, partial capture, discount after payment | Review; 0.4% of Olist orders paid a different amount than their item total |
| `REFUND_UNBOOKED` | Refund in the processor, not in the books | Refund booked late or not at all | Record the refund |
| `PAYOUT_NOT_DEPOSITED` | Payout with no deposit | Not yet arrived, or not booked | In transit if after the period end; otherwise book it |
| `DEPOSIT_NO_PAYOUT` | Deposit with no payout | Different source, or gross booked instead of net | Check the deposit's source |
| `PAYOUT_ARITHMETIC` | Payout ≠ sum of its transactions' nets | Missing rows in the export, wrong date range | Re-export a wider date range |
| `FEES_UNBOOKED` | Processor fees not recorded as an expense | Deposits booked net with no fee entry | Book the period's fees |
| `AMBIGUOUS` | Several candidates qualify | Common amounts, missing references | Choose in review; the decision is logged |

## 7. Tie-out statement

For a period, the statement explains the full difference between sales and deposits. Each line is backed by the records behind it, and the bottom line must be zero:

```
  Gross sales (paid orders)                       A
− Refunds                                         B
− Processor fees                                  C
± Disputes and adjustments                        D
− In transit at period end (paid out later)       E
+ In transit at period start (paid out this period) F
− Deposits recorded in the books                  G
− Unexplained (each item listed in exceptions)    H
= 0
```

If a reconciling item can't be backed by records, it is listed as unexplained rather than absorbed into another line. Open items at the period end (in-transit charges, unmatched records) carry into the next period's run, using a state store like the [Magnificent Seven pipeline's ledger](https://github.com/ZakJRademacher/magnificent-seven-dashboard/blob/main/docs/OPERATIONS.md).

## 8. Proving it's correct

The same discipline as the [real-data regression suite](../realdata/README.md): results are checked against an independent oracle and pinned data, on every push.

1. **Synthetic stores with known truth.** A generator writes Shopify, Stripe and QuickBooks exports in each profile's format from a known ledger, then injects realistic faults: missing rows, late charges, partial refunds, split tenders, combined deposits, typos in references, colliding amounts. Since the truth is known, every match and exception is scored. **Acceptance bar: zero wrong automatic matches; every injected fault surfaces as the right exception code.**
2. **Real-data blind matching.** The Olist measurement in §5.2 joins the regression suite, so a rule change that adds even one wrong match on real data fails the build.
3. **Arithmetic invariants.** Payout sums, tie-out lines summing to zero, and every input record ending in exactly one place: matched, excepted or excluded with a reason.
4. **Client samples, with permission.** Each real engagement's anonymised review rate and corrections feed back into the profiles and the window defaults.

Reported metrics per run: automatic match rate, review rate, wrong matches on evaluation sets (target: zero), and exceptions by code.

## 9. Fit with Data Workbench

```
workbench/recon/
  profiles/*.json      source profiles (versioned, validated on import)
  canonical.py         normalise cleaned tables into canonical records
  match.py             ordered rules, one-to-one assignment, ambiguity detection
  tieout.py            period statement and carry-forward of open items
  exceptions.py        codes, reasons, suggested actions
```

The engine's existing pieces are reused unchanged: ingest and header detection, cleaning rules, the review queue (ambiguous matches become review records), recorded decisions, the audit file and the hash manifest. The AI recipe suggester (on a branch) could later propose profile mappings for unfamiliar exports, checked by the same validators.

## 10. Phases

| Phase | Scope | Why this order |
|---|---|---|
| 1 | Stripe payouts ↔ QuickBooks deposits (leg 3) and payout arithmetic (leg 2) | Smallest useful slice. Stripe's payout IDs make the arithmetic leg exact, and payouts are few, large and dated, so matching them to deposits is rarely ambiguous. "Which payouts never hit the books" is a very common bookkeeping question |
| 2 | Shopify orders ↔ Stripe charges (leg 1) with tolerant matching and the synthetic-store evaluation | Needs the evidence rules and the generator |
| 3 | Shopify Payments profile, refunds and disputes, period carry-forward, tie-out statement | Completes the monthly close |
| 4 | AI profile suggestions for unfamiliar exports; PDF bank statements | Only once the deterministic core is measured |

## 11. Alternatives considered

- **Spreadsheet lookups.** These pick the first match. They can't express one-to-one assignment or many-to-one sums, and per §5.2 they would be wrong on about 1 in 70 payments matched on amount and date.
- **Continuous sync tools.** These post summarised entries going forward and need account access. That's a good fit for steady-state bookkeeping, a poor fit for auditing past months or cleaning up after the fact. This design complements them.
- **Machine-learned matching.** Unnecessary at small-business volumes and hard to explain to an accountant. Ordered, explainable rules with measured error rates fit the job and the audience better.

## 12. Risks and open questions

- **Export drift.** Processors change columns. Mitigation: required-column validation per profile, with a clear error naming the missing column.
- **Weak customer evidence.** Guest checkouts and wallet payments may lack email or postcode, so review rates will be higher. Measure on the first client sample before promising rates.
- **Time zones.** Store-local and UTC dates disagree near midnight. The date window absorbs this, but tie-out period boundaries must use one declared time zone.
- **Scope creep into accounting.** The tool explains differences; it doesn't decide how to book them. Suggested actions stay suggestions.
- **Open:** whether to support Shopify Payments before Stripe in phase 1. That depends on which the first clients use.
