# Data Workbench

Turn related CSV and Excel exports into clean tables, reviewed relationships, and a reproducible data package. Version **2.0.0** adds reusable source formats, explicit precision rules and cross-field validation.

[Download the local app](deliverables/Data-Workbench-2.0.0.zip) · [Comparison and benchmark findings](docs/COMPARISON.md) · [Operating guide](docs/OPERATING_GUIDE.md)

[Additional checks on 471,908 public-source records](docs/ADDITIONAL_BENCHMARKS.md) document exact preservation results and practical limits for high-precision scientific measurements.

![Reviewed combined table in the fictional demo](evidence/browser/combined-view.png)

## Open the app

On Windows, install standard **64-bit Python 3.12 or newer** from [python.org](https://www.python.org/downloads/windows/), including its launcher or PATH option. Download and fully extract the ZIP into a writable folder, then double-click **[Start Workbench.cmd](Start%20Workbench.cmd)**. The local app opens at **http://127.0.0.1:8810**. Keep its terminal window open while working; Ctrl+C stops it. The first launch needs internet access and installs pinned dependencies into `%LOCALAPPDATA%/DataWorkbench/venv`. A standard Anaconda/Miniconda installation is also recognized. For a custom installation set `DATA_WORKBENCH_PYTHON` to its Python executable. No development environment or Node installation is needed.

The launcher and extracted download were tested with a separate Python 3.13.9 installation. On other operating systems, create a Python virtual environment, install `requirements.lock.txt`, and run `python -m workbench.app`; the double-click launcher was verified on Windows only.

Choose **Explore the demo** for a fictional customer, order, and payment project, or **Create a project** to add your own files.

## What it does

- Profiles CSV and `.xlsx` sources; lets you choose worksheet, header, delimiter, and encoding.
- Appends compatible exports through editable source-to-field mappings.
- Applies saved types, whitespace and category rules, reusable per-source date/number formats, required fields, bounds, decimal scale, and cross-field comparisons.
- Keeps identifiers as text, uses exact decimals, and preserves source bytes and record references.
- Flags conflicting keys, invalid records, and repeated records. Saves corrections and explicit exclusions separately from original files.
- Suggests exact-key relationships, previews unmatched and output row counts, and requires approval before publishing combined views.
- Aggregates child tables before joins when necessary to preserve the base table's grain.
- Exports complete CSVs, a review workbook, the audit, the recipe, review decisions, a data dictionary, a closed DuckDB snapshot, and a hash manifest.
- Reuses an unchanged, verified run. Changed data, rules, or decisions create a new run. Stale results are visibly labeled.

## Start with the demo

The first run has **20 input records = 14 accepted + 6 needing review**. Accepted order amount is **460.8500 source units**; accepted payment amount is **455.9750 source units**. These are different measures over different accepted populations, not a reconciliation of all orders against all payments.

There are two conflicting customer copies, one ambiguous order date, one invalid amount, and two copies of the same order. The fixtures are original fictional data, with no assumed currency. See [the operating guide](docs/OPERATING_GUIDE.md) for a complete review exercise and its expected results.

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

Processing runs on this computer; there is no hosted upload service. OneDrive synchronization is independent of the app. Saving a file here does not verify cloud synchronization.

Version-1 recipes migrate with earlier option defaults. Historical inputs, decisions and run snapshots are preserved. New recipes use version 2 and cannot be opened by the earlier app.

To move a project, stop processing and copy its entire `projects/<project-id>/` folder into the destination app's `projects/` folder. A recipe alone contains source identifiers, so importing it into a different project requires updating its source mappings.

## Implementation and checks

[Architecture and API](docs/TECHNICAL.md) · [Operating guide and practice exercise](docs/OPERATING_GUIDE.md) · [Verification](docs/QA.md) · [Benchmark reproduction](docs/BENCHMARKS.md) · [Dependency licenses](licenses/README.md)

From the app folder, using an environment with `requirements-dev.txt` installed:

```powershell
python -B -m pytest tests -q
python -B tools/browser_check.py --workdir C:/Temp/DataWorkbenchBrowser --evidence evidence/browser
python -B tools/benchmark.py --workdir C:/Temp/DataWorkbenchBenchmark --evidence evidence/million-row-check.json
```

Use a scratch location outside OneDrive for the generated benchmark data. The benchmark records measured elapsed time and peak process working memory; it does not impose a universal speed promise for every workbook shape.

## Project scope and licenses

An independent portfolio demonstration of configurable data preparation and reconciliation, with original fictional demo records. The app does not decide business meaning automatically: review mappings, keys, categories, missing-value policy and relationship grain before using a result for analysis. Successful benchmarks establish the documented cases, not a guarantee for arbitrary data.

Application code and the fictional demo are covered by [MIT](LICENSE). Runtime dependency notices remain in [licenses](licenses/README.md). External datasets retain their publishers' terms and are not bundled. The [original Excel Export Consolidator](https://github.com/ZakJRademacher/excel-export-consolidator) remains available for the native Power Query workflow.
