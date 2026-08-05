"""Wide CSV reader.

A wide file has one row per item and, for every payer|plan pair, a group of
columns such as::

    standard_charge|AETNA [1021]|HB NAMPA BP AETNA|negotiated_dollar
    standard_charge|AETNA [1021]|HB NAMPA BP AETNA|negotiated_percentage
    standard_charge|AETNA [1021]|HB NAMPA BP AETNA|methodology
    additional_payer_notes|AETNA [1021]|HB NAMPA BP AETNA

The rows are filtered to the wanted codes first, then the payer columns are
UNPIVOTed into (column, value) pairs, split on '|' and pivoted back so each
payer|plan becomes one tall row.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from hpc.ingest.common import (code_select_sql, col_or_null, ident, read_csv_preamble,
                               read_csv_sql, sql_str)

PAYER_FIELDS = ("negotiated_dollar", "negotiated_percentage", "negotiated_algorithm",
                "methodology")


def payer_columns(header: list[str]) -> list[str]:
    cols = []
    for h in header:
        parts = h.split("|")
        if parts[0].lower() == "standard_charge" and len(parts) == 4 \
                and parts[3].lower() in PAYER_FIELDS:
            cols.append(h)
        elif parts[0].lower() in ("additional_payer_notes", "median_amount",
                                  "estimated_amount") and len(parts) == 3:
            cols.append(h)
    return cols


def load_wide(con: duckdb.DuckDBPyConnection, path: Path, hospital_id: str,
              codes: set[str] | None, encoding: str = "utf-8") -> dict[str, str]:
    meta, header = read_csv_preamble(path)
    code_expr, type_expr = code_select_sql(header)
    c = lambda *names: col_or_null(header, *names)  # noqa: E731
    pcols = payer_columns(header)
    if not pcols:
        raise ValueError(f"{path.name}: wide file without payer columns")
    where = "code IS NOT NULL"
    if codes is not None:
        where += " AND code IN (SELECT code FROM tmp_codes)"
    payer_select = ", ".join(ident(p) for p in pcols)
    con.execute("DROP TABLE IF EXISTS tmp_wide")
    con.execute(f"""
        CREATE TEMP TABLE tmp_wide AS
        SELECT row_number() OVER () AS rid, * FROM (
            SELECT {c('description')} AS description,
                   {code_expr} AS code,
                   {type_expr} AS code_type,
                   lower(trim({c('setting')})) AS setting,
                   nullif(lower(trim({c('billing_class')})), '') AS billing_class,
                   nullif(trim({c('modifiers')}), '') AS modifiers,
                   {c('standard_charge|gross')} AS gross_charge,
                   {c('standard_charge|discounted_cash')} AS cash_price,
                   {payer_select}
            FROM {read_csv_sql(path, encoding)}
        ) WHERE {where}
    """)
    con.execute(f"""
        INSERT INTO stg_rate
        WITH long AS (
            UNPIVOT (SELECT rid, {payer_select} FROM tmp_wide)
            ON {payer_select}
            INTO NAME col VALUE val
        ), split AS (
            SELECT rid, string_split(col, '|') AS p, val
            FROM long WHERE trim(val) <> ''
        ), keyed AS (
            SELECT rid, trim(p[2]) AS payer_name, trim(p[3]) AS plan_name,
                   CASE WHEN lower(p[1]) = 'standard_charge' THEN lower(p[4])
                        WHEN lower(p[1]) = 'additional_payer_notes' THEN 'notes'
                        ELSE 'estimated_amount' END AS field,
                   val
            FROM split
        ), per_payer AS (
            SELECT rid, payer_name, plan_name,
                   max(val) FILTER (WHERE field = 'negotiated_dollar') AS negotiated_dollar,
                   max(val) FILTER (WHERE field = 'negotiated_percentage') AS negotiated_percentage,
                   max(val) FILTER (WHERE field = 'negotiated_algorithm') AS negotiated_algorithm,
                   max(val) FILTER (WHERE field = 'estimated_amount') AS estimated_amount,
                   max(val) FILTER (WHERE field = 'methodology') AS methodology,
                   max(val) FILTER (WHERE field = 'notes') AS notes
            FROM keyed GROUP BY ALL
        )
        SELECT {sql_str(hospital_id)}, w.description, w.code, w.code_type, w.setting,
               w.billing_class, w.modifiers, w.gross_charge, w.cash_price,
               p.payer_name, p.plan_name, p.negotiated_dollar, p.negotiated_percentage,
               p.negotiated_algorithm, p.estimated_amount, lower(trim(p.methodology)),
               p.notes
        FROM per_payer p JOIN tmp_wide w USING (rid)
        WHERE coalesce(p.negotiated_dollar, p.negotiated_percentage,
                       p.negotiated_algorithm) IS NOT NULL
    """)
    con.execute("DROP TABLE tmp_wide")
    return meta
