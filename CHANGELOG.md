# Changelog

## 2026-10-09 – Power BI report and static demo page

- `hpc export --out docs` writes seven Parquet tables to `docs/data/` (a `rate` fact table with each rate's comparison group, metro median and distance from it, plus hospital, service, plan, comparison-group, quality-flag and payer-string tables) and builds `docs/index.html` from them.
- `report/HospitalPrices.pbip`: a TMDL semantic model over those Parquet files with DAX measures for the median negotiated rate (only inside one comparison group), spread versus the metro median and the share of comparable rates, and three pages: service lookup, payer spread and data quality.
- `docs/index.html` shows the same three views as a single static file; `scripts/screenshot_docs.sh` renders it with headless Chromium to `docs/screenshot.png`.
- `tests/test_pbip.py` checks every TMDL column and data type against the exported Parquet, every DAX and visual field reference against the model, and that each relationship joins a unique key with no orphans. `tests/test_export.py` checks the export against the database.

## 2026-10-09 – payer resolution and pricing-method classification at metro scale

- Twelve hospitals instead of five: Treasure Valley, Saint Alphonsus Ontario and Baker City, Weiser Memorial, and St. Luke's Magic Valley, Wood River and McCall. The five Boise MSA hospitals stay first, so `--hospitals 5` loads the same set as before.
- CSV readers ignore spaces around `|` in header names and fall back to Latin-1 when a file is not UTF-8.
- `hpc/method.py` holds the pricing-method rules and value checks that used to sit in `sql/normalize.sql`. New: percentages of Medicare or another fee schedule and dollar methodologies with only a percentage are not compared (`pct_of_fee_schedule`, `method_value_mismatch`); `per day`, `daily rate`, `per case` and `per admission` are recognised.
- Payer map moved from `config/payer_map.csv` to `data/payer_map.csv`. It now gets a row for every raw payer/plan string on each ingest, with `status` (resolved / ambiguous / unresolved) and `reviewed` (yes / no). Checked rows are never overwritten. 199 rows are checked.
- A bare `MA` is no longer read as Medicare Advantage: unless the payer only runs Medicaid it stays plan type `unknown`, flagged `plan_type_ambiguous`. `BLUE CROSS - MA` is read as Massachusetts.
- `hpc check --require-payer-map`, and `hpc check` writes `reports/coverage.md`.
- `tests/test_edge_cases.py` (11 tests).
