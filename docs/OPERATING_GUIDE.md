# Operating Data Workbench

The fictional demo shows the full workflow: inspect files, choose rules, review exceptions, and join the corrected tables. Start there before adding your own exports.

## The first five minutes

1. Open `Start Workbench.cmd` and choose **Explore the demo**.
2. Inspect the four source files. The February workbook has a title row, so the app selects row 2 as its header. CSV and Excel order exports map into the same `orders` table despite alternate headings.
3. Open **Recipe & relationships**. Text keeps IDs such as `001`. Customers use `customer_id` as a key; orders use `order_id`; payments use `payment_id`. The saved region mapping changes `E` to `East`.
4. Click **Run project**. The result has 20 parsed rows, 14 accepted, and 6 requiring review. The raw inputs are unchanged.
5. Open **Results & review**, choose `orders`, and show **Needs review**. **Inspect / correct** displays original values, issues, and editable corrections.

## Resolve the fictional demo

For this fictional demo, use the following stated corrections. Real source corrections would need supporting evidence:

- For order `O102`, check the `order_date` correction box and enter `2026-01-05`. The demo's intended date is January 5; the app does not infer that from `01/05/2026`.
- For order `O104`, correct `amount` to `30.00`, the demo's intended value.
- For the customer copy named `June B.`, choose **Explicitly exclude**. The demo retains the `June Brooks` customer record.
- In the orders recipe, change duplicates to **Exclude exact repeats**. This policy keeps one valid copy of `O106` and records the other as excluded.
- Save and rerun. Expect **20 = 18 accepted + 0 review + 2 excluded** (5 customers, 7 orders, 6 payments), with **7 accepted orders totaling 625.9750** source units.

## Connect the tables without multiplying orders

1. After the corrected table run, open **Recipe & relationships**.
2. Add a combined view named `orders_combined` with base `orders`.
3. Add `customers` as a left lookup: base key `customer_id`, lookup key `customer_id`.
4. Add `payments` as a left lookup: base key `order_id`, lookup key `order_id`.
5. Add a child aggregation for payments: **sum**, field `paid_amount`, output `paid_total`. Payment `P1` and `P2` belong to the same order; directly joining both would repeat that order's amount.
6. Click **Preview joins**. Expect **7 → 7 rows**. One base order has no accepted customer match. Three base orders have no payment match. A missing payment stays missing, rather than being treated as zero.
7. Approve the relationships and rerun. `O100` has a `payments__paid_total` of **125.5000**. Payment `P5` references `O999`, which is absent from the orders base; the payment remains in the clean payments table and audit.

Inner joins are available only by explicit choice and show the number of base records they would remove. Repeated lookup keys block a join until you resolve them or aggregate the child table. Composite keys use comma-separated field names in corresponding order.

## Use your own files

- Create a project, add the files, and confirm sheet/header settings in **Inspect & configure**. Set the optional last data row to stop before a footer or totals section. Records outside the selected boundary are outside the imported table; the complete original file is retained. Uploads are copied into project storage; the originals are untouched.
- Generate a starting recipe. A narrow built-in alias list recognizes headings such as `Cust ID` and `Customer ID`; matching column signatures may be grouped. Confirm that the proposed tables mean the same thing.
- To combine additional exports, use **Move to** to assign a source to an existing table, then **Map columns**. New fields are preserved; omitted source columns require an explicit choice.
- Use **Configure** beside a field to set date order, decimal/thousands separators, missing markers, category mappings, allowed values, or bounds. Blank optional values stay missing.
- When files use different conventions, open **Source format** beside a source mapping. Give the format a reusable name, enable only the parsing overrides that differ, and assign it to other matching exports. Editing a named format affects every assigned source. Required fields, categories and business checks stay on the logical table.
- Decimal fields support a maximum fractional scale. **Reject any extra fractional digit** holds `12.34000` under a four-place contract; it never rounds it to four places. **Ignore redundant trailing zeros** preserves the earlier recipe default. Enable case-insensitive categories when `furniture` should become the allowlisted `Furniture`.
- **Cross-field checks** allows comparisons such as `ship_date >= order_date`. These run after parsing and before duplicate checks. Make the operands Required if a missing value must also be held.
- Suggestions inspect a bounded sample. Confirm every identifier as Text, especially unfamiliar headings. Excel timestamps are retained in full as text; choosing Date deliberately requests a date-only analytical field. Later padded values in inferred numeric fields enter review rather than silently losing their padding.
- Select keys only when they identify the intended record grain. A key can contain several fields. Default repeated-record behavior is review; choose keep-all or exclusion only when justified.
- Run the project. Resolve file issues before treating the outputs as a complete project. Fix row issues through corrections or source replacement, then rerun.

