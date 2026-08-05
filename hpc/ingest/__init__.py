"""Readers for the three CMS hospital price-transparency encodings."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from hpc.ingest.detect import detect_format, extract_zip
from hpc.ingest.json_reader import load_json
from hpc.ingest.tall import load_tall
from hpc.ingest.wide import load_wide

READERS = {"tall": load_tall, "wide": load_wide, "json": load_json}


def stage_file(con: duckdb.DuckDBPyConnection, path: Path, hospital_id: str,
               codes: set[str] | None = None) -> tuple[str, dict[str, str], int]:
    """Replace a hospital's rows in stg_rate with the contents of `path`.

    Returns (encoding, file metadata, rows staged).
    """
    path = Path(path)
    fmt = detect_format(path)
    if fmt == "zip":
        path = extract_zip(path, path.with_suffix(""))
        fmt = detect_format(path)
    con.execute("DELETE FROM stg_rate WHERE hospital_id = ?", [hospital_id])
    con.execute("DROP TABLE IF EXISTS tmp_codes")
    con.execute("CREATE TEMP TABLE tmp_codes (code VARCHAR)")
    if codes is not None:
        con.register("tmp_codes_df", pd.DataFrame({"code": sorted(codes)}))
        con.execute("INSERT INTO tmp_codes SELECT code FROM tmp_codes_df")
        con.unregister("tmp_codes_df")
    try:
        meta = READERS[fmt](con, path, hospital_id, codes)
    except duckdb.InvalidInputException as exc:
        # A few files are Windows-1252/Latin-1 (Treasure Valley writes
        # "G0465?" with a stray 0xA0); DuckDB stops at the first bad byte.
        if fmt == "json" or "utf-8" not in str(exc).lower():
            raise
        con.execute("DELETE FROM stg_rate WHERE hospital_id = ?", [hospital_id])
        meta = READERS[fmt](con, path, hospital_id, codes, encoding="latin-1")
    n = con.execute("SELECT count(*) FROM stg_rate WHERE hospital_id = ?",
                    [hospital_id]).fetchone()[0]
    return fmt, meta, n
