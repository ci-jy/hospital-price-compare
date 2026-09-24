# Results

From the twelve-hospital load (72 shoppable codes), as written to [`reports/coverage.md`](../reports/coverage.md), with the earlier five-hospital load for comparison:

| | five hospitals | twelve hospitals |
|---|---:|---:|
| negotiated rates | 180,454 | 356,935 |
| rates with a comparable amount | 180,229 | 356,528 (99.9%) |
| rates in a comparison group with two or more hospitals | - | 314,398 (88.1%) |
| raw payer names / hospital payer-plan strings | 839 / 1,944 | 872 / 3,971 |
| strings resolved to a known payer | 92.9% | 92.7% |
| comparison groups (none mixing methods) | 817 | 985 |

The three Saint Alphonsus files account for most of the payer strings, because Trinity lists around 900 payer/plan pairs per hospital, many of them Medicare Advantage plans from other states. Those make up most of the 284 strings still `unresolved`.

## How good are the payer rules?

I checked by hand every payer/plan string in the newly added St. Luke's, Treasure Valley and Weiser files (185 rows), plus the Massachusetts `MA` rows and seven rows reviewed earlier. The rules alone agree with 184 of those 199 (92.5%). The misses:

- `VA CCN` matched Coventry's `CCN` alias instead of Veterans Affairs.
- `Regence Blue Shield of Idaho / TriWest` matched the `BLUE SHIELD` alias of Blue Cross.
- Workers' comp networks were folded into the state fund.
- A `BCI PPO FEP` plan stayed with Blue Cross of Idaho instead of the federal employee program.
- Treasure Valley's `Intewest` is a typo for Interwest Health.

The corrections live in `data/payer_map.csv`, so the loaded data uses the checked answer.

The only real `MA` ambiguity left is `MEDICAID MA QMB` at the four Saint Alphonsus hospitals (Massachusetts or Medicare Advantage?), so those 472 rates are flagged `plan_type_ambiguous`.

## Quality flags

300,378 `ok`, 24,765 `billing_class_assumed`, 21,922 `payer_unresolved`, 3,552 `above_gross`, 2,734 `method_inferred`, 2,705 `pct_derived`, 472 `plan_type_ambiguous`, 268 `no_amount` and 139 `method_unknown`. The percent-of-fee-schedule and method/value mismatch checks found nothing in these files.

## Payer spread

Dollar-priced rates in comparison groups with at least two hospitals; the median of each rate's distance from its group's metro median, for payers with 100 or more such rates:

- Medicare: -10% (27,600 rates)
- Idaho Medicaid: -8% (67,386 rates)
- Blue Cross of Idaho: +5% (55,724 rates)
- Regence BlueShield of Idaho: +39% (3,857 rates)
- Idaho Workers' Compensation: +126% (37,926 rates)
- MultiPlan: +146% (4,414 rates)

Most large commercial carriers (Aetna, Cigna, UnitedHealthcare, SelectHealth, PacificSource) land at 0%, which mostly says that many hospitals use one fee schedule for several payers. Workers' comp and the rental networks (MultiPlan, First Choice Health) are where the big gaps are. These numbers are weighted by rate count, and the four Saint Alphonsus files hold about 95% of the dollar rates in those groups, so they mostly describe Trinity's contracts.

## Known issues

- `--all-codes` over all five Boise files (about 22 million rates) gets killed for memory on a 6 GB machine, even with DuckDB capped at 768 MB and one thread. The default shoppable-code load is the one that is tested and checked.
- Out-of-state Blue Cross plans (BlueCard) are folded into Blue Cross of Idaho, since local claims are priced under the local Blue contract. That is a judgement call, not something the files state.
- St. Luke's files use the setting `both`, which is kept apart from `inpatient` and `outpatient`, so some St. Luke's rates sit in their own group.
- The payer alias list is hand-written for Boise. A new metro needs its own aliases and a review pass over the `reviewed=no` rows in `data/payer_map.csv`.
- The two Oregon hospitals list their state Medicaid as plain `MEDICAID`, which resolves to Idaho Medicaid. The rules have no notion of which state a hospital is in yet.
- Only six of the twelve hospitals are in the Boise MSA. The others help with payer resolution (same payers, same systems), but a Twin Falls rate is not a Boise alternative for a patient.
- The roughly 2,700 Saint Alphonsus payer strings haven't been reviewed, so the 92.5% agreement figure says nothing yet about Trinity's naming.
- The `CCN` and `BLUE SHIELD` alias mistakes are fixed only through checked rows, not in `config/payers.yaml`, so they would come back at a new hospital.
- St. Luke's percent-of-charges rates carry `pct_derived` because the percentage column is filled only for some payers. The derived values match the posted ones wherever both exist, but they are still estimates.
- The Power BI report hasn't been opened in Power BI Desktop. The report JSON files were validated once by hand against Microsoft's published PBIR schemas, and `tests/test_pbip.py` checks the model against the data, but nothing here parses the TMDL or evaluates the DAX the way Power BI does.

## Next steps

- Review a sample of the Saint Alphonsus payer strings, fix the alias-order problems in `config/payers.yaml`, and re-measure.
- Open the report in Power BI Desktop on Windows, fix whatever it complains about, and add screenshots of the three pages.
- Weight the payer-spread measure by hospital so one system's many rows don't dominate it.
- Load the CMS shoppable-services list and MS-DRGs directly instead of the hand-copied code list.