## Refresh an existing project

Use **Replace file** on the source card. This keeps its source slot and saved mapping while retaining the old bytes. Reinspect the selected header and sheet. A new column must be mapped or explicitly omitted; a missing mapped column blocks execution until corrected.

Unchanged source bytes, filenames, settings, rules, and decisions reuse a verified result. New source versions get new record IDs, so old row corrections are not silently applied to replacement files. Reusable field/category rules remain in the recipe.

Version 2 reads version-1 recipes with their previous defaults, then saves new edits as version 2. Raw files, review decisions, prior input snapshots and completion manifests remain on disk. A changed engine build triggers a fresh run. Older versions reject version-2 recipes; keep a source backup before downgrading and do not edit historical snapshots to make them load.

## Understand the outputs

- `<table>.csv`: all accepted records, including source references.
- `<table>-review.csv`: all held records and their reasons.
- `<table>-excluded.csv`: every explicitly excluded row or duplicate copy, with a reason.
- `<view>.csv`: approved, combined tables at the selected base grain.
- `<view>-unmatched-*.csv`: base records without a lookup and lookup records without a base match, separately for each relationship. These remain available even when an inner join omits unmatched base records.
- `audit.csv`: raw values, prepared values, record disposition, provenance, and issues for every parsed record.
- `Review.xlsx`: summary, exact totals, file issues, labeled clean/review previews, and relationship results. Previews stop at 1,000 rows; CSVs are complete.
- `recipe.json`, `decisions.json`, and `data-dictionary.json`: reproducibility and handoff material.
- `data.duckdb`: closed snapshot of clean tables and result/audit tables. Open read-only in a DuckDB-capable tool.
- `manifest.json`: source hashes, output hashes, counts, totals, profiles, and run status. It is published last.

## Limits and interpretation

- Up to **50 sources, 50 MB each, 250 MB combined, and 1,000,000 parsed input rows**. One processing job at a time. Preview pages are bounded; full datasets remain in exports.
- CSV: UTF-8, Windows-1252, or explicitly selected UTF-16; comma, semicolon, tab, or pipe delimiters. Source references count logical CSV records, including headers, rather than physical lines inside quoted records.
- Excel: `.xlsx`, one selected sheet per source. Split workbooks containing separate tables into sheets or files. Legacy `.xls`, macros, merged analytical layouts, PDF extraction, and automatic formula recalculation are outside this version.
- Formula inputs use the workbook's stored results. Formula text and cache provenance are retained in the audit; freshness is unverified. Missing cached results go to review. Numeric cells with simple zero-padding formats preserve displayed leading zeros.
- Dates require an explicit format. Ambiguous source dates remain text until you choose a rule. Complete Excel timestamps are retained as text; choosing Date requests a date-only field. Decimal scale supports up to 12 fractional places and 26 whole-number digits. The default ignores redundant trailing zeros; a strict scale rejects every extra fractional digit. Excess precision goes to review rather than being rounded.
- A relationship suggestion is evidence of compatible data, not proof of business meaning. No fuzzy entity merging, imputation, unit conversion, or currency conversion is performed.
- Field profiles show missing/invalid-null counts and distinct values. Numeric outliers use 1.5 × IQR as a diagnostic only; they are retained.
- File errors make the project **incomplete**. Accepted outputs then cover only readable sources. The app never labels that subset a complete project.
- CSV formula-looking text is made inert with an apostrophe. Typed numeric negatives stay numeric. Original source bytes and raw audit JSON preserve the original text.

## Storage and reproducibility

Source, documents, evidence, and completed runs live together in the extracted app folder. Actual user projects are under `projects/` and excluded from Git and release downloads. Active databases and spill files live under `%LOCALAPPDATA%/DataWorkbench/runtime`; completed packages contain closed database copies. Old source versions and run snapshots remain available on disk.

Files are processed on your computer. If you store projects in OneDrive, synchronization is handled separately by OneDrive.

Version-1 recipes migrate with earlier option defaults. Historical inputs, decisions and run snapshots are preserved. New recipes use version 2 and cannot be opened by the earlier app.

To move a project, stop processing and copy its entire `projects/<project-id>/` folder into the destination app's `projects/` folder. A recipe alone contains source identifiers, so importing it into a different project requires updating its source mappings.
