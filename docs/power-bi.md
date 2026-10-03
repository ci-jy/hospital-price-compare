# Power BI report and docs page

## Export

`hpc export --out docs` flattens the star schema into seven Parquet tables in `docs/data/`: the fact table `rate`, plus `hospital`, `service`, `plan`, `comparison_group`, `quality_flag`, and `payer_string` for the payer review. Each rate carries its comparison group, the group's metro median from `v_rate_compare` and its distance from that median. `docs/index.html` is built by reading those Parquet files back, so the page and the Power BI model use the same numbers.

## Power BI project

`report/` is a Power BI project in the PBIP format, so the model and pages are plain text files that diff in git.

```
report/
├── HospitalPrices.pbip
├── HospitalPrices.SemanticModel/definition/
│   ├── model.tmdl, relationships.tmdl, expressions.tmdl
│   └── tables/  rate, hospital, service, plan, comparison_group, quality_flag, payer_string
└── HospitalPrices.Report/definition/pages/
    ├── service_lookup/   service, setting and method slicers; rates by group, hospital and payer
    ├── payer_spread/     each payer's typical distance from the metro median, and payer by hospital
    └── data_quality/     comparable share, payer strings resolved, flags and methods by hospital
```

Each table is imported with `Parquet.Document(File.Contents(ParquetFolder & "<table>.parquet"))`. `ParquetFolder` is a text parameter holding a placeholder Windows path. Power Query can't take a path relative to the `.pbip`, so after opening it set the parameter to your checkout's `docs\data\` folder (Transform data, then Edit parameters) and refresh.

There is no base theme, so Desktop uses its default.

## Measures

On the `rate` and `payer_string` tables:

- `Median Negotiated Rate`: median `compare_amount`, but blank unless the filter holds exactly one comparison group, so a visual can't take a median over case rates and percentages at once. This leaves the service-lookup bar chart empty until you pick a narrow enough code, setting and method.
- `Metro Median Rate`: the same median with the hospital and payer filters removed.
- `Spread vs Metro Median`: `Median Negotiated Rate / Metro Median Rate - 1`.
- `Typical Spread vs Metro Median`: median of each dollar rate's own distance from its group's metro median. This one works across codes, so the payer page can rank payers.
- `Share of Comparable Rates`: rates with a comparable amount / all rates.
- `Share of Flagged Rates`, `Share of Payer Strings Resolved`: for the data-quality page.

## Static page

`docs/index.html` shows the same three views without Power BI. It is one self-contained file (no CDN, about 0.8 MB with the data inlined), so it works from disk or GitHub Pages.

![docs page, service lookup for 45378](screenshot.png)
