# Changelog

## 2026-10-09 – payer resolution and pricing-method classification at metro scale

- Twelve hospitals instead of five: Treasure Valley, Saint Alphonsus Ontario and Baker City, Weiser Memorial, and St. Luke's Magic Valley, Wood River and McCall. The five Boise MSA hospitals stay first, so `--hospitals 5` loads the same set as before.
- CSV readers ignore spaces around `|` in header names and fall back to Latin-1 when a file is not UTF-8.
- `hpc/method.py` holds the pricing-method rules and value checks that used to sit in `sql/normalize.sql`. New: percentages of Medicare or another fee schedule and dollar methodologies with only a percentage are not compared (`pct_of_fee_schedule`, `method_value_mismatch`); `per day`, `daily rate`, `per case` and `per admission` are recognised.
- Payer map moved from `config/payer_map.csv` to `data/payer_map.csv`. It now gets a row for every raw payer/plan string on each ingest, with `status` (resolved / ambiguous / unresolved) and `reviewed` (yes / no). Checked rows are never overwritten. 199 rows are checked.
- A bare `MA` is no longer read as Medicare Advantage: unless the payer only runs Medicaid it stays plan type `unknown`, flagged `plan_type_ambiguous`. `BLUE CROSS - MA` is read as Massachusetts.
- `hpc check --require-payer-map`, and `hpc check` writes `reports/coverage.md`.
- `tests/test_edge_cases.py` (11 tests).
