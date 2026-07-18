"""JSON reader: streams standard_charge_information items with ijson.

The JSON files run to hundreds of megabytes as a single object, so loading
them with json.load (or DuckDB's read_json) would hold the whole document in
memory. ijson yields one item at a time; rows are flushed to DuckDB in
batches through a pandas DataFrame.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import ijson
import pandas as pd

from hpc.ingest.common import COMPARABLE_CODE_TYPES, STG_COLUMNS

BATCH_ROWS = 50_000
META_KEYS = ("hospital_name", "last_updated_on", "version")


def _s(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return " | ".join(str(v) for v in value)
    text = str(value).strip()
    return text or None


def pick_code(code_information: list[dict]) -> tuple[str | None, str | None]:
    for ci in code_information or []:
        ctype = str(ci.get("type", "")).strip().upper()
        if ctype in COMPARABLE_CODE_TYPES:
            return str(ci.get("code", "")).strip(), ctype
    return None, None


def item_rows(item: dict, hospital_id: str, codes: set[str] | None):
    code, ctype = pick_code(item.get("code_information"))
    if not code or (codes is not None and code not in codes):
        return
    for sc in item.get("standard_charges") or []:
        for p in sc.get("payers_information") or []:
            yield (
                hospital_id, _s(item.get("description")), code, ctype,
                (_s(sc.get("setting")) or "").lower() or None,
                (_s(sc.get("billing_class")) or "").lower() or None,
                _s(sc.get("modifier_code") or sc.get("modifiers")),
                _s(sc.get("gross_charge")), _s(sc.get("discounted_cash")),
                _s(p.get("payer_name")), _s(p.get("plan_name")),
                _s(p.get("standard_charge_dollar")),
                _s(p.get("standard_charge_percentage")),
                _s(p.get("standard_charge_algorithm")),
                _s(p.get("estimated_amount") or p.get("median_amount")),
                (_s(p.get("methodology")) or "").lower() or None,
                _s(p.get("additional_payer_notes")),
            )


def read_json_meta(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    with open(path, "rb") as f:
        for prefix, event, value in ijson.parse(f):
            if prefix in META_KEYS and event in ("string", "number"):
                meta[prefix] = str(value)
            if prefix == "standard_charge_information" or len(meta) == len(META_KEYS):
                break
    return meta


def load_json(con: duckdb.DuckDBPyConnection, path: Path, hospital_id: str,
              codes: set[str] | None) -> dict[str, str]:
    batch: list[tuple] = []

    def flush():
        if batch:
            df = pd.DataFrame(batch, columns=STG_COLUMNS, dtype="object")
            con.register("tmp_json_batch", df)
            con.execute("INSERT INTO stg_rate SELECT * FROM tmp_json_batch")
            con.unregister("tmp_json_batch")
            batch.clear()

    with open(path, "rb") as f:
        # use_float keeps numbers as plain floats instead of Decimal
        for item in ijson.items(f, "standard_charge_information.item", use_float=True):
            batch.extend(item_rows(item, hospital_id, codes))
            if len(batch) >= BATCH_ROWS:
                flush()
    flush()
    return read_json_meta(path)
