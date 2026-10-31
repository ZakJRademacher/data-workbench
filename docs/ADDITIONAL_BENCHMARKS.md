# Additional public-source checks

Tested October 30, 2026, America/Denver (October 31 UTC), against a fresh download of the published **2.0.0** ZIP. All 98 extracted files matched the archive byte for byte. The application was not modified for these tests.

**Result: every record, prepared value, disposition, source reference and declared numeric total matched independent controls. No records were lost, and no accepted numeric values changed. However, 5,730 records require review under the numeric import contract. These are successful preservation and validation checks, not an assertion that every source record is immediately analysis-ready.**

## Results

| New source | Input records | Accepted | Review | Excluded / unreadable files | Processing and export | Cached replay | Peak process RSS |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| UCI Bank Marketing | 41,188 | 41,188 | 0 | 0 / 0 | 14.040 s | 0.141 s | 205.3 MiB |
| UCI Beijing air quality, 12 station files | 420,768 | 420,074 | 694 | 0 / 0 | 99.187 s | 1.093 s | 955.2 MiB |
| USGS all-events 30-day earthquake snapshot | 9,952 | 4,916 | 5,036 | 0 / 0 | 9.855 s | 0.063 s | 261.1 MiB |
| **Total** | **471,908** | **466,178** | **5,730** | **0 / 0** | | | |

Processing excludes acquisition, upload/profile setup and independent verification. It includes the actual run and exports. Replay returned the same verified run rather than reprocessing. RSS was sampled every 50 ms in the test process; it includes the driver and retained reference objects, so it is not isolated application-only memory. Single-machine timings are observations, not performance guarantees. Python 3.13.9 and the release's pinned runtime requirements were used; psutil was added only for measurement.

The [frozen source manifest](../evidence/additional/sources.json) records selections, acquisition times, licenses and SHA-256 hashes. The [summary](../evidence/additional/summary.json) contains full results, independent numeric controls and per-field missing counts. The [release identity](../evidence/additional/release-identity.json) identifies the tested download:

`a986581b0c697409b9c98d93b96797ff797a41ae50234e5881a2220558ab7f76`

## What needed attention

**Numeric precision is a practical limitation on scientific data.** The numeric importer supports ordinary decimal notation with at most 12 nonzero fractional places. In Beijing's temperature field, 687 records exceeded that scale and seven used scientific notation. USGS had 5,036 records containing values beyond the scale. These are naturally occurring source values, not injected errors or necessarily incorrect measurements. Exact affected-field counts and examples are in the [Beijing](../evidence/additional/beijing-numeric-limits.json) and [USGS](../evidence/additional/usgs-numeric-limits.json) diagnostics.

All of those rows remained in review, with original source text intact in the raw audit. They did not enter accepted totals. Typed numeric previews in review can reflect the 12-place database representation; use the original raw value to decide a correction. Do not treat a review preview as a lossless scientific measurement. Keeping an affected field as Text preserves its spelling but also means it cannot be used directly as a numeric measure. Any conversion or rounding requires an explicit analytical policy; these tests did not round the inputs or silently relax the rules to obtain acceptance.

**Sampled type suggestions still need confirmation.** Bank's `nr.employed` was initially suggested as an integer, although later records contain fractions. The saved policy explicitly made it Decimal. Beijing also needed numeric types and its `NA` missing marker configured; USGS's `gap` was configured as Decimal instead of the sampled Integer suggestion. The evidence records differences between the first file's sampled type suggestions and the declared policy. These results apply to the saved policies, not an untouched default recipe or a claim of automatic understanding of arbitrary schemas.

## Source policies and attribution

- **[UCI Bank Marketing](https://archive.ics.uci.edu/dataset/222/bank+marketing):** complete `bank-additional-full.csv`, semicolon-separated, 21 fields. Explicit integer and decimal fields; remaining fields Text. Retained legitimate repeats, negative indicators, 12,718 `unknown` category cells and the `pdays=999` sentinel. Those values were not invented missing-value errors. No key or deduplication rule was imposed. Attribution: Moro, Rita and Cortez (2014), DOI [10.24432/C5K306](https://doi.org/10.24432/C5K306), CC BY 4.0.
- **[UCI Beijing Multi-Site Air Quality](https://archive.ics.uci.edu/dataset/501/beijing+multi+site+air+quality+data):** all 12 PRSA station CSVs, March 2013–February 2017, appended into one logical table. Independently verified unique station/year/month/day/hour keys. Kept per-file `No` identifiers as Text. Preserved negative readings; all 74,027 documented `NA` cells became missing, never zero. Attribution: Chen (2017), DOI [10.24432/C5RK5G](https://doi.org/10.24432/C5RK5G), CC BY 4.0; the UCI source credits the Beijing Municipal Environmental Monitoring Center and China Meteorological Administration.
- **[USGS earthquake feed](https://earthquake.usgs.gov/earthquakes/feed/v1.0/csv.php):** complete rolling all-events 30-day CSV at acquisition, 22 fields. Preserved complete UTC `time` and `updated` strings, event IDs, negative measurements, non-earthquake event types and optional missing measurements. Independently verified unique event IDs. Credit: U.S. Geological Survey. USGS-produced data are U.S. public domain; [USGS policy](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits) distinguishes third-party content. This feed changes; the recorded hash identifies this particular snapshot.

No bulk source files or row-level outputs are redistributed here. External sources retain their terms. Natural anomalies were kept separate from the earlier [seed-42 corruption tests](BENCHMARKS.md). These additional cases exercise typed preparation, preservation and the 12-file append; they add no new join, Excel-native-refresh or browser claims. Those checks remain documented in the original qualification.

## Independent checks and reproduction

The [driver](../tools/additional_benchmark.py) uses standard-library CSV, explicit frozen source policies and Decimal arithmetic to calculate expectations from original inputs. It compares every mapped value, every raw field, record disposition, source filename and logical record number through ordered hashes. It independently checks all accepted numeric totals and exact accepted numeric values, every cell in delivered accepted/review/excluded CSVs, complete populations, output hashes and cached replay. Workbench output does not provide its own expected answer. An empty source cell and numeric zero have different expectations.

Use a fresh checkout and a scratch directory outside the repository or OneDrive. Install `requirements.lock.txt` plus `psutil`. From the repository root:

```powershell
python -B tools/additional_benchmark.py --cache C:/Temp/WorkbenchAdditional/inputs --workdir C:/Temp/WorkbenchAdditional/runs --evidence C:/Temp/WorkbenchAdditional/evidence --acquire-only
python -B tools/additional_benchmark.py --cache C:/Temp/WorkbenchAdditional/inputs --workdir C:/Temp/WorkbenchAdditional/runs --evidence C:/Temp/WorkbenchAdditional/evidence --run-only
```

The first command freezes acquisition and policy before evaluating results. The second checks the frozen hashes and imports the application only from the downloaded release. Preserve that cache to reproduce the same rolling USGS snapshot; a later download is a new test, not a reproduction of this hash. The driver creates fresh projects and times their first processing run separately from immediate cached replay. Source IDs and scratch paths are omitted from retained public evidence.

## Interpretation

The extra checks support confidence in faithful preparation within declared rules. They also demonstrate why a person must confirm field types, missing markers and numerical requirements. The current release is useful for ordinary tabular preparation; high-precision scientific numeric workflows require a separate conversion decision or another numeric representation. This report does not claim universal data-quality validation or that source data are factually correct.
