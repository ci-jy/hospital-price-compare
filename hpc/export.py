"""Export the comparison tables to Parquet and a static page.

`hpc export --out docs` writes

    docs/data/<table>.parquet   the tables the Power BI model in report/ reads
    docs/index.html             the same results as one self-contained page

Both come from the same Parquet files, so the page and the report can't
disagree. The tables are a flattened star: `rate` is the fact table and
everything else is a dimension it points at, except `payer_string`, which is a
second fact table (one row per raw payer/plan string a hospital uses).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from hpc.db import connect

log = logging.getLogger("hpc")

TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"

METHOD_LABELS = {
    "flat_dollar": "flat dollar amount",
    "case_rate": "case rate",
    "per_diem": "per diem",
    "percent_of_charges": "percent of billed charges",
    "other": "other (not compared)",
}

# Same wording as the quality-flag table in the README. `compared` says
# whether a rate with this flag keeps a compare amount.
QUALITY_FLAGS = {
    "ok": ("nothing found", True),
    "pct_in_dollar_field": ("percent-of-charges rate written in the dollar column", True),
    "pct_rescaled": ("percentage written as a fraction and scaled to percent", True),
    "pct_derived": ("percentage missing; taken from the notes or dollar / gross charge", True),
    "method_value_mismatch": ("methodology and filled-in value disagree", False),
    "pct_of_fee_schedule": ("percentage of Medicare or another fee schedule", False),
    "method_unknown": ("methodology 'other' with nothing to classify it", False),
    "no_amount": ("no amount to compare", False),
    "zero_amount": ("amount is zero", False),
    "above_gross": ("flat or case rate above the hospital's gross charge", True),
    "pct_over_100": ("percent of charges above 100", True),
    "payer_unresolved": ("payer name not matched to a known payer", True),
    "plan_type_ambiguous": ("plan type left unknown on purpose (a bare 'MA')", True),
    "method_inferred": ("methodology missing, method read from other fields", True),
    "billing_class_assumed": ("file has no billing class; treated as facility", True),
}

# Model table -> SELECT that produces it. Column names here are what the TMDL
# files in report/ point at, so tests/test_pbip.py reads them from Parquet.
TABLES = {
    "hospital": """
        SELECT hospital_id, hospital_name, city, encoding, file_last_updated,
               staged_rows
        FROM dim_hospital ORDER BY hospital_id
    """,
    "service": """
        SELECT service_id, code, code_type, description,
               code || ' ' || coalesce(description, '') AS service_label,
               raw_descriptions
        FROM dim_service ORDER BY code, code_type
    """,
    "plan": """
        SELECT pl.plan_id, p.payer_id, p.payer_name, pl.plan_type
        FROM dim_plan pl JOIN dim_payer p USING (payer_id) ORDER BY pl.plan_id
    """,
    "comparison_group": """
        SELECT comparison_group, code, setting, billing_class, modifiers,
               pricing_method, method_label, compare_unit,
               rates, hospitals, payers, min_amount, median_amount, max_amount
        FROM export_groups ORDER BY comparison_group
    """,
    "rate": """
        SELECT hospital_id, service_id, plan_id, comparison_group,
               setting, billing_class, modifiers, pricing_method, compare_unit,
               gross_charge, negotiated_dollar, negotiated_percentage,
               compare_amount, group_median, group_hospitals, vs_metro_median,
               is_comparable, quality_flag
        FROM export_rate
        ORDER BY comparison_group, hospital_id, plan_id, compare_amount
    """,
    "payer_string": """
        SELECT hospital_id, payer_raw, plan_raw, plan_id, match_method, match_score,
               match_method NOT IN ('unresolved', 'ambiguous') AS is_resolved
        FROM map_payer_plan ORDER BY hospital_id, payer_raw, plan_raw
    """,
    "quality_flag": """
        SELECT quality_flag, meaning, compared FROM export_flags ORDER BY quality_flag
    """,
}


def _prepare(con: duckdb.DuckDBPyConnection) -> None:
    """Temp tables the exported tables are cut from."""
    # Every rate gets its comparison group, including the ones without an
    # amount, so the share of comparable rates can be sliced by group too.
    # The group median and spread come from v_rate_compare, i.e. the same
    # window `hpc compare` and `hpc check` use.
    con.execute("""
        CREATE TEMP TABLE export_rate AS
        WITH f AS (
            SELECT f.*, s.code,
                   coalesce(f.setting, 'unknown') AS setting_k,
                   coalesce(f.billing_class, 'unspecified') AS billing_class_k,
                   coalesce(f.modifiers, '') AS modifiers_k
            FROM fact_rate f JOIN dim_service s USING (service_id)
        ), med AS (
            SELECT DISTINCT comparison_group, group_median, group_hospitals
            FROM v_rate_compare
        )
        SELECT f.hospital_id, f.service_id, f.plan_id,
               concat_ws('|', f.code, f.setting_k, f.billing_class_k, f.modifiers_k,
                         f.pricing_method) AS comparison_group,
               f.setting_k AS setting, f.billing_class_k AS billing_class,
               f.modifiers_k AS modifiers, f.pricing_method, f.compare_unit,
               f.gross_charge, f.negotiated_dollar, f.negotiated_percentage,
               f.compare_amount,
               CASE WHEN f.compare_amount IS NOT NULL THEN m.group_median END AS group_median,
               CASE WHEN f.compare_amount IS NOT NULL THEN m.group_hospitals END
                   AS group_hospitals,
               CASE WHEN f.compare_amount IS NOT NULL AND m.group_hospitals >= 2
                         AND m.group_median > 0
                    THEN f.compare_amount / m.group_median - 1 END AS vs_metro_median,
               f.compare_amount IS NOT NULL AS is_comparable,
               f.quality_flag
        FROM f LEFT JOIN med m
          ON m.comparison_group = concat_ws('|', f.code, f.setting_k, f.billing_class_k,
                                            f.modifiers_k, f.pricing_method)
    """)
    con.execute("CREATE TEMP TABLE export_method_label (pricing_method VARCHAR, "
                "method_label VARCHAR)")
    con.executemany("INSERT INTO export_method_label VALUES (?, ?)",
                    list(METHOD_LABELS.items()))
    con.execute("""
        CREATE TEMP TABLE export_groups AS
        SELECT r.comparison_group, s.code, r.setting, r.billing_class, r.modifiers,
               r.pricing_method,
               coalesce(any_value(l.method_label), r.pricing_method) AS method_label,
               any_value(r.compare_unit) AS compare_unit,
               count(*) AS rates,
               count(DISTINCT r.hospital_id) FILTER (WHERE r.is_comparable) AS hospitals,
               count(DISTINCT p.payer_id) FILTER (WHERE r.is_comparable) AS payers,
               min(r.compare_amount) AS min_amount,
               median(r.compare_amount) AS median_amount,
               max(r.compare_amount) AS max_amount
        FROM export_rate r JOIN dim_service s USING (service_id)
        JOIN dim_plan p USING (plan_id)
        LEFT JOIN export_method_label l USING (pricing_method)
        GROUP BY r.comparison_group, s.code, r.setting, r.billing_class, r.modifiers,
                 r.pricing_method
    """)
    con.execute("CREATE TEMP TABLE export_flags (quality_flag VARCHAR, meaning VARCHAR, "
                "compared BOOLEAN)")
    seen = {f for (f,) in con.execute("SELECT DISTINCT quality_flag FROM fact_rate").fetchall()}
    flags = dict(QUALITY_FLAGS)
    for f in seen - flags.keys():
        flags[f] = ("", True)
    con.executemany("INSERT INTO export_flags VALUES (?, ?, ?)",
                    [(k, m, c) for k, (m, c) in flags.items()])


def export_parquet(con: duckdb.DuckDBPyConnection, data_dir: Path) -> dict[str, int]:
    data_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, sql in TABLES.items():
        path = data_dir / f"{name}.parquet"
        con.execute(f"COPY ({sql}) TO '{path}' (FORMAT parquet, COMPRESSION snappy)")
        counts[name] = con.execute(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]
        log.info("wrote %s (%s rows)", path, f"{counts[name]:,}")
    return counts


def page_data(data_dir: Path, title: str) -> dict:
    """Everything docs/index.html shows, read back from the Parquet files.

    Rates are summarised to one row per comparison group, hospital and payer
    (median of that payer's rates, how many rates, the most common flag) to
    keep the page small; the Parquet files keep every rate.
    """
    con = duckdb.connect()
    for name in TABLES:
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM "
                    f"read_parquet('{data_dir / (name + '.parquet')}')")
    hospitals = con.execute(
        "SELECT hospital_id, hospital_name, city, encoding FROM hospital "
        "ORDER BY hospital_name").fetchall()
    h_index = {h[0]: i for i, h in enumerate(hospitals)}
    payers = [p for (p,) in con.execute(
        "SELECT DISTINCT payer_name FROM plan ORDER BY payer_name").fetchall()]
    p_index = {p: i for i, p in enumerate(payers)}
    services = con.execute("""
        SELECT s.code, any_value(s.description ORDER BY s.raw_descriptions DESC)
        FROM service s JOIN rate r USING (service_id) WHERE r.is_comparable
        GROUP BY s.code ORDER BY s.code
    """).fetchall()
    groups = con.execute("""
        SELECT comparison_group, code, setting, billing_class, modifiers, method_label,
               compare_unit, rates, hospitals, payers, median_amount
        FROM comparison_group WHERE median_amount IS NOT NULL
        ORDER BY code, setting, billing_class, modifiers, pricing_method
    """).fetchall()
    g_index = {g[0]: i for i, g in enumerate(groups)}
    cells = con.execute("""
        SELECT r.comparison_group, r.hospital_id, p.payer_name,
               median(r.compare_amount) AS amount, count(*) AS n,
               mode(r.quality_flag) AS flag
        FROM rate r JOIN plan p USING (plan_id)
        WHERE r.is_comparable
        GROUP BY ALL ORDER BY 1, 4, 2, 3
    """).fetchall()
    flags = sorted({c[5] for c in cells})
    f_index = {f: i for i, f in enumerate(flags)}

    payer_spread = con.execute("""
        SELECT p.payer_name, r.hospital_id, median(r.vs_metro_median) AS spread,
               count(*) AS n
        FROM rate r JOIN plan p USING (plan_id)
        WHERE r.vs_metro_median IS NOT NULL AND r.compare_unit = 'usd'
        GROUP BY ALL HAVING count(*) >= 20
    """).fetchall()
    payer_totals = con.execute("""
        SELECT p.payer_name, median(r.vs_metro_median) AS spread, count(*) AS n,
               count(DISTINCT r.hospital_id) AS hospitals
        FROM rate r JOIN plan p USING (plan_id)
        WHERE r.vs_metro_median IS NOT NULL AND r.compare_unit = 'usd'
        GROUP BY ALL HAVING count(*) >= 100 ORDER BY spread
    """).fetchall()

    quality = con.execute("""
        WITH r AS (
            SELECT hospital_id, count(*) AS rates,
                   count(*) FILTER (WHERE is_comparable) AS comparable,
                   count(*) FILTER (WHERE quality_flag = 'ok') AS ok
            FROM rate GROUP BY ALL
        ), s AS (
            SELECT hospital_id, count(*) AS strings,
                   count(*) FILTER (WHERE is_resolved) AS resolved
            FROM payer_string GROUP BY ALL
        )
        SELECT h.hospital_id, coalesce(r.rates, 0), coalesce(r.comparable, 0),
               coalesce(r.ok, 0), coalesce(s.strings, 0), coalesce(s.resolved, 0)
        FROM hospital h LEFT JOIN r USING (hospital_id) LEFT JOIN s USING (hospital_id)
        ORDER BY h.hospital_name
    """).fetchall()
    flag_counts = con.execute("""
        SELECT r.quality_flag, any_value(q.meaning), any_value(q.compared), count(*)
        FROM rate r LEFT JOIN quality_flag q USING (quality_flag)
        GROUP BY 1 ORDER BY 4 DESC
    """).fetchall()
    method_counts = con.execute("""
        SELECT pricing_method, count(*), count(*) FILTER (WHERE is_comparable)
        FROM rate GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    totals = con.execute("""
        SELECT count(*), count(*) FILTER (WHERE is_comparable),
               count(*) FILTER (WHERE group_hospitals >= 2),
               (SELECT count(*) FROM comparison_group WHERE median_amount IS NOT NULL),
               (SELECT count(*) FILTER (WHERE is_resolved) / count(*) FROM payer_string)
        FROM rate
    """).fetchone()
    con.close()

    r4 = lambda x: None if x is None else round(float(x), 4)  # noqa: E731
    return {
        "title": title,
        "generated": f"{datetime.now(timezone.utc):%Y-%m-%d}",
        "totals": {"rates": totals[0], "comparable": totals[1], "multi_hospital": totals[2],
                   "groups": totals[3], "resolved_share": r4(totals[4] or 0),
                   "hospitals": len(hospitals), "payers": len(payers)},
        "hospitals": [[h[1], h[2], h[3]] for h in hospitals],
        "payers": payers,
        "flags": flags,
        "services": [[c, d] for c, d in services],
        # group: code, setting, billing class, modifiers, method, unit, rates,
        # hospitals, payers, median
        "groups": [list(g[1:7]) + [g[7], g[8], g[9], r4(g[10])] for g in groups],
        # cell: group, hospital, payer, median amount, rates, flag
        "cells": [[g_index[c[0]], h_index[c[1]], p_index[c[2]], r4(c[3]), c[4], f_index[c[5]]]
                  for c in cells if c[0] in g_index],
        "payer_spread": [[p_index[p], h_index[h], r4(s), n] for p, h, s, n in payer_spread],
        "payer_totals": [[p_index[p], r4(s), n, nh] for p, s, n, nh in payer_totals],
        "quality": [[h_index[q[0]]] + list(q[1:]) for q in quality],
        "flag_counts": [[f, m or "", bool(c) if c is not None else True, n]
                        for f, m, c, n in flag_counts],
        "methods": [[METHOD_LABELS.get(m, m), n, c] for m, n, c in method_counts],
    }


def write_page(data: dict, out: Path) -> Path:
    html = TEMPLATE.read_text()
    # </ inside the JSON would end the <script> block early.
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*DATA*/null", payload).replace("{{title}}", data["title"])
    path = out / "index.html"
    path.write_text(html)
    return path


def export(db: Path, out: Path, title: str, memory_limit: str = "1GB",
           threads: int = 2) -> dict[str, int]:
    con = connect(db, memory_limit, threads, read_only=True)
    try:
        _prepare(con)
        counts = export_parquet(con, out / "data")
    finally:
        con.close()
    write_page(page_data(out / "data", title), out)
    return counts
