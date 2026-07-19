"""Command line: python -m hpc {discover,ingest,check,compare,payers}."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from hpc import discover as disc
from hpc.config import load_metro
from hpc.db import connect

DEFAULT_METRO = "config/metro.yaml"

METHOD_LABELS = {
    "flat_dollar": "flat dollar amount",
    "case_rate": "case rate",
    "per_diem": "per diem",
    "percent_of_charges": "percent of billed charges",
}


def _db(args) -> Path:
    return Path(args.db) if args.db else load_metro(args.metro).database


def cmd_discover(args) -> int:
    metro = load_metro(args.metro)
    rows = disc.discover(metro, metro.hospitals[: args.hospitals or None])
    out = metro.root / "data" / "sources.csv"
    disc.write_sources(rows, out)
    for r in rows:
        print(f"{r['hospital_id']:<24} {r['mrf_url'].split('?')[0]}")
    print(f"wrote {out}")
    return 0


def cmd_ingest(args) -> int:
    from hpc.pipeline import ingest

    metro = load_metro(args.metro)
    if args.db:
        metro.database = Path(args.db)
    report = ingest(metro, args.hospitals, all_codes=args.all_codes)
    con = connect(metro.database, metro.memory_limit, metro.threads, read_only=True)
    facts, compared = con.execute(
        "SELECT count(*), count(compare_amount) FROM fact_rate").fetchone()
    con.close()
    total = sum(r["rows"] for r in report)
    print(f"staged {total:,} rows from {len(report)} hospitals; "
          f"{facts:,} rates in fact_rate, {compared:,} comparable")
    return 0


def cmd_check(args) -> int:
    con = connect(_db(args), read_only=True)
    checks: list[tuple[str, bool, str]] = []

    staged = con.execute("SELECT count(*) FROM stg_rate").fetchone()[0]
    facts = con.execute("SELECT count(*) FROM fact_rate").fetchone()[0]
    checks.append(("rates loaded", facts > 0, f"{facts:,} rates"))
    checks.append(("every staged row became a rate", staged == facts,
                   f"{staged:,} staged vs {facts:,} rates"))

    hospitals = con.execute(
        "SELECT count(DISTINCT hospital_id) FROM fact_rate WHERE compare_amount IS NOT NULL"
    ).fetchone()[0]
    checks.append(("hospital coverage", hospitals >= args.min_hospitals,
                   f"{hospitals} hospitals with comparable rates (need {args.min_hospitals})"))
    encodings = con.execute("SELECT count(DISTINCT encoding) FROM dim_hospital").fetchone()[0]
    checks.append(("encodings", encodings >= args.min_encodings,
                   f"{encodings} file encodings loaded (need {args.min_encodings})"))

    mixed = con.execute("""
        SELECT count(*) FROM v_compare_summary
        WHERE methods_in_group <> 1 OR units_in_group <> 1
    """).fetchone()[0]
    groups = con.execute("SELECT count(*) FROM v_compare_summary").fetchone()[0]
    checks.append(("no comparison mixes pricing methods", mixed == 0 and groups > 0,
                   f"{groups:,} comparison groups, {mixed} mixed"))
    unit_mismatch = con.execute("""
        SELECT count(*) FROM fact_rate
        WHERE (pricing_method = 'percent_of_charges') <> (compare_unit = 'pct_of_charges')
    """).fetchone()[0]
    checks.append(("percent rates never in dollars", unit_mismatch == 0,
                   f"{unit_mismatch} rates with the wrong unit"))
    unflagged = con.execute(
        "SELECT count(*) FROM fact_rate WHERE quality_flag IS NULL OR quality_flag = ''"
    ).fetchone()[0]
    checks.append(("every rate has a quality flag", unflagged == 0, f"{unflagged} missing"))
    orphans = con.execute("""
        SELECT count(*) FROM fact_rate f
        LEFT JOIN dim_plan p USING (plan_id) LEFT JOIN dim_service s USING (service_id)
        WHERE p.plan_id IS NULL OR s.service_id IS NULL
    """).fetchone()[0]
    checks.append(("facts join to dimensions", orphans == 0, f"{orphans} orphan rates"))

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    con.close()
    return 0 if all(ok for _, ok, _ in checks) else 1


def _fmt_amount(value: float, unit: str) -> str:
    return f"{value:.1f}% of charges" if unit == "pct_of_charges" else f"${value:,.2f}"


def cmd_compare(args) -> int:
    con = connect(_db(args), read_only=True)
    where = ["code = ?"]
    params: list = [args.code]
    if args.setting:
        where.append("setting = ?")
        params.append(args.setting)
    rows = con.execute(f"""
        SELECT setting, billing_class, modifiers, pricing_method, compare_unit,
               hospital_name, payer_name, plan_type,
               compare_amount, quality_flag,
               count(*) AS raw_rows,
               count(DISTINCT payer_raw || '|' || plan_raw) AS raw_plans,
               any_value(group_median) AS group_median,
               any_value(group_hospitals) AS group_hospitals,
               any_value(service) AS service
        FROM v_rate_compare
        WHERE {' AND '.join(where)}
        GROUP BY ALL
        ORDER BY setting, billing_class, modifiers, pricing_method, compare_amount,
                 hospital_name, payer_name
    """, params).df()
    con.close()
    if rows.empty:
        print(f"no comparable rates for code {args.code}")
        return 1
    if args.format == "csv":
        rows.drop(columns=["service"]).to_csv(sys.stdout, index=False)
        return 0

    print(f"{args.code}  {rows['service'].iloc[0]}")
    print("Rates are only compared within one setting, billing class, modifier set "
          "and pricing method.\n")
    keys = ["setting", "billing_class", "modifiers", "pricing_method"]
    for (setting, bclass, mods, method), grp in rows.groupby(keys, sort=False):
        unit = grp["compare_unit"].iloc[0]
        med = grp["group_median"].iloc[0]
        mod = f" | modifier {mods}" if mods else ""
        print(f"== {setting} | {bclass}{mod} | {METHOD_LABELS.get(method, method)} | "
              f"{grp['group_hospitals'].iloc[0]} hospitals, median {_fmt_amount(med, unit)}")
        print(f"   {'hospital':<40} {'payer':<36} {'plan type':<18} "
              f"{'rate':>22} {'vs median':>9}  {'plans':>5}  flag")
        shown = grp if args.limit <= 0 else grp.head(args.limit)
        for r in shown.itertuples():
            rel = (r.compare_amount / med - 1) * 100 if med else 0.0
            print(f"   {r.hospital_name[:40]:<40} {r.payer_name[:36]:<36} {r.plan_type:<18} "
                  f"{_fmt_amount(r.compare_amount, unit):>22} {rel:>+8.0f}%  "
                  f"{r.raw_plans:>5}  {r.quality_flag}")
        if len(grp) > len(shown):
            print(f"   ... {len(grp) - len(shown)} more (use --limit 0)")
        print()
    return 0


def cmd_payers(args) -> int:
    con = connect(_db(args), read_only=True)
    df = con.execute("""
        SELECT m.hospital_id, m.payer_raw, m.plan_raw, p.payer_name, pl.plan_type,
               m.match_method, m.match_score
        FROM map_payer_plan m JOIN dim_plan pl USING (plan_id)
        JOIN dim_payer p ON p.payer_id = pl.payer_id
        ORDER BY m.match_method DESC, p.payer_name, m.hospital_id, m.payer_raw
    """).df()
    con.close()
    if args.unresolved:
        df = df[df["match_method"].isin(["unresolved", "fuzzy"])]
    out = args.out
    if out:
        df.to_csv(out, index=False)
        print(f"wrote {len(df)} rows to {out}")
    else:
        with __import__("pandas").option_context("display.max_rows", None,
                                                  "display.width", 200):
            print(df.to_string(index=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hpc", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, hospitals=False):
        sp.add_argument("--metro", default=DEFAULT_METRO, help="metro config YAML")
        sp.add_argument("--db", help="DuckDB file (default: from the metro config)")
        if hospitals:
            sp.add_argument("--hospitals", type=int, default=None,
                            help="only the first N hospitals in the config")

    sp = sub.add_parser("discover", help="read cms-hpt.txt files into data/sources.csv")
    common(sp, hospitals=True)
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("ingest", help="download, stage and normalise hospital files")
    common(sp, hospitals=True)
    sp.add_argument("--all-codes", action="store_true",
                    help="keep every billing code, not just config codes_file")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("check", help="assert row counts, coverage and method separation")
    common(sp)
    sp.add_argument("--min-hospitals", type=int, default=1)
    sp.add_argument("--min-encodings", type=int, default=1)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("compare", help="rates for one CPT/HCPCS code by hospital and payer")
    common(sp)
    sp.add_argument("--code", required=True)
    sp.add_argument("--setting", choices=["inpatient", "outpatient", "both"])
    sp.add_argument("--limit", type=int, default=25, help="rows per group, 0 for all")
    sp.add_argument("--format", choices=["table", "csv"], default="table")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("payers", help="list how raw payer/plan names were resolved")
    common(sp)
    sp.add_argument("--unresolved", action="store_true", help="only fuzzy/unresolved rows")
    sp.add_argument("--out", help="write CSV here for review")
    sp.set_defaults(func=cmd_payers)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    return args.func(args)
