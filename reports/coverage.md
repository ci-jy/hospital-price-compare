# Coverage

Written by `hpc check` on 2026-10-09 23:23 UTC.

- Hospitals loaded: 12
- Rates: 356,935
- Rates with a comparable amount: 356,528 (99.9%)
- Rates in a comparison group with at least two hospitals: 314,398 (88.1%)
- Payer names: 872 raw names in 3,971 hospital payer/plan strings; 3,683 (92.7%) resolved to 44 payers and 84 payer x plan-type plans, 4 left ambiguous on purpose
- Automatic payer rules agree with 184 of 199 hand-checked rows in data/payer_map.csv (92.5%)

## Checks

| check | result | detail |
|---|---|---|
| rates loaded | pass | 356,935 rates |
| every staged row became a rate | pass | 356,935 staged vs 356,935 rates |
| hospital coverage | pass | 12 hospitals with comparable rates (need 12) |
| encodings | pass | 3 file encodings loaded (need 1) |
| no comparison mixes pricing methods | pass | 985 comparison groups, 0 mixed |
| percent rates never in dollars | pass | 0 rates with the wrong unit |
| every rate has a quality flag | pass | 0 missing |
| facts join to dimensions | pass | 0 orphan rates |
| payer map exists | pass | 1,399 rows in payer_map.csv |
| payer map covers every raw name | pass | 1,399 of 1,399 payer/plan strings listed |
| payer map rows have a status and some are reviewed | pass | 199 hand-checked, 0 without a valid status |
| ambiguous names stay unresolved | pass | 1 ambiguous in the map, 0 given a plan type |

## By hospital

| hospital | city | encoding | rates | comparable | in a multi-hospital group | payer/plan strings | resolved |
|---|---|---|---:|---:|---:|---:|---:|
| St. Luke's Boise Medical Center | Boise | wide | 7,712 | 97.6% | 84.8% | 60 | 95.0% |
| Saint Alphonsus Regional Medical Center | Boise | tall | 90,552 | 100.0% | 82.7% | 898 | 92.5% |
| St. Luke's Nampa Medical Center | Nampa | wide | 4,860 | 99.1% | 98.0% | 45 | 100.0% |
| Saint Alphonsus Medical Center Nampa | Nampa | tall | 74,778 | 100.0% | 93.9% | 897 | 92.4% |
| West Valley Medical Center | Caldwell | json | 2,552 | 100.0% | 76.1% | 44 | 100.0% |
| Treasure Valley Hospital | Boise | wide | 2,223 | 100.0% | 71.7% | 33 | 72.7% |
| Saint Alphonsus Medical Center Ontario | Ontario, OR | tall | 92,072 | 100.0% | 83.2% | 964 | 92.8% |
| Weiser Memorial Hospital | Weiser | tall | 2,513 | 100.0% | 96.9% | 18 | 94.4% |
| St. Luke's Magic Valley Medical Center | Twin Falls | wide | 6,542 | 97.9% | 95.7% | 60 | 95.0% |
| St. Luke's Wood River Medical Center | Ketchum | wide | 3,699 | 98.9% | 98.8% | 39 | 97.4% |
| St. Luke's McCall Medical Center | McCall | wide | 3,464 | 100.0% | 98.8% | 36 | 97.2% |
| Saint Alphonsus Medical Center Baker City | Baker City, OR | tall | 65,968 | 100.0% | 94.1% | 877 | 92.5% |

## By pricing method

| method | rates | comparable |
|---|---:|---:|
| flat_dollar | 280,952 | 280,923 |
| percent_of_charges | 74,712 | 74,712 |
| case_rate | 865 | 865 |
| per_diem | 267 | 28 |
| other | 139 | 0 |

## Payer match method

| method | payer/plan strings |
|---|---:|
| alias | 3,488 |
| unresolved | 284 |
| reviewed | 195 |
| ambiguous | 4 |

## Quality flags

| flag | rates |
|---|---:|
| ok | 300,378 |
| billing_class_assumed | 24,765 |
| payer_unresolved | 21,922 |
| above_gross | 3,552 |
| method_inferred | 2,734 |
| pct_derived | 2,705 |
| plan_type_ambiguous | 472 |
| no_amount | 268 |
| method_unknown | 139 |
