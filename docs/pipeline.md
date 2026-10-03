# How the pipeline works

## Hospitals

The five general hospitals of the Boise City MSA come first. The rest are Treasure Valley Hospital (the only other Boise MSA hospital whose `cms-hpt.txt` points at a downloadable file), Ontario and Weiser from the wider Boise-Mountain Home-Ontario area, and the two Boise systems' other hospitals in southern Idaho and eastern Oregon, which contract with the same payers. St. Luke's Meridian and Elmore share the St. Luke's Boise file, so they are not separate rows.

| Hospital | City | System | File encoding |
|---|---|---|---|
| St. Luke's Boise Medical Center | Boise | St. Luke's | wide CSV (481 MB) |
| Saint Alphonsus Regional Medical Center | Boise | Trinity Health | tall CSV in a zip (6.3 GB unzipped) |
| St. Luke's Nampa Medical Center | Nampa | St. Luke's | wide CSV (347 MB) |
| Saint Alphonsus Medical Center Nampa | Nampa | Trinity Health | tall CSV in a zip (4.0 GB unzipped) |
| West Valley Medical Center | Caldwell | HCA | JSON (243 MB) |
| Treasure Valley Hospital | Boise | physician-owned surgical | wide CSV, Latin-1 (54 MB) |
| Saint Alphonsus Medical Center Ontario | Ontario, OR | Trinity Health | tall CSV in a zip |
| Weiser Memorial Hospital | Weiser | independent | tall CSV (79 MB) |
| St. Luke's Magic Valley Medical Center | Twin Falls | St. Luke's | wide CSV |
| St. Luke's Wood River Medical Center | Ketchum | St. Luke's | wide CSV |
| St. Luke's McCall Medical Center | McCall | St. Luke's | wide CSV |
| Saint Alphonsus Medical Center Baker City | Baker City, OR | Trinity Health | tall CSV in a zip |

The [CMS templates](https://github.com/CMSgov/hospital-price-transparency) allow three encodings: tall CSV, wide CSV (one column group per payer and plan) and JSON. KFF's [ongoing challenges with hospital price transparency](https://www.healthsystemtracker.org/brief/ongoing-challenges-with-hospital-price-transparency/) brief covers why the files are hard to compare.

## Steps

1. Discover (`hpc/discover.py`). Every hospital publishes `https://<domain>/cms-hpt.txt` listing its file URL. The configured `location-name` is looked up there and the URL and fetch time go to `data/sources.csv`.
2. Ingest (`hpc/ingest/`). The format is detected from the first megabyte (zip magic, `{`, or the CSV header on row 3). Header names are matched with spaces around `|` ignored (Weiser writes `standard_charge | gross`), and a CSV that fails as UTF-8 is re-read as Latin-1 (Treasure Valley). Tall CSVs are read straight into DuckDB. Wide CSVs are filtered to the wanted codes, then the payer columns (`standard_charge|<payer>|<plan>|negotiated_dollar` and so on) are unpivoted, split on `|` and pivoted back into one row per payer and plan. JSON is streamed item by item with ijson. DuckDB spills to disk past its memory limit.
3. Resolve payers (`hpc/resolve.py`). Checked rows in `data/payer_map.csv` (`reviewed=yes`) win. Otherwise alias rules from `config/payers.yaml` apply: longest alias first, carrier names before program words like `MEDICARE`, then the plan column when the payer column just says `COMMERCIAL`. Then RapidFuzz `token_sort_ratio >= 88` for typos. Anything left keeps its cleaned name and is marked `unresolved`. Plan type comes from the text. A bare `MA` is read as Medicaid for Medicaid-only payers (Molina, Magellan) and otherwise left as plan type `unknown` with status `ambiguous`. `BLUE CROSS - XX` state codes are removed before plan type is read.
4. Classify pricing method (`hpc/method.py`). The CMS `methodology` field when it holds one of the four template values, otherwise the algorithm text (`per diem`, `per day`, `case rate`, `per admission`, `% of billed charges`), otherwise which number is filled in. Then value checks: a percentage in the dollar column, a fraction written for a percentage, a missing percentage recovered from the notes or dollar / gross, a percentage of Medicare or another fee schedule (`120% of current Medicare`, `APC rate x 135%`), and a dollar methodology with only a percentage filled in. The last two become `other` and are never compared.
5. Normalise (`sql/normalize.sql`). Each rate gets its method (`flat_dollar`, `percent_of_charges`, `per_diem`, `case_rate` or `other`), a compare amount and unit, and a quality flag. Percent-of-charges rates are compared as a percentage, never as dollars.
6. Export (`hpc/export.py`). See [power-bi.md](power-bi.md).

The `$0.76` case is a real one. Saint Alphonsus Nampa lists clobazam 10 mg at a $2.85 gross charge, 26.78% of billed charges, with `.76` in the dollar column. A naive rule would call that 76% or 76 cents; the pipeline compares it as 26.78% of charges. The test fixtures keep that row.

## Schema

`sql/schema.sql` defines a star schema around `fact_rate`.

- `dim_hospital`: one row per loaded file, with its encoding, source URL and file date.
- `dim_service`: one row per CPT / HCPCS / DRG code, plus how many different descriptions the files use for it.
- `dim_payer`: resolved payer.
- `dim_plan`: one row per payer and plan type.
- `map_payer_plan`: each hospital's raw payer/plan strings mapped to `dim_plan`, with match method and score.
- `fact_rate`: one negotiated rate: setting, billing class, modifiers, gross charge, dollar, percentage, `pricing_method`, `compare_amount`, `compare_unit`, `quality_flag`.
- `v_rate_compare`: rates with the median of their comparison group. A group is one code, setting, billing class, modifier set and pricing method, so a professional fee, a discontinued-procedure (`-74`) rate and a percent-of-charges rate never sit in the same ranking.
- `v_compare_summary`: one row per comparison group with its rate count and min / median / max.

## Quality flags

- `ok`: nothing found.
- `pct_in_dollar_field`: percent-of-charges rate whose only number is a dollar value of 1 or less with no usable gross charge, so `$0.76` is read as 76%.
- `pct_rescaled`: percentage written as a fraction (`0.76`) and scaled to 76.
- `pct_derived`: percentage missing; taken from the notes ("79.1% of billable gross charges") or dollar / gross charge.
- `method_value_mismatch`: methodology says fee schedule, case rate or per diem but only a percentage is given. Not compared.
- `pct_of_fee_schedule`: a percentage of Medicare or another fee schedule, not of charges. Not compared.
- `method_unknown`: methodology `other` with nothing to classify it. Not compared.
- `no_amount`, `zero_amount`: nothing to compare.
- `above_gross`: flat or case rate above the hospital's own gross charge.
- `pct_over_100`: percent of charges above 100.
- `payer_unresolved`: payer name not matched to a known payer.
- `plan_type_ambiguous`: plan type left `unknown` on purpose (a bare `MA`).
- `method_inferred`: methodology missing or `other`, method read from the other fields.
- `billing_class_assumed`: file has no billing class; treated as facility.
