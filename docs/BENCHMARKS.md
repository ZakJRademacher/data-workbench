# Reproduce the comparison

See also [three additional public-source checks](ADDITIONAL_BENCHMARKS.md), run against the published 2.0.0 download. They document 471,908 further records, required type setup, and scientific-number precision limitations separately from the original comparison.

Keep downloaded sources, active databases and bulk outputs in a scratch folder outside the repository. Install runtime requirements plus `requirements-dev.txt` in a Python 3.12+ environment. Native Excel checks additionally need Windows and desktop Excel. Headless browser checks need Microsoft Edge installed. No Kaggle credentials are included or required by the application.

## Frozen inputs and policy

[`external-source-manifest.json`](../evidence/external-source-manifest.json) records input hashes, sizes, source links, terms and selections. Acquisition and freeze took place during October 30, 2026 in America/Denver (October 31 UTC). Earlier Kaggle downloads were reused only after comparison with retained acquisition hashes: Superstore from October 29 and Olist from October 16. A changed public download is a new benchmark, not a silent update to this evidence.

| Source | Selection | Declared preparation policy |
| --- | --- | --- |
| [UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail) | Full 541,909-row Excel workbook | Quantity integer, UnitPrice exact decimal; other fields text, including complete InvoiceDate timestamps and CustomerID. Missing IDs, cancellations and repeated lines remain valid source records. |
| [Chicago Food Inspections](https://data.cityofchicago.org/d/4ijn-s7e5) | First 50,000 inspection IDs before 2020 | All fields text; trim outer whitespace, retain missing fields and unusual categories, preserve long and multiline descriptions. The 64.8 MB original exceeds the unchanged single-file cap; lossless 5,000-row CSV partitions are required for Workbench. |
| [NYC historical 311](https://data.cityofnewyork.us/d/76ig-c548) | First 50,000 requests from 2019 by unique key; 16 explicitly listed scalar fields | Preserve ZIPs, timestamps and text; absent optional fields stay missing, including valid missing closure dates. Disjoint key-interval acquisition avoids broad-query timeouts; an independent server count verifies completeness before sorting and selection. |
| [Kaggle Superstore](https://www.kaggle.com/datasets/vivek468/superstore-dataset-final) | Complete 9,994-row CSV | Sales, Profit and Discount exact decimals; Quantity integer; two declared MDY dates; remaining fields text. No key or deduplication rule is imposed on legitimate lines. |
| [Kaggle Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) | Orders, items, payments, customers, products and category translations; 448,440 rows | Text by default; exact price, freight and payment decimals. Keep missing milestones/products. Aggregate items and split payments by order before left joins; missing aggregates stay NULL. |

These permissive source-preservation policies test faithful preparation, not a claim that every naturally missing value is an error or that every source row is ready for every business analysis. Category normalization, required fields and deduplication need a separately agreed analytical contract. The seed-42 corruption workload below tests strict known-invalid rejection separately.

UCI attribution: Daqing Chen (2015), *Online Retail*, UCI Machine Learning Repository, DOI [10.24432/C5BW33](https://doi.org/10.24432/C5BW33), CC BY 4.0. Chicago data is published by the Chicago Department of Public Health under its portal terms. NYC data is published through NYC Open Data/311 under its portal terms. Superstore is published by Vivek on the linked Kaggle page, which credits the original Tableau creators and restricts its stated purpose to education; its license metadata says Other (specified in description). Olist is published by Olist and distributed under CC BY-NC-SA 4.0. External raw or derived row-level datasets are not bundled or relicensed as application code. Review publisher terms before downloading or using them.

## Commands

Examples below use `C:/Temp/WorkbenchBenchmark` for disposable data and a sibling checkout of the preserved Excel project as `../excel-export-consolidator`.

1. Download the Kaggle datasets from their linked pages. Put Superstore at `C:/Temp/WorkbenchBenchmark/inputs/superstore/Sample - Superstore.csv` and the six documented Olist CSVs under `inputs/olist/`. Create `inputs/olist/prior-source-manifest.json` as `{"source_hashes": {"filename.csv": "sha256", ...}}` using the frozen per-file hashes. Do not substitute unrelated Superstore variants.
2. Fetch public sources and freeze the manifest. The acquisition helper can reuse verified local historical Kaggle copies; an independent reader can supply the same frozen files directly.

```powershell
python -B tools/acquire_external.py --cache C:/Temp/WorkbenchBenchmark/inputs
python -B tools/external_benchmark.py --cache C:/Temp/WorkbenchBenchmark/inputs --workdir C:/Temp/WorkbenchBenchmark/external --old-project ../excel-export-consolidator --case all
```

The independent controls use standard-library CSV, openpyxl, explicit date policies, Decimal arithmetic and Python dictionaries for joined totals. They compare each raw field, normalized field, row disposition and lineage reference through ordered hashes, plus exact declared totals. Workbench output never supplies its own expected answer. The helper writes aggregate evidence only; data and database copies stay in the scratch folder. Full processing/export and cached replay are timed separately. Memory is sampled process RSS, not a universal maximum across all possible inputs.

## Comparable eight-field workload

Raw public schemas outside the old eight-field CSV contract are **unsupported by Excel Consolidator**, not cleaning failures. The direct comparison uses identical original files and an explicit Superstore projection selecting Row ID, Order ID, Order Date, Ship Date, Category, Region, Product ID and Sales. MDY dates are serialized to the same ISO convention for both tools. No business fields are invented.

```powershell
python -B tools/project_superstore.py --source 'C:/Temp/WorkbenchBenchmark/inputs/superstore/Sample - Superstore.csv' --output C:/Temp/WorkbenchBenchmark/projection/batch_a_public.csv
python -B tools/compare_consolidator.py --old-project ../excel-export-consolidator --input ../excel-export-consolidator/data/incoming --workdir C:/Temp/WorkbenchBenchmark/original --name original
python -B tools/compare_consolidator.py --old-project ../excel-export-consolidator --input C:/Temp/WorkbenchBenchmark/projection --workdir C:/Temp/WorkbenchBenchmark/superstore --name superstore
python -B tools/seeded_cases.py --projection C:/Temp/WorkbenchBenchmark/projection/batch_a_public.csv --output C:/Temp/WorkbenchBenchmark/seeded-inputs
python -B tools/compare_consolidator.py --old-project ../excel-export-consolidator --input C:/Temp/WorkbenchBenchmark/seeded-inputs --workdir C:/Temp/WorkbenchBenchmark/seeded --name seeded --labels C:/Temp/WorkbenchBenchmark/seeded-inputs/known-labels.json
```

Seed 42 samples 300 rows and adds 20 copies: impossible dates, shipping before ordering, a fifth fractional digit (including redundant zero), missing keys/products/regions, negative sales, unknown categories, malformed grouping, wrong date conventions, exact repeats and conflicting copies. Known-valid controls include category case/whitespace and a padded identifier. All 320 record labels are specified independently before execution. Natural source anomalies never count as injected defects.

For native Excel, run the helper against a disposable workbook copy, then pass its JSON to the comparison command with `--native`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/native_compare.ps1 -SourceFolder C:/Temp/WorkbenchBenchmark/projection -OutputFolder C:/Temp/WorkbenchBenchmark/native -WorkbookTemplate '../excel-export-consolidator/deliverables/Export Consolidator.xlsx'
```

Native refresh timing excludes the later COM extraction of table evidence. Its memory counter covers Excel's own process and excludes separate Power Query containers. The old project's Python reference timing is separately labeled and must not be described as native Excel speed.

## Application and download checks

```powershell
python -B -m pytest tests -q
python -B tools/browser_check.py --workdir C:/Temp/WorkbenchBenchmark/browser --evidence evidence/browser
python -B tools/benchmark.py --workdir C:/Temp/WorkbenchBenchmark/million --evidence evidence/million-row-check.json
python -B tools/package_release.py
python -B tools/clean_install_check.py --archive C:/Temp/WorkbenchBenchmark/Data-Workbench-2.0.0.zip --workdir C:/Temp/WorkbenchBenchmark/clean --python C:/Python313/python.exe --evidence evidence/clean-install.json
```

Use a fresh scratch folder and ensure port 8810 is free for the clean-install check. It extracts the download, creates a standard-Python environment, runs the public launcher, processes the fictional demo, verifies cached replay and checks every exported hash. Packaging excludes private projects, environments, caches and external raw data.

The clean-install command above uses the archived [2.0.0 package](https://github.com/ZakJRademacher/data-workbench/blob/79d89586061172ac1f6a9a58dc3eb913b4976966/deliverables/Data-Workbench-2.0.0.zip), downloaded to the stated scratch path. Current downloads and new installation checks use version 2.0.1.
