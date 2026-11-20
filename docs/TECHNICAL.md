# Architecture and local API

The local server uses Python 3.12+ with FastAPI/Uvicorn. DuckDB handles preparation and joins, and openpyxl reads Excel files. Browser assets are bundled, so running the app does not need Node. The release records exact dependency versions and licenses.

## Pipeline

`immutable source bytes → source inspection → versioned recipe → streaming staging CSV → DuckDB normalization and validation → review/exclusion decisions → clean logical tables → validated joins → exports → completion manifest`

CSV source values stage as text with logical record references. Excel is streamed twice in parallel: once for stored values and once for formulas. Date formats are explicit. Decimal conversion validates grouping, range, and fractional precision before accepted publication. Identifiers remain text by default. A source file with structural errors contributes no partial records; its parsed count is unknown.

Recipe operations are allowlisted. Table/field names are validated identifiers; SQL identifiers and literals are quoted. Users cannot submit SQL or Python expressions. Cleaning and exports can run without the HTTP/UI layer.

For key-based conflicts, all active copies of a key go to review if normalized values differ or one repeated-key copy is invalid. Explicitly excluded copies leave the active conflict group. Exact-duplicate exclusion applies only after validation, with deterministic ordering by source name, logical row, and record ID. Keep-all still respects configured key conflicts; omit a key when repetitions represent legitimate separate records.

Missing values and failed conversions are visible in field profiles. IQR outlier diagnostics operate on accepted numeric values and do not alter their disposition. Sums use exact decimals; diagnostic quartile comparisons use floating-point thresholds and do not define financial results.

## Storage and publication

`projects/<id>/project.json` stores current settings. `raw/<source-id>/<sha256>.<extension>` retains uploaded versions. `runs/<run-id>/input-snapshot.json` captures inputs, rules, and decisions. Run output is downloadable only after `outputs/manifest.json` exists. Output hashes are checked on replay and export.

The runtime directory is `%LOCALAPPDATA%/DataWorkbench/runtime/<workspace-hash>/`. `DATA_WORKBENCH_RUNTIME` overrides it for tests. Active DuckDB files and spill files stay there. Completed snapshots are copied after closing their connections. Restart changes queued/running status to interrupted; partial outputs remain unpublished, and a new run rebuilds from retained inputs. Intermediate files are retained for diagnosis rather than automatically deleting user work.

Reconciliations live in `reconciliations/<id>/` beside the projects: `inputs/<role>/<original name>` (unchanged), `request.json` (settings and each input's SHA-256), `status.json`, and `outputs/`, published by renaming a complete staging folder. They run the same code as `python -m workbench.recon`, on the same single executor as project runs.

One executor serializes jobs. Processing snapshots isolate a running job from later setting changes. A semantic run fingerprint includes engine version and source-code build hash, source bytes/settings/names, recipe, and correction/exclusion values. Decisions include source-version-specific record IDs. Stable pagination orders by source and row.

## API

The server binds to `127.0.0.1`: port 8810 in browser mode, or a free port chosen at start in the desktop window (`workbench/desktop.py`, pywebview over WebView2, run by `pythonw.exe`). The Host header must name that exact address and port, which defeats DNS rebinding. Requests a browser labels as coming from another site or another local port (`Sec-Fetch-Site` other than `same-origin` or `none`) are refused, reads included. Mutating requests also require `X-Workbench-Token` from `GET /api/session`, and cross-origin mutations are rejected. The token changes on restart. There is no public authentication or deployment surface.

Only the first app session on a projects folder holds `.workbench.lock` in it, and only that session relabels runs left queued or running as interrupted, so a second session can never relabel work the first is still processing. A desktop session also records its port and a one-time key in `.desktop-instance.json`, which a second launch uses to bring the open window forward (`POST /api/desktop/focus`).

| Interface | Behavior |
| --- | --- |
| `GET/POST /api/projects` | List summaries or create a named project. |
| `GET /api/projects/{id}` | Current sources, recipe, decisions, and latest run ID. |
| `POST .../sources` | Multipart `file`, optional `replace_id`; bounded one-file upload. |
| `PUT .../sources/{source_id}` | Sheet, header row, optional last data row, encoding, delimiter settings. |
| `POST .../suggest-recipe` | Save a fresh version-2 starting recipe; replaces current rules. |
| `PUT .../recipe` | Validate and save mappings, rules, keys, and combined views. |
| `POST .../runs` | Return a run ID/status; execute asynchronously or reuse verified output. |
| `GET .../runs/{run_id}` | State, progress, stale flag, and published manifest when present. |
| `GET .../runs/{run_id}/rows` | `table`, `disposition`, `offset`, `limit` (1–200); paginated records. |
| `PUT .../decisions/{record_id}` | `{corrections: {field: text}, exclude: boolean}`; validated on rerun. |
| `POST .../join-preview` | Predict approved-view output counts against current clean tables. |
| `GET .../runs/{run_id}/files/{filename}` | Download a published, hash-checked output. |
| `GET .../runs/{run_id}/package` | Download the complete published output ZIP. |
| `POST /api/demo` | Create the original fictional demonstration project. |
| `POST /api/projects/import` | `{folder}`: copy projects not already present from another projects folder, verified file by file. |
| `GET /api/reconciliations` | Saved reconciliations, and saved month closes that can be carried forward. |
| `POST /api/reconciliations/uploads` | Multipart `file`: stage one export (CSV or XLSX) for a reconciliation. |
| `POST /api/reconciliations` | Start a payouts (`period`, optional `close_account`, `prior_close`) or orders (`platform`) reconciliation from staged uploads. |
| `GET /api/reconciliations/{id}` | Request, state, and when complete the result, outputs and currency. |
| `GET /api/reconciliations/{id}/files/{name}` | Download one output: `summary.md`, `matches.csv`, `refunds.csv`, `exceptions.csv`, `result.json`, `close-state.json`. |

Decimal and date values are serialized as strings rather than lossy JSON numbers. Validation errors return a readable error with HTTP 400. The advanced recipe editor exposes the same versioned JSON used by the visual controls; source identifiers must be remapped when moving a recipe to an unrelated project.

## Files worth reading

- `workbench/ingest.py`: source inspection, raw preservation, and streaming records.
- `workbench/engine.py`: normalization, validation, reconciliation, relationship checks, and exports.
- `workbench/store.py`: project persistence, decisions, jobs, replay, and preview.

The demo's expected counts and totals are specified separately in `workbench/demo.py` and tested against actual outputs.

## Version 2 recipes

The root `formats` object names source parsing profiles; a source assignment optionally references one through `format`. Profile `fields` can override only `date_format`, `date_exact`, `decimal_separator`, `thousands_separator`, and `strip_symbols`. They cannot override validation or execute code. Logical fields add `decimal_places` (0–12), `precision_mode` (`nonzero` or `strict`), `case_insensitive`, `unsigned`, `date_exact` and inference provenance. Tables add allowlisted `checks` with `left`, `op` and `right` fields. Numeric values remain DECIMAL(38,12); scale validation occurs before conversion.

Version-1 migration deep-copies the current recipe, adds empty formats/checks and changes the version. Missing new options retain previous defaults. Migration never rewrites earlier input snapshots, decisions or manifests. The older validator requires version 1, so it rejects version-2 recipes. Raw retained Excel bytes allow a fresh version-2 run to recover complete timestamps even when a historical version-1 output had truncated them.
