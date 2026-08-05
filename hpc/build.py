"""Build the star schema from stg_rate."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from hpc import method
from hpc.db import SQL_DIR
from hpc.resolve import PayerResolver


def build_services(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("DELETE FROM dim_service")
    con.execute("""
        INSERT INTO dim_service
        SELECT row_number() OVER (ORDER BY code_type, code)::INTEGER,
               code, code_type, mode(trim(description)),
               count(DISTINCT trim(description))::INTEGER
        FROM stg_rate
        WHERE code IS NOT NULL AND code_type IS NOT NULL
        GROUP BY code, code_type
    """)


def build_payers(con: duckdb.DuckDBPyConnection, resolver: PayerResolver) -> pd.DataFrame:
    raw = con.execute("""
        SELECT DISTINCT hospital_id, coalesce(payer_name, '') AS payer_raw,
               coalesce(plan_name, '') AS plan_raw
        FROM stg_rate ORDER BY ALL
    """).df()
    resolved = [resolver.resolve(r.payer_raw, r.plan_raw) for r in raw.itertuples()]
    raw["payer_name"] = [r.payer_name for r in resolved]
    raw["plan_type"] = [r.plan_type for r in resolved]
    raw["match_method"] = [r.method for r in resolved]
    raw["match_score"] = [r.score for r in resolved]

    payers = sorted(raw["payer_name"].unique())
    payer_ids = {name: i + 1 for i, name in enumerate(payers)}
    plans = sorted({(p, t) for p, t in zip(raw["payer_name"], raw["plan_type"])})
    plan_ids = {key: i + 1 for i, key in enumerate(plans)}
    raw["plan_id"] = [plan_ids[(p, t)] for p, t in zip(raw["payer_name"], raw["plan_type"])]

    dim_payer = pd.DataFrame({"payer_id": list(payer_ids.values()),
                              "payer_name": list(payer_ids.keys())})
    dim_plan = pd.DataFrame([(pid, payer_ids[p], t) for (p, t), pid in plan_ids.items()],
                            columns=["plan_id", "payer_id", "plan_type"])
    mapping = raw[["hospital_id", "payer_raw", "plan_raw", "plan_id",
                   "match_method", "match_score"]]
    for table in ("map_payer_plan", "dim_plan", "dim_payer"):
        con.execute(f"DELETE FROM {table}")
    con.register("tmp_df", dim_payer)
    con.execute("INSERT INTO dim_payer SELECT payer_id::INTEGER, payer_name FROM tmp_df")
    con.register("tmp_df", dim_plan)
    con.execute("INSERT INTO dim_plan SELECT plan_id::INTEGER, payer_id::INTEGER, plan_type FROM tmp_df")
    con.register("tmp_df", mapping)
    con.execute("""INSERT INTO map_payer_plan
                   SELECT hospital_id, payer_raw, plan_raw, plan_id::INTEGER,
                          match_method, match_score FROM tmp_df""")
    con.unregister("tmp_df")
    return raw


def build_facts(con: duckdb.DuckDBPyConnection) -> None:
    method.create_view(con)
    con.execute((SQL_DIR / "normalize.sql").read_text())


def rebuild(con: duckdb.DuckDBPyConnection, resolver: PayerResolver) -> pd.DataFrame:
    """Rebuild every table from stg_rate; returns the payer resolution rows."""
    # Facts reference the dimensions, so clear them first.
    con.execute("DELETE FROM fact_rate")
    build_services(con)
    mapping = build_payers(con, resolver)
    build_facts(con)
    return mapping


def default_resolver(root: Path) -> PayerResolver:
    return PayerResolver.from_files(root / "config" / "payers.yaml",
                                    root / "data" / "payer_map.csv")
