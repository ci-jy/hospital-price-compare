"""discover -> download -> stage -> build, for the hospitals in a metro config."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from hpc import build, discover
from hpc.config import Hospital, Metro
from hpc.db import apply_schema, connect
from hpc.ingest import stage_file

log = logging.getLogger("hpc")


def raw_path(root: Path, hospital_id: str, url: str) -> Path:
    # Some CDNs append an asset hash after the file name (".../x.csv/58d75b...").
    found = re.findall(r"\.(csv|json|zip)(?=/|$)", urlsplit(url).path.lower())
    suffix = f".{found[-1]}" if found else ".bin"
    return root / "data" / "raw" / f"{hospital_id}{suffix}"


def _url_key(url: str) -> str:
    # Signed blob URLs change their query string on every fetch.
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}"


def download(url: str, dest: Path) -> Path:
    """Fetch `url` to `dest` unless the same file is already cached."""
    marker = dest.with_name(dest.name + ".url")
    if dest.exists() and marker.exists() and marker.read_text().strip() == _url_key(url):
        log.info("cached %s", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    log.info("downloading %s", url.split("?")[0])
    with requests.get(url, stream=True, timeout=120,
                      headers={"User-Agent": discover.USER_AGENT}) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    marker.write_text(_url_key(url))
    return dest


def resolve_sources(metro: Metro, hospitals: list[Hospital]) -> dict[str, dict[str, str]]:
    sources_csv = metro.root / "data" / "sources.csv"
    try:
        rows = discover.discover(metro, hospitals)
        discover.write_sources(rows, sources_csv)
    except Exception as exc:  # offline: fall back to the last recorded URLs
        log.warning("discovery failed (%s); using %s", exc, sources_csv)
    known = discover.read_sources(sources_csv)
    missing = [h.id for h in hospitals if h.id not in known]
    if missing:
        raise RuntimeError(f"no source URL for {missing}; run `hpc discover` online")
    return known


def ingest(metro: Metro, n_hospitals: int | None = None, all_codes: bool = False,
           files: dict[str, Path] | None = None) -> list[dict]:
    """Load the first `n_hospitals` hospitals of the metro config.

    `files` maps hospital id -> local file and skips discovery/download (tests).
    """
    hospitals = metro.hospitals[: n_hospitals or None]
    sources = {} if files else resolve_sources(metro, hospitals)
    codes = None if all_codes else metro.codes
    con = connect(metro.database, metro.memory_limit, metro.threads)
    apply_schema(con)
    report = []
    try:
        for h in hospitals:
            started = time.perf_counter()
            url = "" if files else sources[h.id]["mrf_url"]
            path = files[h.id] if files else download(url, raw_path(metro.root, h.id, url))
            fmt, meta, n = stage_file(con, path, h.id, codes)
            con.execute("DELETE FROM fact_rate WHERE hospital_id = ?", [h.id])
            con.execute("DELETE FROM dim_hospital WHERE hospital_id = ?", [h.id])
            con.execute("INSERT INTO dim_hospital VALUES (?,?,?,?,?,?,?,?,?,?)", [
                h.id, h.name, h.city, fmt, url or str(path),
                meta.get("hospital_name"), meta.get("last_updated_on"), meta.get("version"),
                n, datetime.now(timezone.utc).replace(tzinfo=None)])
            secs = time.perf_counter() - started
            log.info("%-24s %-5s %9d rows  %6.1fs", h.id, fmt, n, secs)
            report.append({"hospital_id": h.id, "encoding": fmt, "rows": n, "seconds": secs})
        build.rebuild(con, build.default_resolver(metro.root))
    finally:
        con.close()
    return report
