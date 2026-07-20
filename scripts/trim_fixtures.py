"""Cut small test fixtures out of the real downloaded files in data/raw.

Run after `hpc ingest` has downloaded the files:  python3 scripts/trim_fixtures.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hpc.discover import fetch_index  # noqa: E402

RAW = Path("data/raw")
OUT = Path("tests/fixtures")


PLACEHOLDER_NAME = "Example Attester"


def trim_csv(src: Path, dest: Path, keep, limit: int) -> None:
    with open(src, newline="", encoding="utf-8-sig") as f, \
            open(dest, "w", newline="", encoding="utf-8") as out:
        r, w = csv.reader(f), csv.writer(out)
        keys, values = next(r), next(r)
        # The metadata rows name the person who signed the attestation.
        values = [PLACEHOLDER_NAME if k.strip().lower() == "attester_name" and v else v
                  for k, v in zip(keys, values)] + values[len(keys):]
        w.writerow(keys)
        w.writerow(values)
        w.writerow(next(r))
        n = 0
        for row in r:
            if keep(row):
                w.writerow(row)
                n += 1
                if n >= limit:
                    break


def redact_contacts(text: str) -> str:
    """cms-hpt.txt names a real person per location; keep them out of fixtures."""
    text = re.sub(r"(?m)^contact-name:.*$", "contact-name: Example Contact", text)
    return re.sub(r"(?m)^contact-email:.*$", "contact-email: contact@example.org", text)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # First two locations of Saint Alphonsus's index, with contacts redacted.
    _, index = fetch_index("saintalphonsus.org")
    blocks = index.replace("\r\n", "\n").split("\n\n")[:2]
    (OUT / "cms-hpt.txt").write_text(redact_contacts("\n\n".join(blocks)) + "\n")
    # Wide: both 45378 rows plus one lab code from St. Luke's Nampa.
    trim_csv(RAW / "stlukes-nampa.csv", OUT / "wide_stlukes_nampa.csv",
             lambda row: row[3] in ("45378", "85025"), limit=4)

    # Tall: a handful of 45378 rows and the clobazam "$.76" percent rows.
    picked = {"45378": 0, ".76": 0}

    def tall_keep(row):
        if row[3] == "45378" and row[15][:5] in ("AETNA", "BLUE ", "CIGNA", "MOLIN", "ID CO") \
                and picked["45378"] < 12:
            picked["45378"] += 1
            return True
        if row[0] == "CLOBAZAM 10 MG TABLET" and row[18] == ".76" and picked[".76"] < 1:
            picked[".76"] += 1
            return True
        return False

    trim_csv(RAW / "saint-alphonsus-nampa.csv", OUT / "tall_saint_alphonsus_nampa.csv",
             tall_keep, limit=13)

    # JSON: header plus the 45378 items and one percent-in-notes item.
    with open(RAW / "west-valley.json", "rb") as f:
        header = {k: v for k, v in ijson.kvitems(f, "")
                  if k not in ("standard_charge_information", "modifier_information")}
    items, seen = [], set()
    with open(RAW / "west-valley.json", "rb") as f:
        for item in ijson.items(f, "standard_charge_information.item", use_float=True):
            codes = {c["code"] for c in item["code_information"]}
            if "45378" in codes:
                items.append(item)
            elif "72193" in codes and "72193" not in seen:
                # Keep the percent-of-charges payers whose percentage is only in the notes.
                sc = item["standard_charges"][0]
                pct = [p for p in sc.get("payers_information", [])
                       if "percent" in p.get("methodology", "")][:4]
                if pct:
                    sc["payers_information"] = pct
                    item["standard_charges"] = [sc]
                    items.append(item)
                    seen.add("72193")
            if len(items) >= 5 and "72193" in seen:
                break
    if isinstance(header.get("attestation"), dict) and header["attestation"].get("attester_name"):
        header["attestation"]["attester_name"] = PLACEHOLDER_NAME
    header["general_contract_provisions"] = header.get("general_contract_provisions", [])[:2]
    header["standard_charge_information"] = items
    (OUT / "west_valley.json").write_text(json.dumps(header, indent=1))


if __name__ == "__main__":
    main()
