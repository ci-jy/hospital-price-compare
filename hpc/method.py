"""Classify each staged rate's pricing method and work out what to compare.

The CMS `standard_charge|methodology` field is the starting point, but it is
missing in some files, set to `other` in others, and sometimes disagrees with
the numbers next to it. So each rate is classified in two steps:

1. method: the methodology field when it is one of the four CMS values,
   otherwise read from the algorithm text ("per diem rate of $2,500",
   "65% of billed charges"), otherwise from which number is filled in.
2. value checks: a percent-of-charges rate whose percentage sits in the
   dollar column ("$0.76"), a fraction written for a percentage (0.76), a
   missing percentage that can be read from the notes or from dollar / gross
   charge, a percentage of a *fee schedule* (Medicare, APC) that must not be
   read as a percentage of charges, and a methodology that names a dollar
   method while only a percentage is filled in.

Methods: flat_dollar, case_rate, per_diem, percent_of_charges, and `other`,
which is never compared. The SQL lives here so the same rules run inside the
pipeline (`classified_sql`) and on hand-made rows in tests (`classify`).
"""
from __future__ import annotations

import duckdb
import pandas as pd

METHODS = ("flat_dollar", "case_rate", "per_diem", "percent_of_charges", "other")

# methodology values from the CMS template, lower-cased
CMS_METHODOLOGIES = {
    "fee schedule": "flat_dollar",
    "case rate": "case_rate",
    "per diem": "per_diem",
    "percent of total billed charges": "percent_of_charges",
}

# Input columns `classify` accepts; they match stg_rate.
INPUT_COLUMNS = ("setting", "gross_charge", "negotiated_dollar", "negotiated_percentage",
                 "negotiated_algorithm", "methodology", "notes")

_PARSE_NUM = """CREATE OR REPLACE MACRO parse_num(x) AS
    TRY_CAST(nullif(regexp_replace(trim(x), '[$,%\\s]', '', 'g'), '') AS DOUBLE)"""

# Text that means "a percentage of some fee schedule", not of billed charges.
_FEE_SCHEDULE_TEXT = ("\\b(medicare|mcare|mcr|medicaid|fee schedule|apc|drg|asc|rbrvs|"
                      "opps|ipps|grouper|base rate|pricer)\\b")


