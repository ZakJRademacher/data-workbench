# Excel Consolidator and Data Workbench: comparison

This comparison checks where Data Workbench 2.0.0 matches the Excel Consolidator and where their purposes differ. Workbench reproduces the documented sales result and adds configurable related-table preparation. Excel Consolidator remains the native Power Query option for a handoff that does not require Python. Version 2.0.1 retains this behavior; the external benchmark results below identify the 2.0.0 release actually tested.

## Before and after

The starting comparison found material regressions despite both earlier 16-test suites passing. The recorded pre-repair Workbench result used its closest supported recipe; it could not express all old rules.

| Original 309-record fixture | Accepted | Review | Skipped/excluded | Accepted sales |
| --- | ---: | ---: | ---: | ---: |
| Excel Consolidator | 291 | 10 | 8 | 57,507.7198 |
| Workbench before repair | 172 | 129 | 8 | 36,232.6048 |
| Workbench 2.0.0 compatibility profile | 291 | 10 | 8 | 57,507.7198 |

[Recorded starting point](../evidence/before-v1.json) · [Final original-fixture evidence](../evidence/compatibility-original.json). Every record disposition and every accepted/skipped business value matches the independent old reference; a fresh native Excel refresh also matches all accepted records and values. Typed missing/invalid review values are NULL in Workbench, with the original values retained in its raw audit.

Repairs preserve complete Excel timestamps and text IDs, retain leading zeros, allow reusable source-specific parsing, enforce a strict four-decimal grammar without rounding, canonicalize allowlisted categories without case sensitivity, and check shipping against ordering dates before duplicate grouping. Version-1 recipes retain option defaults on migration; historical project files remain preserved. The previous validator rejects version-2 recipes.

## Comparable workloads

| Identical inputs and contract | Both tools: accepted / review / skipped | Accepted sales |
| --- | --- | ---: |
| Original fixture, 309 rows | 291 / 10 / 8 | 57,507.7198 |
| Explicit eight-field Superstore projection, 9,994 rows | 9,994 / 0 / 0 | 2,297,200.8603 |
| Seed-42 corruption workload, 320 rows | 190 / 120 / 10 | 70,094.6730 |

The projection selects eight existing fields and serializes declared MDY dates to the same ISO convention for both tools. No missing business fields are invented. The seeded case has independent labels for all 320 records, separate from natural source missingness: zero known-invalid acceptances and zero known-valid rejections. Exact repeats and conflicting copies follow the declared contract. [Projection comparison](../evidence/compatibility-superstore.json) · [Seeded ground truth comparison](../evidence/compatibility-seeded.json).

## External sources: as received and configured

| Source | Rows checked | Workbench result | Excel Consolidator as received |
| --- | ---: | --- | --- |
| UCI Online Retail | 541,909 | All fields/dispositions match; complete timestamps, missing customer IDs, cancellations and repeated lines retained | Unsupported XLSX/schema |
| Chicago Food Inspections | 50,000 | Original file exceeds 50 MB; after explicitly documented lossless partitioning, every field matches | Unsupported schema |
| NYC historical 311, 2019 selection | 50,000 | All selected fields match; ZIPs, timestamps and 614 missing closure dates retained | Unsupported schema |
| Kaggle Superstore, complete raw schema | 9,994 | All fields and exact numeric totals match; date conventions explicitly configured | Unsupported raw schema; comparable projection tested separately |
| Kaggle Olist, six related tables | 448,440 | All fields match; 99,441 joined orders remain 99,441 | Unsupported schemas and related-table workload |

All five frozen sources pass independent CSV/openpyxl, Decimal and Python-dictionary controls. Every raw value, prepared value, disposition, source reference and declared numeric total is checked. These policies retain legitimate natural missingness and repeated lines; zero review rows under permissive source-preservation rules is not a claim that every business question can use every row unchanged. The separate strict seeded contract tests invalid-record rejection. No unexplained dropped records, silent rounding or multiplied joins were found in these cases.

