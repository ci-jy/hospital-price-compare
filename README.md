# hospital-price-compare

Insurers in southwest Idaho don't pay hospitals anything like the same amount for the same service. I loaded the published negotiated-rate files of twelve hospitals around Boise and compared each rate to the metro median for the same billing code. The typical Idaho workers' comp rate comes out 126% above that median, MultiPlan's 146% above, and Medicare's 10% below. One caveat: the four Saint Alphonsus files hold about 95% of the dollar rates behind those figures, so mostly they describe one hospital system's contracts.

The hard part was making the files comparable at all. Hospitals have had to publish these rates for a while, but every hospital names payers its own way, there is no plan-type field, and one hospital's colonoscopy is a case rate while the next one's is 80% of billed charges.

## What the comparison shows

Dollar-priced rates in groups with at least two hospitals, taking the median of each rate's distance from its group's metro median:

| payer | typical distance from metro median | rates |
|---|---:|---:|
| Medicare | -10% | 27,600 |
| Idaho Medicaid | -8% | 67,386 |
| Blue Cross of Idaho | +5% | 55,724 |
| Regence BlueShield of Idaho | +39% | 3,857 |
| Idaho Workers' Compensation | +126% | 37,926 |
| MultiPlan | +146% | 4,414 |

Aetna, Cigna, UnitedHealthcare and most other big commercial carriers land at 0%. That doesn't mean they negotiate the same rates. Mostly it means a lot of hospitals use one fee schedule for several payers.

The results are also on a static page, `docs/index.html`, here open on a diagnostic colonoscopy (CPT 45378):

![docs page, service lookup for 45378](docs/screenshot.png)

## Running it

You need Python 3.10 or newer and the packages in `requirements.txt`. There's no server. Everything goes into a DuckDB file at `data/hpc.duckdb`.

```
python3 -m hpc ingest --metro config/metro.yaml --hospitals 5
python3 -m hpc check --min-hospitals 5
python3 -m hpc compare --code 45378
python3 -m hpc ingest --metro config/metro.yaml --hospitals 12
python3 -m hpc check --min-hospitals 12 --require-payer-map
python3 -m hpc export --out docs
python3 -m pytest -q
```

Check your free disk space first. The first five-hospital ingest downloads about 1.6 GB, and the two Saint Alphonsus zips unpack to about 10 GB. Downloads are cached in `data/raw/`, so later runs are quick. If you only want the results, `docs/index.html` and the Parquet files in `docs/data/` are committed, and the page opens straight from disk.

Every command and flag, with run times and memory: [docs/commands.md](docs/commands.md)

## Inside the pipeline

Each hospital lists its file in a `cms-hpt.txt` on its own domain, so finding the files is easy. Reading them isn't. They come in three formats (tall CSV, wide CSV, JSON), and each one had its own surprise. Weiser puts spaces around the `|` in its header names. Treasure Valley's CSV isn't UTF-8 and has to be re-read as Latin-1. The Saint Alphonsus Boise file is 6.3 GB once unzipped, so DuckDB runs with a memory cap and spills to disk.

Payer names took the most work. St. Luke's writes `BLUE CROSS IDAHO [1007]`, Treasure Valley writes `BCI`, and the twelve files have 872 distinct payer names between them. Hand-checked rows in `data/payer_map.csv` win, then alias rules from `config/payers.yaml`, then fuzzy matching for typos. Whatever is left stays unresolved. `MA` was a trap: `BLUE CROSS - MA` is Blue Cross of Massachusetts, not Medicare Advantage, so a bare `MA` is left unknown rather than guessed.

Then every rate gets a pricing method (flat dollar, percent of charges, per diem, case rate or other), and it's only ranked against rates with the same code, setting, billing class, modifiers and method. A percentage is never compared as dollars. One Saint Alphonsus Nampa row has `.76` in the dollar column when the real rate is 26.78% of charges. `hpc check` fails if any comparison group mixes methods.

`hpc export` writes Parquet tables that feed both the static page and a Power BI project in `report/`.

Pipeline steps, hospitals, schema and quality flags: [docs/pipeline.md](docs/pipeline.md)
The Power BI report and its measures: [docs/power-bi.md](docs/power-bi.md)
Full results and the payer-rule review: [docs/results.md](docs/results.md)

## Known issues

- I hand-checked the St. Luke's, Treasure Valley and Weiser payer strings but not the Saint Alphonsus ones, and those files hold most of the rates. Reviewing a sample of them is next.
- The payer spread is weighted by rate count, which is why one system dominates it. Weighting by hospital would be fairer.
- `--all-codes` across the five Boise files gets killed for memory on my 6 GB machine, even with DuckDB capped lower. I haven't found which step goes past the cap, so only the default shoppable-code load is tested.
- I haven't opened the Power BI report in Power BI Desktop, which doesn't run on Linux. The tests check the model against the data, but nothing evaluates the DAX the way Power BI does.

Project period: 2026-07-18 to 2026-10-09.