def classified_sql(source: str) -> str:
    """SELECT over `source` (stg_rate columns) adding method, amount and flags.

    Output columns, beyond the source's own: d_raw, pct_raw, gross,
    m (methodology), pricing_method, method_inferred, dollar, pct, amount and
    the boolean checks pct_in_dollar, pct_fraction, pct_derived,
    pct_of_fee_schedule, method_value_mismatch.
    """
    return f"""
WITH parsed AS (
    SELECT s.*,
           parse_num(s.negotiated_dollar) AS d_raw,
           parse_num(s.negotiated_percentage) AS pct_raw,
           parse_num(s.gross_charge) AS gross,
           coalesce(lower(trim(s.methodology)), '') AS m,
           coalesce(lower(s.negotiated_algorithm), '') AS alg
    FROM {source} s
),
checked AS (
    SELECT *,
        -- "120% of Medicare", "APC rate x 135%": a percentage, but of a
        -- fee schedule, so it can't sit next to percent-of-charges rates.
        pct_raw IS NOT NULL AND d_raw IS NULL
            AND regexp_matches(alg || ' ' || lower(coalesce(notes, '')),
                               '%\\s*(of\\s+)?(the\\s+)?(current\\s+)?{_FEE_SCHEDULE_TEXT}'
                               || '|{_FEE_SCHEDULE_TEXT}[^%]*\\d\\s*%')
            AND m <> 'percent of total billed charges' AS pct_of_fee_schedule,
        -- methodology names a dollar method but only a percentage is given
        m IN ('fee schedule', 'case rate', 'per diem')
            AND d_raw IS NULL AND pct_raw IS NOT NULL AS method_value_mismatch
    FROM parsed
),
classified AS (
    SELECT *,
        CASE
            WHEN method_value_mismatch OR pct_of_fee_schedule THEN 'other'
            WHEN m LIKE '%per diem%' THEN 'per_diem'
            WHEN m LIKE '%case rate%' THEN 'case_rate'
            WHEN m LIKE '%percent%' THEN 'percent_of_charges'
            WHEN m = 'fee schedule' THEN 'flat_dollar'
            WHEN alg LIKE '%per diem%' OR alg LIKE '%per day%'
                 OR alg LIKE '%daily rate%' THEN 'per_diem'
            WHEN alg LIKE '%case rate%' OR alg LIKE '%per case%'
                 OR alg LIKE '%per admission%' THEN 'case_rate'
            WHEN pct_raw IS NOT NULL AND d_raw IS NULL THEN 'percent_of_charges'
            WHEN d_raw IS NULL AND (alg LIKE '%percent%' OR alg LIKE '%\\% of%' ESCAPE '\\'
                                    OR alg LIKE '%billed charges%') THEN 'percent_of_charges'
            WHEN d_raw IS NOT NULL THEN 'flat_dollar'
            ELSE 'other'
        END AS pricing_method,
        m NOT IN ('fee schedule', 'case rate', 'per diem',
                  'percent of total billed charges') AS method_inferred
    FROM checked
),
repaired AS (
    -- Percent-of-charges rates where the percentage column is empty. The
    -- number is recovered, in order, from text like "79.1% of billable gross
    -- charges" in the notes, from dollar / gross charge, or - when there is
    -- no usable gross charge and the dollar value is at most 1 (a "$0.76"
    -- that can only be a fraction) - from the dollar column itself.
    SELECT *,
        TRY_CAST(nullif(regexp_extract(coalesce(notes, '') || ' ' || alg,
                                       '(\\d+(?:\\.\\d+)?)\\s*%', 1), '') AS DOUBLE) AS pct_text,
        CASE WHEN gross > 0 THEN d_raw / gross * 100 END AS pct_ratio,
        pricing_method = 'percent_of_charges' AND pct_raw IS NULL AS pct_missing
    FROM classified
),
amounts AS (
    SELECT *,
        pct_missing AND pct_text IS NULL AND d_raw > 0 AND d_raw <= 1
            AND (pct_ratio IS NULL OR pct_ratio < 1) AS pct_in_dollar,
        pricing_method = 'percent_of_charges' AND pct_raw > 0 AND pct_raw <= 1 AS pct_fraction,
        pct_missing AND (pct_text IS NOT NULL OR pct_ratio >= 1) AS pct_derived
    FROM repaired
),
vals AS (
    SELECT *,
        CASE WHEN pct_in_dollar THEN NULL ELSE d_raw END AS dollar,
        CASE WHEN pct_in_dollar THEN d_raw * 100
             WHEN pct_fraction THEN pct_raw * 100
             WHEN pct_missing AND pct_text IS NOT NULL THEN pct_text
             WHEN pct_missing AND pct_ratio >= 1 THEN round(pct_ratio, 2)
             ELSE pct_raw END AS pct
    FROM amounts
)
SELECT *,
    CASE WHEN pricing_method = 'percent_of_charges' THEN pct ELSE dollar END AS amount
FROM vals
"""


def create_view(con: duckdb.DuckDBPyConnection, source: str = "stg_rate",
                view: str = "rate_classified") -> None:
    con.execute(_PARSE_NUM)
    con.execute(f"CREATE OR REPLACE TEMP VIEW {view} AS {classified_sql(source)}")


def classify(rows: list[dict]) -> pd.DataFrame:
    """Run the classifier on plain dicts (keys from INPUT_COLUMNS).

    Returns one row per input with pricing_method, amount, compare_unit and
    the value-check booleans. Handy for tests and for checking a single file
    row by hand.
    """
    df = pd.DataFrame([{c: r.get(c) for c in INPUT_COLUMNS} for r in rows],
                      columns=list(INPUT_COLUMNS)).astype(object)
    df["row_no"] = range(len(df))
    con = duckdb.connect()
    try:
        con.register("input_df", df)
        con.execute("CREATE TEMP TABLE input_rows AS SELECT "
                    + ", ".join(f"CAST({c} AS VARCHAR) AS {c}" for c in INPUT_COLUMNS)
                    + ", row_no FROM input_df")
        create_view(con, "input_rows")
        out = con.execute("""
            SELECT pricing_method, amount,
                   CASE WHEN pricing_method = 'percent_of_charges'
                        THEN 'pct_of_charges' ELSE 'usd' END AS compare_unit,
                   COLUMNS(c -> c IN ('method_inferred', 'pct_in_dollar', 'pct_fraction',
                                      'pct_derived', 'pct_of_fee_schedule',
                                      'method_value_mismatch')) IS TRUE
            FROM rate_classified ORDER BY row_no
        """).df()
    finally:
        con.close()
    return out
