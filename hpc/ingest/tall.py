"""Tall CSV reader: one row per item x payer x plan already."""
from __future__ import annotations

from pathlib import Path

import duckdb

from hpc.ingest.common import (code_select_sql, col_or_null, read_csv_preamble,
                               read_csv_sql, sql_str)


def load_tall(con: duckdb.DuckDBPyConnection, path: Path, hospital_id: str,
              codes: set[str] | None, encoding: str = "utf-8") -> dict[str, str]:
    meta, header = read_csv_preamble(path)
    code_expr, type_expr = code_select_sql(header)
    c = lambda *names: col_or_null(header, *names)  # noqa: E731
    where = "code IS NOT NULL AND payer_name IS NOT NULL"
    if codes is not None:
        where += " AND code IN (SELECT code FROM tmp_codes)"
    con.execute(f"""
        INSERT INTO stg_rate
        SELECT * FROM (
            SELECT {sql_str(hospital_id)} AS hospital_id,
                   {c('description')} AS description,
                   {code_expr} AS code,
                   {type_expr} AS code_type,
                   lower(trim({c('setting')})) AS setting,
                   nullif(lower(trim({c('billing_class')})), '') AS billing_class,
                   nullif(trim({c('modifiers')}), '') AS modifiers,
                   {c('standard_charge|gross')} AS gross_charge,
                   {c('standard_charge|discounted_cash')} AS cash_price,
                   nullif(trim({c('payer_name')}), '') AS payer_name,
                   nullif(trim({c('plan_name')}), '') AS plan_name,
                   {c('standard_charge|negotiated_dollar')} AS negotiated_dollar,
                   {c('standard_charge|negotiated_percentage')} AS negotiated_percentage,
                   {c('standard_charge|negotiated_algorithm')} AS negotiated_algorithm,
                   {c('estimated_amount', 'median_amount')} AS estimated_amount,
                   lower(trim({c('standard_charge|methodology')})) AS methodology,
                   {c('additional_generic_notes', 'additional_payer_notes')} AS notes
            FROM {read_csv_sql(path, encoding)}
        ) WHERE {where}
    """)
    return meta
