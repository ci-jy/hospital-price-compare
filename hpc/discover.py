"""Find each hospital's machine-readable file through its cms-hpt.txt.

CMS requires every hospital to publish a plain-text index at
https://<domain>/cms-hpt.txt with one block per location:

    location-name: ...
    source-page-url: ...
    mrf-url: ...
    contact-name: ...
    contact-email: ...

Blocks are separated by blank lines. Several locations may point at the same
file (St. Luke's Boise and Meridian share one license, for example).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import requests

from hpc.config import Hospital, Metro

USER_AGENT = "hospital-price-compare/0.1 (+public price transparency research)"
SOURCES_FIELDS = ["hospital_id", "hospital_name", "location_name", "index_url",
                  "mrf_url", "source_page_url", "fetched_at"]


def parse_cms_hpt(text: str) -> list[dict[str, str]]:
    """Parse a cms-hpt.txt body into a list of location dicts."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.lstrip("﻿").splitlines():
        line = line.strip()
        if not line:
            if current:
                blocks.append(current)
                current = {}
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        # A new location-name without a blank line still starts a new block.
        if key == "location-name" and "location-name" in current:
            blocks.append(current)
            current = {}
        current[key] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def match_location(blocks: list[dict[str, str]], location: str) -> dict[str, str]:
    want = " ".join(location.lower().split())
    for block in blocks:
        if " ".join(block.get("location-name", "").lower().split()) == want:
            return block
    names = [b.get("location-name") for b in blocks]
    raise LookupError(f"location {location!r} not in cms-hpt.txt (found {names})")


def fetch_index(domain: str, session: requests.Session | None = None) -> tuple[str, str]:
    session = session or requests.Session()
    last_error: Exception | None = None
    for url in (f"https://www.{domain}/cms-hpt.txt", f"https://{domain}/cms-hpt.txt"):
        try:
            resp = session.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
            if resp.ok and "mrf-url" in resp.text:
                return url, resp.text
            last_error = RuntimeError(f"{url}: HTTP {resp.status_code}")
        except requests.RequestException as exc:
            last_error = exc
    raise RuntimeError(f"no usable cms-hpt.txt for {domain}: {last_error}")


def discover(metro: Metro, hospitals: list[Hospital] | None = None) -> list[dict[str, str]]:
    session = requests.Session()
    cache: dict[str, tuple[str, str]] = {}
    rows = []
    for h in hospitals or metro.hospitals:
        if h.domain not in cache:
            cache[h.domain] = fetch_index(h.domain, session)
        index_url, text = cache[h.domain]
        block = match_location(parse_cms_hpt(text), h.location)
        rows.append({
            "hospital_id": h.id,
            "hospital_name": h.name,
            "location_name": block["location-name"],
            "index_url": index_url,
            "mrf_url": block["mrf-url"],
            "source_page_url": block.get("source-page-url", ""),
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return rows


def write_sources(rows: list[dict[str, str]], path: Path) -> None:
    """Merge rows into sources.csv, replacing older entries for the same hospital."""
    existing = read_sources(path)
    for row in rows:
        existing[row["hospital_id"]] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SOURCES_FIELDS)
        w.writeheader()
        for row in existing.values():
            w.writerow(row)


def read_sources(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {row["hospital_id"]: row for row in csv.DictReader(f)}
