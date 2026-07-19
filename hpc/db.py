"""DuckDB connection with the memory cap applied."""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def connect(path: str | Path, memory_limit: str = "1GB", threads: int = 2,
            read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET threads = {int(threads)}")
    con.execute("SET preserve_insertion_order = false")
    spill = Path(os.environ.get("HPC_SPILL_DIR", path.parent / ".duckdb_tmp"))
    con.execute(f"SET temp_directory = '{spill}'")
    return con


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute((SQL_DIR / "schema.sql").read_text())