For Olist, independent totals are merchandise **13,591,643.70**, freight **2,251,909.54**, and payments **16,008,872.12**, in source BRL units. Items and split payments are aggregated by order before joining. There are 775 orders without items and one without payments; these are retained with missing aggregates rather than fabricated zeros. The six sources are not bundled.

[Frozen sources, selections, terms and hashes](../evidence/external-source-manifest.json) · [Source policies and reproduction](BENCHMARKS.md). Public data was acquired/frozen during October 30 local time, October 31 UTC; Kaggle historical copies were hash-verified against their earlier provenance. Bulk downloads and disposable outputs remain outside the repository/library.

## Measured processing and refresh

Times below cover processing and complete export after intake setup. Acquisition, initial upload/inspection and independent control calculations are outside this timed interval. Cached replay verifies existing hashes and is shown separately. RSS is sampled process resident memory, with sequential-case baseline effects; these are observed runs, not universal speed or memory promises.

| Workbench external case | Full processing/export (s) | Verified replay (s) | Peak process RSS (MB) |
| --- | ---: | ---: | ---: |
| UCI | 144.825 | 0.902 | 855.4 |
| Chicago, partitioned | 24.257 | 1.663 | 1015.7 |
| NYC 311 | 12.135 | 0.234 | 424.7 |
| Superstore raw | 5.101 | 0.050 | 193.1 |
| Olist six-table project | 58.204 | 0.717 | 485.7 |

The generated one-million-row case took **66.462 s** with **457.4 MB peak RSS**, preserved all rows and maintained 350,000 joined orders. It produces about 807 MB of complete audit/export outputs from 31.4 MB of source CSV. Wide real files can need more memory than narrow generated fixtures.

| Comparable case | Old Python reference (s) | Native Excel refresh (s) | Excel-process peak MB | Workbench processing/export (s) |
| --- | ---: | ---: | ---: | ---: |
| Original fixture | 0.020 | 17.878 | 457.6 | 0.609 |
| Superstore projection | 0.408 | 109.114 | 466.8 | 2.693 |

Native Excel 16.0 refreshed disposable workbook copies, and all accepted records/values matched. Refresh timing excludes later COM extraction; its peak working set covers Excel only and excludes separate Power Query containers. The Python reference is a control implementation, not native Excel. Differing output/audit breadth and measurement scopes prevent a simple memory ranking.

## Setup, refresh and judgment

| Area | Excel Consolidator | Data Workbench 2.0.0 |
| --- | --- | --- |
| Initial setup | Desktop Excel; point the workbook to configured CSV exports | Standard Python 3.12+; first-launch dependency download; inspect files and confirm mappings/rules |
| Business contract | Defined eight required sales fields and configured prefixes | Explicit configurable tables, named parsing formats, rules, decisions and guarded views |
| Refresh | Replace/add configured folder files, then Refresh All | Replace a source slot, preserve previous bytes/mappings, rerun; unchanged inputs use verified replay |
| Error handoff | Workbook correction queue and import history | Row review, corrections, exclusions, file issues, raw lineage, full audits and manifests |
| Limits | Existing 10,000-file/100,000-row guardrails; CSV workflow | 50 files, 50 MB/file, 250 MB/project, 1,000,000 rows; CSV/XLSX and one processing job |
| Strongest use | Repeatable known-format CSV cleanup inside Excel | Broader local multi-source preparation and related-table reconciliation |

Setup effort was observed as the operations above; no invented human setup-time metric is claimed. Workbench requires more up-front policy choices for unfamiliar data. It does not infer whether two datasets share business meaning, whether a missing value is invalid, or which duplicate policy is appropriate. The Excel version remains a competent specialized deliverable and is preserved as an alternative workflow.

**Recorded checks:** 22 Workbench regressions; 16 preserved Excel tests; browser controls and review/rerun workflow; two native Excel comparisons; all five external sources; independent seed-42 labels; one-million-row capacity case; standard-Python clean download/launcher/export checks; migration/history preservation and older-version rejection. [Qualification record](../evidence/qualification.json) · [Verification scope and limitations](QA.md).

For the tested workloads, Workbench preserves the expected records and totals under the saved rules. New data still needs someone to confirm mappings, record grain, and business rules before interpreting its output.
