"""Work out which of the three CMS encodings a file uses.

* ``json``  - the JSON schema (one object with standard_charge_information)
* ``tall``  - CSV with one row per item x payer x plan (has a payer_name column)
* ``wide``  - CSV with one row per item and a column group per payer|plan

Both CSV layouts start with two metadata rows (field names, then values)
before the real header on row three.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

ZIP_MAGIC = b"PK\x03\x04"


class UnknownFormat(ValueError):
    pass


def sniff_bytes(head: bytes) -> str:
    if head.startswith(ZIP_MAGIC):
        return "zip"
    text = head.decode("utf-8", errors="replace").lstrip("﻿ \t\r\n")
    if text.startswith("{"):
        return "json"
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 3:
        raise UnknownFormat("CSV has fewer than three rows in the first block")
    header = [c.strip().lower() for c in rows[2]]
    if "payer_name" in header and "plan_name" in header:
        return "tall"
    if any(c.startswith("standard_charge|") and c.count("|") >= 3 for c in header):
        return "wide"
    raise UnknownFormat(f"unrecognised CSV header: {header[:8]}")


def detect_format(path: str | Path, head_bytes: int = 1 << 20) -> str:
    with open(path, "rb") as f:
        head = f.read(head_bytes)
    return sniff_bytes(head)


def extract_zip(path: Path, dest_stem: Path) -> Path:
    """Stream the single data member of a zip next to it; returns the new path."""
    with zipfile.ZipFile(path) as zf:
        members = [m for m in zf.infolist()
                   if not m.is_dir() and m.filename.lower().endswith((".csv", ".json"))]
        if len(members) != 1:
            raise UnknownFormat(f"{path.name}: expected one CSV/JSON member, got "
                                f"{[m.filename for m in members]}")
        member = members[0]
        out = dest_stem.with_suffix(Path(member.filename).suffix.lower())
        if out.exists() and out.stat().st_size == member.file_size:
            return out
        tmp = out.with_name(out.name + ".part")
        with zf.open(member) as src, open(tmp, "wb") as dst:
            while chunk := src.read(1 << 22):
                dst.write(chunk)
        tmp.rename(out)
        return out
