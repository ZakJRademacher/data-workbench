# Real-data regression suite

Public datasets are run through the workbench, and every count, total, join gap and exported value is compared with an independent reference implementation ([`oracle.py`](oracle.py)). The oracle reads the raw files using only Python's standard library (`csv`, `decimal`, `datetime`). It imports nothing from the workbench or DuckDB, so a defect in the app can't also hide in the answer it's checked against.

| Mode | When | Rows | Time (one CI runner) |
|---|---|---:|---:|
| `sample` | Every push and pull request, on Linux and Windows | ≈ 270,000 | ≈ 1.5 min |
| `full` | Nightly, or on demand | 1,554,645 | ≈ 5 min |

Full mode also checks published facts about each dataset, such as Olist's 99,441 orders and 16,008,872.12 in payments, NYC flights' 52,606 flights without a plane record, and Online Retail's 5,268 exact repeat lines.

## What it checks

| Case | Data | Checked against the oracle |
|---|---|---|
| Olist e-commerce | 5 related tables, 448,369 rows | Row counts; price, freight and payment totals to the cent; the late-delivery rule holds exactly the late order IDs; an order view with customer, item and payment roll-ups matches per order; cleaned text equals the source; a join that would double-count is blocked |
| NYC flights 2013 | 5 tables, 367,687 rows, R-style `NA` | Totals and populated counts; weather readings held for review are exactly the unparseable ones (such as `1e3`); unmatched rows for each of four joins |
| UCI Online Retail | 541,909 rows | Exact-repeat lines removed and totals over the kept rows |
| Excel exports | `.xlsx` with real date and timestamp cells | Date cells convert to dates; real times of day go to review |
| OurAirports | 86,343 rows | Every exported text value equals the source, including southern-hemisphere coordinates that start with `-` |
| Windows-1252 | US airports, first accent past 128 KB | Encoding detected; every row and name intact |
| Order matching | Olist orders and payments with the order IDs hidden | The order-to-payment matcher makes no wrong matches and matches at least 95% (99.24% on the full data) |

The last three cases guard against the bugs fixed in 2.0.2. Run against the 2.0.1 code, exactly those three tests fail and the rest pass. Disabling the double-counting guard or breaking duplicate removal also makes the matching test fail.

## Data

Every file is downloaded from a URL pinned to a fixed commit or release and must match the SHA-256 in [`datasets.json`](datasets.json). A changed upstream file therefore stops the run instead of quietly changing what is tested. No dataset is stored in this repository; each keeps its publisher's terms.

- [Olist Brazilian e-commerce](https://github.com/olist/work-at-olist-data): Olist, CC BY-NC-SA 4.0
- [UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail): Daqing Chen (2015), DOI 10.24432/C5BW33, CC BY 4.0. Retrieved from the copy in [Spark: The Definitive Guide](https://github.com/databricks/Spark-The-Definitive-Guide)
- [nycflights13](https://pypi.org/project/nycflights13/): 2013 New York departures, from the PyPI package of the same name
- [OurAirports airport codes](https://github.com/datasets/airport-codes): via the Frictionless Data `datasets` collection

## Run it

```sh
pip install -r requirements.lock.txt pytest
python -m realdata.fetch --cache .realdata-cache          # about 100 MB, hash-checked
python -B -m pytest realdata -q                            # sample mode
REALDATA_MODE=full python -B -m pytest realdata -q         # every row
```

On Windows PowerShell, set the mode with `$env:REALDATA_MODE = "full"`. Set `REALDATA_REPORT=report.json` to write row counts and timings per case.

## Match feasibility

`python -m realdata.match_feasibility` measures how often payments can be matched to orders without a shared ID on the Olist data, scoring correct, wrong, ambiguous and unmatched outcomes. Its results set the matching rules in [the reconciliation design](../docs/RECONCILIATION_DESIGN.md#52-evidence-amount-and-date-are-not-enough).

## Adding a case

Add the file to `datasets.json` with its pinned URL and hash, compute the expected results in `oracle.py` from the raw file without using the workbench, then add a test in `test_realdata.py` that runs the workbench and compares the two. Before relying on a new test, confirm that it fails when the behaviour it guards is broken.
