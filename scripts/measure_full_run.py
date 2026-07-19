"""Load every billing code from the configured hospitals and record size, time and memory.

Writes docs/full_run.json. The database goes to $TMPDIR (it is ~1.7 GB) and is
deleted afterwards.

    python3 scripts/measure_full_run.py [--hospitals 5]
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hpc.config import load_metro  # noqa: E402
from hpc.pipeline import ingest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metro", default="config/metro.yaml")
    ap.add_argument("--hospitals", type=int, default=5)
    args = ap.parse_args()

    metro = load_metro(args.metro)
    tmp = Path(tempfile.mkdtemp(prefix="hpc-full-"))
    metro.database = tmp / "full.duckdb"
    started = time.perf_counter()
    report = ingest(metro, args.hospitals, all_codes=True)
    seconds = time.perf_counter() - started

    con = duckdb.connect(str(metro.database), read_only=True)
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    result = {
        "hospitals": report,
        "seconds_total": round(seconds, 1),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "duckdb_memory_limit": metro.memory_limit,
        "rates": q("SELECT count(*) FROM fact_rate"),
        "comparable_rates": q("SELECT count(compare_amount) FROM fact_rate"),
        "comparison_groups": q("SELECT count(*) FROM v_compare_summary"),
        "mixed_method_groups": q("SELECT count(*) FROM v_compare_summary WHERE methods_in_group > 1"),
        "billing_codes": q("SELECT count(*) FROM dim_service"),
        "raw_payer_names": q("SELECT count(DISTINCT payer_raw) FROM map_payer_plan"),
        "raw_payer_plan_pairs": q("SELECT count(*) FROM map_payer_plan"),
        "payers": q("SELECT count(*) FROM dim_payer"),
        "plans": q("SELECT count(*) FROM dim_plan"),
        "unresolved_pairs": q("SELECT count(*) FROM map_payer_plan WHERE match_method = 'unresolved'"),
        "quality_flags": dict(con.execute(
            "SELECT quality_flag, count(*) FROM fact_rate GROUP BY 1 ORDER BY 2 DESC").fetchall()),
        "pricing_methods": dict(con.execute(
            "SELECT pricing_method, count(*) FROM fact_rate GROUP BY 1 ORDER BY 2 DESC").fetchall()),
    }
    con.close()
    for f in tmp.iterdir():
        if f.is_file():
            f.unlink()
    out = Path("docs/full_run.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "hospitals"}, indent=2))


if __name__ == "__main__":
    os.environ.setdefault("HPC_SPILL_DIR", tempfile.gettempdir())
    main()
