# Verification for version 2.0.0

Checks were performed on Windows 11 using Python 3.12.14, a separate standard Python 3.13.9 installation, Microsoft Edge and desktop Excel 16.0. Results establish the documented cases and limits.

## Regression and browser checks

**22 Workbench tests pass**, including the original 16 and six preservation/rule regressions. The preserved Excel project also passes its 16 tests. See [test results](../evidence/tests.xml).

Coverage includes raw preservation, leading-zero IDs (including CamelCase headings and values outside the inference sample), complete Excel timestamps, Unicode and multiline whitespace, source-specific date formats, strict decimal scale without rounding, case-insensitive canonical categories, shipping/order checks, version-1 migration, malformed files, missing-versus-zero values, duplicates/conflicts, saved decisions, schema changes, interrupted jobs, output tampering, cardinality checks, unmatched records, exact totals, row limits and local API protections.

The complete headless Edge workflow passes with no JavaScript page errors. It exercises upload, recipe controls, precision settings, source formats, cross-field checks, review corrections, exclusions, stale warnings, reruns, relationships, child aggregation, join approval, combined results, package download and mobile width. [Browser record and screenshots](../evidence/browser/browser-check.json).

The fictional demo starts at **20 = 14 accepted + 6 review**. After the documented decisions, it becomes **20 = 18 accepted + 0 review + 2 excluded**, with seven orders totaling **625.9750** source units. Split payments remain a separate measure. Saved review-workbook values preserve text identifiers and exact decimal text; creator metadata names Zak Rademacher.

## Independent comparison

The [comparison report](COMPARISON.md) covers original-fixture parity, the full eight-field Superstore projection, seed-42 known defects, five frozen external sources and native Excel refreshes. Every external raw and normalized field is checked independently, not merely a sample or a matching grand total. Source policies deliberately retain legitimate missing values, cancellations and repeated lines.

Native Excel refresh runs use disposable copies of the original workbook. The original fixture and 9,994-row projection both reconcile to the Python reference and Workbench. The original workbook, repository and working links are preserved. Native timing and Excel-process memory are separately labeled; Power Query container memory is outside that counter.

## Scale and installation

The generated capacity run processes **1,000,000 rows** (300,000 customers, 350,000 orders and 350,000 payments) in **66.462 seconds**, with **457.4 MB peak process RSS** sampled at 50 ms. All rows are accepted. The combined view remains 350,000 orders; independent order/payment totals each equal **4,320,960** exactly. The fixture has 31.4 MB of inputs and about 807 MB of complete outputs. Source generation is excluded from processing time. [Measured evidence](../evidence/million-row-check.json).

The machine has 127.9 GB RAM and DuckDB uses a configured 1 GB processing-memory limit. Row width, export audits and Excel parsing affect memory and speed; these measurements are not a universal performance promise. Bulk inputs/outputs stay outside the project library.

The clean-download check extracts the curated ZIP, creates a fresh environment with the separate Python 3.13.9 installation, installs the pinned runtime, invokes the public PowerShell launcher, processes the demo, verifies cached replay, downloads the package and checks all output hashes. It does not use the development Python environment. [Clean-install evidence](../evidence/clean-install.json). Evidence records the exact tested archive; later documentation-only packaging can change archive bytes without changing the runtime.

## Remaining limits

The application remains local, with 50 files, 50 MB per file, 250 MB combined and 1,000,000 parsed rows. The 64.8 MB Chicago selection requires explicitly documented lossless partitioning. Excel Consolidator retains a different native workbook workflow and its existing 10,000-file/100,000-row guardrails; those are limits, not a measured capacity promise.

There is no automatic inference of business meaning, fuzzy identity matching, imputation, currency conversion, formula recalculation, legacy XLS support or hosted upload service. Excel formula cache freshness is unverified. Review schemas and source conventions before treating prepared tables as fit for a particular analysis. Local saving does not verify OneDrive synchronization.
