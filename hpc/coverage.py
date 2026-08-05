"""Coverage figures for `hpc check` and the reports/coverage.md it writes."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb

RESOLVED = "m.match_method NOT IN ('unresolved', 'ambiguous')"


def measure(con: duckdb.DuckDBPyConnection) -> dict:
    hospitals = con.execute(f"""
        WITH rates AS (
            SELECT hospital_id, count(*) AS rates, count(compare_amount) AS comparable,
                   count(*) FILTER (WHERE pricing_method = 'other') AS method_other
            FROM fact_rate GROUP BY ALL
        ), cross_hosp AS (
            SELECT hospital_id, count(*) AS cross_hospital
            FROM v_rate_compare WHERE group_hospitals >= 2 GROUP BY ALL
        ), names AS (
            SELECT hospital_id, count(*) AS strings,
                   count(*) FILTER (WHERE {RESOLVED}) AS resolved
            FROM map_payer_plan m GROUP BY ALL
        )
        SELECT h.hospital_id, h.hospital_name, h.city, h.encoding,
               coalesce(r.rates, 0) AS rates, coalesce(r.comparable, 0) AS comparable,
               coalesce(c.cross_hospital, 0) AS cross_hospital,
               coalesce(r.method_other, 0) AS method_other,
               coalesce(n.strings, 0) AS strings, coalesce(n.resolved, 0) AS resolved
        FROM dim_hospital h
        LEFT JOIN rates r USING (hospital_id) LEFT JOIN cross_hosp c USING (hospital_id)
        LEFT JOIN names n USING (hospital_id)
        ORDER BY h.loaded_at, h.hospital_id
    """).df()
    rates, comparable, cross = con.execute("""
        SELECT count(*), count(compare_amount),
               (SELECT count(*) FROM v_rate_compare WHERE group_hospitals >= 2)
        FROM fact_rate
    """).fetchone()
    raw_names, strings, resolved, ambiguous, payers, plans = con.execute(f"""
        SELECT count(DISTINCT payer_raw), count(*),
               count(*) FILTER (WHERE {RESOLVED}),
               count(*) FILTER (WHERE m.match_method = 'ambiguous'),
               count(DISTINCT pl.payer_id) FILTER (WHERE {RESOLVED}),
               count(DISTINCT m.plan_id) FILTER (WHERE {RESOLVED})
        FROM map_payer_plan m JOIN dim_plan pl USING (plan_id)
    """).fetchone()
    methods = con.execute("""
        SELECT pricing_method, count(*) AS rates, count(compare_amount) AS comparable
        FROM fact_rate GROUP BY ALL ORDER BY rates DESC
    """).df()
    match = con.execute("""
        SELECT match_method, count(*) AS strings FROM map_payer_plan
        GROUP BY ALL ORDER BY strings DESC
    """).df()
    flags = con.execute("""
        SELECT quality_flag, count(*) AS rates FROM fact_rate
        GROUP BY ALL ORDER BY rates DESC
    """).df()
    share = lambda a, b: a / b if b else 0.0  # noqa: E731
    return {
        "hospitals": hospitals, "methods": methods, "match": match, "flags": flags,
        "totals": {
            "hospitals": len(hospitals), "rates": rates, "comparable": comparable,
            "comparable_share": share(comparable, rates),
            "cross_hospital": cross, "cross_hospital_share": share(cross, rates),
            "raw_names": raw_names, "strings": strings, "resolved": resolved,
            "resolved_share": share(resolved, strings), "ambiguous": ambiguous,
            "payers": payers, "plans": plans,
        },
    }


def _pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "-"


def write_report(cov: dict, path: Path, checks: list[tuple[str, bool, str]],
                 review: tuple[int, int]) -> None:
    t = cov["totals"]
    lines = [
        "# Coverage",
        "",
        f"Written by `hpc check` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.",
        "",
        f"- Hospitals loaded: {t['hospitals']}",
        f"- Rates: {t['rates']:,}",
        f"- Rates with a comparable amount: {t['comparable']:,} ({_pct(t['comparable'], t['rates'])})",
        f"- Rates in a comparison group with at least two hospitals: {t['cross_hospital']:,} "
        f"({_pct(t['cross_hospital'], t['rates'])})",
        f"- Payer names: {t['raw_names']:,} raw names in {t['strings']:,} hospital payer/plan "
        f"strings; {t['resolved']:,} ({_pct(t['resolved'], t['strings'])}) resolved to "
        f"{t['payers']} payers and {t['plans']} payer x plan-type plans, "
        f"{t['ambiguous']} left ambiguous on purpose",
    ]
    agree, reviewed = review
    if reviewed:
        lines.append(f"- Automatic payer rules agree with {agree} of {reviewed} hand-checked "
                     f"rows in data/payer_map.csv ({_pct(agree, reviewed)})")
    lines += ["", "## Checks", "", "| check | result | detail |", "|---|---|---|"]
    lines += [f"| {n} | {'pass' if ok else 'FAIL'} | {d} |" for n, ok, d in checks]
    lines += ["", "## By hospital", "",
              "| hospital | city | encoding | rates | comparable | in a multi-hospital group "
              "| payer/plan strings | resolved |",
              "|---|---|---|---:|---:|---:|---:|---:|"]
    for r in cov["hospitals"].itertuples():
        lines.append(f"| {r.hospital_name} | {r.city} | {r.encoding} | {r.rates:,} "
                     f"| {_pct(r.comparable, r.rates)} | {_pct(r.cross_hospital, r.rates)} "
                     f"| {r.strings:,} | {_pct(r.resolved, r.strings)} |")
    lines += ["", "## By pricing method", "", "| method | rates | comparable |",
              "|---|---:|---:|"]
    lines += [f"| {r.pricing_method} | {r.rates:,} | {r.comparable:,} |"
              for r in cov["methods"].itertuples()]
    lines += ["", "## Payer match method", "", "| method | payer/plan strings |", "|---|---:|"]
    lines += [f"| {r.match_method} | {r.strings:,} |" for r in cov["match"].itertuples()]
    lines += ["", "## Quality flags", "", "| flag | rates |", "|---|---:|"]
    lines += [f"| {r.quality_flag} | {r.rates:,} |" for r in cov["flags"].itertuples()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
