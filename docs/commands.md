# Commands

All commands run as `python3 -m hpc <command>`. Every command takes `--metro` (default `config/metro.yaml`) and `--db` (a DuckDB file; by default the one named in the metro config, `data/hpc.duckdb`). `-v` turns on debug logging.

```
python3 -m hpc discover [--hospitals N]          # cms-hpt.txt -> data/sources.csv
python3 -m hpc ingest  --metro config/metro.yaml [--hospitals N] [--all-codes]
python3 -m hpc check   [--min-hospitals N] [--min-encodings N] [--require-payer-map] [--payer-map PATH] [--report reports/coverage.md]
python3 -m hpc compare --code 45378 [--setting inpatient|outpatient|both] [--limit 25|0] [--format table|csv]
python3 -m hpc payers  [--unresolved] [--out review.csv]
python3 -m hpc export  [--out docs]                # Parquet in docs/data/ + docs/index.html
```

## discover

Reads each configured hospital's `https://<domain>/cms-hpt.txt`, finds its `location-name`, and writes the file URL and fetch time to `data/sources.csv`. `--hospitals N` takes only the first N hospitals in the config.

## ingest

Downloads (or reuses from `data/raw/`), stages and normalises the hospital files.

- `--hospitals N`: only the first N hospitals. The five Boise MSA hospitals are listed first, so `--hospitals 5` loads those.
- `--all-codes`: keep every billing code. By default only the 72 codes in `config/shoppable_codes.csv` are kept (the CPT codes from the CMS list of 70 shoppable services).

It prints how many rows were staged, how many became rates, how many are comparable, and the peak memory of the run.

Each run also merges every raw payer/plan string into `data/payer_map.csv` (see below). Commit that file after a review.

## check

Asserts that every staged row became a rate, that enough hospitals and encodings are covered, that each ranked group holds one pricing method and one unit and that its median covers exactly that group's rates, that every rate has a quality flag, and that every fact joins to its dimensions. Exits non-zero if any check fails.

- `--min-hospitals N`, `--min-encodings N`: coverage thresholds (default 1 each).
- `--require-payer-map`: also fail unless `data/payer_map.csv` lists every loaded payer/plan string, has some hand-checked rows, every row has a valid status, and no row marked `ambiguous` was given a plan type.
- `--payer-map PATH`: use another payer map (default: `payer_map.csv` next to the database).
- `--report PATH`: where to write the coverage report. Defaults to `reports/coverage.md` unless `--db` is given; pass `''` to skip.

## compare

Rates for one CPT/HCPCS/DRG code, grouped by setting, billing class, modifiers and pricing method, each with the group median and every rate's distance from it.

- `--code`: required.
- `--setting`: `inpatient`, `outpatient` or `both` (St. Luke's uses `both`).
- `--limit N`: rows per group, default 25, `0` for all.
- `--format csv`: write the rows as CSV instead of the table.

The `plans` column is how many raw payer/plan strings collapsed into that row.

## payers

Lists how each raw payer/plan string was resolved (hospital, raw payer, raw plan, resolved payer, plan type, match method and score). `--unresolved` shows only fuzzy and unresolved rows; `--out` writes a CSV for review.

## export

Writes seven Parquet tables to `<out>/data/` and builds `<out>/index.html` from them. Default `--out docs`. Takes under a second.

## The payer map

`data/payer_map.csv` has one row per raw payer/plan string with a `status` (`resolved`, `ambiguous` or `unresolved`) and `reviewed` (`yes` or `no`). New strings come in as `reviewed=no`. To correct one, set the payer and plan type, mark it `reviewed=yes`, and re-run `ingest`. Checked rows are never overwritten and always win over the automatic rules.

## Disk, time and memory

- The first five-hospital ingest downloads about 1.6 GB into `data/raw/` (gitignored). Unzipping the two Saint Alphonsus files takes about 10 GB of disk.
- The other seven hospitals add roughly another 1 GB of downloads and two more large unzipped Saint Alphonsus files.
- Later runs reuse cached files unless the URL in `cms-hpt.txt` changes. West Valley's URL is a signed blob URL that changes, so `ingest` re-reads `cms-hpt.txt` every run.
- With files cached, the twelve-hospital run (72 codes) takes about 55 seconds, stores 356,935 rates and peaks at about 500-580 MB of memory.
- DuckDB's memory limit and thread count are set in `config/metro.yaml` (1GB and two threads).
- `export` writes about 0.7 MB of Parquet.

## Screenshot

`scripts/screenshot_docs.sh` renders `docs/index.html` to `docs/screenshot.png` with headless Chromium or Chrome.

## Tests

```
python3 -m pytest -q
```

The tests run in about two seconds against `tests/fixtures/`, trimmed excerpts of the real St. Luke's Nampa wide CSV, Saint Alphonsus Nampa tall CSV, West Valley JSON and the Saint Alphonsus `cms-hpt.txt` (cut by `scripts/trim_fixtures.py`, with attester names and contact details replaced by placeholders).

- `tests/test_edge_cases.py` has one test per hard case: a bare `MA` plan, `BLUE CROSS - MA`, a percentage stored as dollars (next to a real 69-cent fee), a percent of Medicare, per-diem rows, and four descriptions of one colonoscopy code becoming one service. It also checks the payer map rewrite and `check --require-payer-map`.
- Two tests corrupt a copy of the database (a percent rate relabelled as dollars, and a ranking view that ignores pricing method) and confirm `hpc check` fails.
- `tests/test_export.py` checks the export against the database and the numbers inlined into `index.html` against the Parquet files.
- `tests/test_pbip.py` checks the TMDL model against the exported Parquet: column names and types, DAX and visual field references, and that relationships join unique keys with no orphans.
