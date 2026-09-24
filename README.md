# hospital-price-compare

US hospitals have to publish every rate they have negotiated with insurers, but the files don't line up with each other. Each hospital names payers its own way, there is no plan-type field, and one hospital's colonoscopy is a case rate while the next one's is 80% of billed charges. This project loads those files for twelve hospitals around Boise, Idaho and makes them comparable one billing code at a time: which hospital charges which payer what, and how that rate is calculated. I started with Boise because its five general hospitals publish all three CMS file formats between them.

## Running it

You need Python 3.10 or newer and the packages in `requirements.txt` (duckdb, pandas, rapidfuzz, ijson, PyYAML, requests, pytest). There is no server. Everything goes into a DuckDB file at `data/hpc.duckdb`.

```
python3 -m hpc ingest --metro config/metro.yaml --hospitals 5
python3 -m hpc check --min-hospitals 5
python3 -m hpc compare --code 45378
python3 -m hpc ingest --metro config/metro.yaml --hospitals 12
python3 -m hpc check --min-hospitals 12 --require-payer-map
python3 -m hpc export --out docs
python3 -m pytest -q
```

Watch your disk. The first five-hospital ingest downloads about 1.6 GB, and the two Saint Alphonsus zips unpack to about 10 GB. Downloads are cached in `data/raw/`, so later runs are quick. If you only want the results, `docs/index.html` and the Parquet files in `docs/data/` are committed and the page opens straight from disk.

Part of what `compare` prints for a diagnostic colonoscopy:

```
$ python3 -m hpc compare --code 45378 --setting outpatient --limit 4
45378  PR Colonoscopy Flexible Diagnostic W Collection Specimen Brushing/Washing
Rates are only compared within one setting, billing class, modifier set and pricing method.

== outpatient | facility | percent of billed charges | 5 hospitals, median 80.0% of charges
   hospital                                 payer                                plan type                            rate vs median  plans  flag
   St. Luke's Boise Medical Center          PacificSource                        commercial               75.0% of charges       -6%      1  pct_derived
   St. Luke's Magic Valley Medical Center   PacificSource                        commercial               75.0% of charges       -6%      1  pct_derived
   St. Luke's Nampa Medical Center          PacificSource                        commercial               75.0% of charges       -6%      1  pct_derived
   St. Luke's Boise Medical Center          MultiPlan                            commercial               80.0% of charges       +0%      1  pct_derived
   ... 6 more (use --limit 0)
```

Every command and flag, plus run times and memory: [docs/commands.md](docs/commands.md)

## How it works

Each hospital lists its file in a `cms-hpt.txt` at its own domain, so discovery is just reading those. The format is guessed from the first megabyte. Tall CSVs go straight into DuckDB. Wide CSVs have one column group per payer and plan, so they get unpivoted and split on `|`. West Valley's 243 MB JSON is streamed with ijson rather than loaded whole. The files had their own surprises: Weiser puts spaces around the `|` in its header names, Treasure Valley's CSV isn't UTF-8 and has to be re-read as Latin-1, and the Saint Alphonsus Boise file is 6.3 GB once unzipped. DuckDB runs with a 1 GB memory cap and spills to disk.

Payer names were the hard part. St. Luke's writes `BLUE CROSS IDAHO [1007]`, Treasure Valley writes `BCI`, and across the twelve files there are 872 distinct payer names. Resolution tries hand-checked rows in `data/payer_map.csv` first, then alias rules from `config/payers.yaml`, then fuzzy matching for typos. Whatever is left stays unresolved. Plan type has to be read from free text, and `MA` is a trap: `BLUE CROSS - MA` is Blue Cross of Massachusetts, not Medicare Advantage. A bare `MA` is left as unknown instead of guessed. Every ingest adds new strings to the payer map as unreviewed, so that file doubles as the review queue.

Each rate then gets a pricing method (flat dollar, percent of charges, per diem, case rate or other) and is only ranked against rates with the same code, setting, billing class, modifiers and method. A percentage is never compared as dollars. That sounds obvious until you meet a row like Saint Alphonsus Nampa's clobazam, with `.76` in the dollar column and a real rate of 26.78% of charges. Every rate carries a quality flag, and `hpc check` fails if any comparison group mixes methods. It also rewrites [reports/coverage.md](reports/coverage.md).

`hpc export` flattens everything into Parquet tables that feed both `docs/index.html` and a Power BI project in `report/`.

Pipeline steps, the hospital list, schema and quality flags: [docs/pipeline.md](docs/pipeline.md)
The Power BI report, its measures and the static page: [docs/power-bi.md](docs/power-bi.md)
Results from the twelve-hospital load and the payer review: [docs/results.md](docs/results.md)

## Known issues

- `--all-codes` across the five Boise files gets killed for memory on my 6 GB machine, even with DuckDB capped lower. I haven't found which step goes past the cap, so the default shoppable-code load is the only one that's tested.
- I haven't opened the Power BI report in Power BI Desktop, which doesn't run on my Linux machine. The tests check the model against the data, but nothing evaluates the DAX the way Power BI does.
- Only six of the twelve hospitals are in the Boise MSA. The two Oregon ones list plain `MEDICAID`, which currently resolves to Idaho Medicaid because the rules don't know which state a hospital is in.
- I hand-checked the St. Luke's, Treasure Valley and Weiser payer strings but not the Saint Alphonsus ones, and those files hold most of the rates. Reviewing a sample of them is next.

The full list and what I'd do next are at the end of [docs/results.md](docs/results.md).

Project period: 2026-07-18 to 2026-09-26.
