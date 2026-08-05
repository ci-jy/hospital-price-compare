"""Shared pieces for the three readers."""
from __future__ import annotations

import csv
from pathlib import Path

# Code types we compare on. CDM (chargemaster) and RC (revenue code) are
# hospital-internal and never comparable across hospitals.
COMPARABLE_CODE_TYPES = ("CPT", "HCPCS", "MS-DRG", "APR-DRG")

# Column order of the stg_rate table in sql/schema.sql.
STG_COLUMNS = [
    "hospital_id", "description", "code", "code_type", "setting", "billing_class",
    "modifiers", "gross_charge", "cash_price", "payer_name", "plan_name",
    "negotiated_dollar", "negotiated_percentage", "negotiated_algorithm",
    "estimated_amount", "methodology", "notes",
]


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def read_csv_preamble(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return (metadata from rows 1-2, header names from row 3)."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        r = csv.reader(f)
        keys, values, header = next(r), next(r), next(r)
    meta = {k.strip().lower(): v.strip() for k, v in zip(keys, values) if k.strip()}
    return meta, header


def norm_name(name: str) -> str:
    """Lower-case a header name and drop spaces around '|' separators.

    Most files write ``standard_charge|gross``; Weiser Memorial writes
    ``standard_charge | gross``.
    """
    return "|".join(part.strip() for part in name.lower().split("|"))


def code_columns(header: list[str]) -> list[tuple[str, str]]:
    """Pairs of (code column, code-type column) present in a CSV header."""
    lower = {norm_name(h): h for h in header}
    pairs = []
    for i in range(1, 10):
        c, t = lower.get(f"code|{i}"), lower.get(f"code|{i}|type")
        if c and t:
            pairs.append((c, t))
    return pairs


def code_select_sql(header: list[str]) -> tuple[str, str]:
    """SQL expressions picking the first comparable (code, code_type) on a row."""
    types = ", ".join(sql_str(t) for t in COMPARABLE_CODE_TYPES)
    code_cases, type_cases = [], []
    for c, t in code_columns(header):
        cond = f"upper(trim({ident(t)})) IN ({types})"
        code_cases.append(f"WHEN {cond} THEN trim({ident(c)})")
        type_cases.append(f"WHEN {cond} THEN upper(trim({ident(t)}))")
    if not code_cases:
        raise ValueError("no code|N / code|N|type columns in header")
    return ("CASE " + " ".join(code_cases) + " END",
            "CASE " + " ".join(type_cases) + " END")


def col_or_null(header: list[str], *names: str) -> str:
    lower = {norm_name(h): h for h in header}
    for n in names:
        if n in lower:
            return ident(lower[n])
    return "NULL"


def read_csv_sql(path: Path, encoding: str = "utf-8") -> str:
    return (f"read_csv({sql_str(str(path))}, skip=2, header=true, all_varchar=true, "
            f"encoding={sql_str(encoding)}, "
            f"quote='\"', escape='\"', null_padding=true, max_line_size=2097152, "
            f"ignore_errors=false)")
