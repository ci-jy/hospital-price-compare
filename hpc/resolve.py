"""Resolve each hospital's payer and plan names to a canonical payer and plan type.

Hospitals spell payers their own way ("BLUE CROSS IDAHO [1007]", "Blue Cross ",
"BCI HMO") and none of the encodings has a plan-type field, so plan type is
read out of the free text. Resolution runs in this order:

1. hand-checked rows in data/payer_map.csv (exact payer_raw, plan_raw or
   '*'): status `reviewed` fixes the payer and plan type, status
   `ambiguous` keeps the plan type `unknown` on purpose
2. alias rules from config/payers.yaml, longest alias first, on the payer
   name and then on the plan name (some files put "COMMERCIAL" in the payer
   column and the real network in the plan column)
3. RapidFuzz similarity against every alias, for typos and suffixes
4. otherwise the cleaned raw name is kept as its own payer and marked
   'unresolved' so `hpc payers` can list it for review

A bare "MA" plan code is the classic ambiguous case: Medicare Advantage at
most carriers, "Medical Assistance" (Medicaid) at others. Unless the payer
only runs Medicaid business, it is not guessed: the plan type stays `unknown`
and the match is marked `ambiguous`.

`write_payer_map` keeps data/payer_map.csv in step with the loaded files:
every raw payer/plan pair gets a row, new pairs come in as `auto` (or
`unresolved` / `ambiguous`) for review, and hand-checked rows are never
overwritten.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

FUZZY_CUTOFF = 88.0

PLAN_TYPES = ("medicare_advantage", "medicare", "medicaid", "exchange",
              "workers_comp", "military", "commercial", "unknown")

# data/payer_map.csv
MAP_FIELDS = ["payer_raw", "plan_raw", "payer_name", "plan_type", "status",
              "match_method", "match_score", "hospitals", "note"]
# statuses a person sets after checking a row; the pipeline never changes them
CHECKED_STATUSES = ("reviewed", "ambiguous")
MAP_STATUSES = CHECKED_STATUSES + ("auto", "unresolved")

_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_HOSPITAL_PREFIX = re.compile(r"^(HB|PB)\s+(NAMPA|BOISE|MERIDIAN|MAGIC VALLEY|MSTI|SLRMC)\s+")
_NON_WORD = re.compile(r"[^A-Z0-9&+/ -]+")


def clean(text: str | None) -> str:
    t = (text or "").upper().replace("'", "")
    t = _BRACKETS.sub(" ", t)
    t = _NON_WORD.sub(" ", t)
    t = " ".join(t.split())
    t = _HOSPITAL_PREFIX.sub("", t)
    return t


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![A-Z0-9]){re.escape(phrase)}(?![A-Z0-9])", text) is not None


_MA_PATTERNS = ("MEDICARE ADVANTAGE", "MEDICARE ADV", "MCR ADV", "MED ADV", "MEDADV",
                "MAPD", "MA PPO", "MA HMO", "DSNP", "D-SNP", "SNP", "MEDICARE REPLACEMENT",
                "SENIOR", "DUAL", "MMP", "MCRHMO", "MCR HMO",
                "MCRPPO", "MCR PPO", "TRUE BLUE")
_MEDICAID_PATTERNS = ("MEDICAID", "MCD", "MMCP", "MLTSS", "CHIP", "MGMCD", "IDHW",
                      "MEDICAID COUNTY")
_EXCHANGE_PATTERNS = ("QHP", "HIX", "EXCHANGE", "MARKETPLACE", "ACA", "INDIVIDUAL")
_WC_PATTERNS = ("WORKERS COMP", "WORK COMP", "WCOMP", "WORKERS COMPENSATION", "WC")
_MILITARY_PATTERNS = ("TRICARE", "CHAMPVA", "VACCN", "VETERANS", "TRIWEST", "VA")


def classify_plan_type(payer_text: str, plan_text: str, medicaid_mco: bool = False) -> str:
    """Plan type from free text; 'MA' alone is the ambiguous case.

    'MA' is Medicare Advantage at most payers but some Medicaid-only plans use
    it for "Medical Assistance". It is read as Medicaid when the payer only
    runs Medicaid business or the text mentions Medicaid; otherwise as
    Medicare Advantage.
    """
    text = f"{clean(payer_text)} {clean(plan_text)}"
    if any(_has(text, p) for p in _MEDICAID_PATTERNS):
        return "medicaid"
    if any(_has(text, p) for p in _MA_PATTERNS):
        return "medicare_advantage"
    if _has(text, "MA"):
        return "medicaid" if medicaid_mco else "unknown"
    if _has(text, "MEDICARE") or _has(text, "MCR"):
        return "medicaid" if medicaid_mco else "medicare"
    if medicaid_mco:
        return "medicaid"
    if any(_has(text, p) for p in _WC_PATTERNS):
        return "workers_comp"
    if any(_has(text, p) for p in _MILITARY_PATTERNS):
        return "military"
    if any(_has(text, p) for p in _EXCHANGE_PATTERNS):
        return "exchange"
    return "commercial"


@dataclass
class Resolution:
    payer_name: str
    plan_type: str
    method: str          # reviewed | alias | fuzzy | ambiguous | unresolved
    score: float | None = None


class PayerResolver:
    def __init__(self, payers: dict, reviewed: list[dict[str, str]] | None = None):
        self.medicaid_mco: set[str] = set()
        self.aliases: list[tuple[str, str]] = []          # carriers: (alias, canonical)
        self.program_aliases: list[tuple[str, str]] = []  # Medicare, Medicaid
        for name, spec in payers.items():
            if isinstance(spec, dict):
                aliases = spec.get("aliases", [])
                if spec.get("medicaid_mco"):
                    self.medicaid_mco.add(name)
                target = self.program_aliases if spec.get("program") else self.aliases
            else:
                aliases, target = spec, self.aliases
            for a in [name, *aliases]:
                target.append((clean(a), name))
        # Longest alias wins: "BLUE CROSS FEDERAL" before "BLUE CROSS".
        self.aliases.sort(key=lambda a: -len(a[0]))
        self.program_aliases.sort(key=lambda a: -len(a[0]))
        self._all = self.aliases + self.program_aliases
        self._alias_choices = [a for a, _ in self._all]
        # Rows without a status come from the older reviewed-only format.
        self.reviewed = {(r["payer_raw"].strip(), r["plan_raw"].strip()): r
                         for r in reviewed or []
                         if (r.get("status") or "reviewed") in CHECKED_STATUSES}

    @classmethod
    def from_files(cls, payers_yaml: Path, payer_map_csv: Path | None = None) -> "PayerResolver":
        payers = yaml.safe_load(Path(payers_yaml).read_text())["payers"]
        reviewed = []
        if payer_map_csv and Path(payer_map_csv).exists():
            with open(payer_map_csv, newline="") as f:
                reviewed = list(csv.DictReader(f))
        return cls(payers, reviewed)

    def _alias_match(self, text: str, aliases: list[tuple[str, str]]) -> str | None:
        for alias, canonical in aliases:
            if alias and _has(text, alias):
                return canonical
        return None

    def resolve(self, payer_raw: str, plan_raw: str, use_reviewed: bool = True) -> Resolution:
        """Resolve one raw pair. `use_reviewed=False` runs the automatic
        rules only, which is how they are scored against the review."""
        payer_raw, plan_raw = (payer_raw or "").strip(), (plan_raw or "").strip()
        hit = use_reviewed and (self.reviewed.get((payer_raw, plan_raw))
                                or self.reviewed.get((payer_raw, "*")))
        if hit:
            if (hit.get("status") or "reviewed") == "ambiguous":
                auto = self.resolve(payer_raw, plan_raw, use_reviewed=False)
                return Resolution(hit.get("payer_name") or auto.payer_name, "unknown",
                                  "ambiguous", None)
            return Resolution(hit["payer_name"], hit["plan_type"], "reviewed", 100.0)

        payer_c, plan_c = clean(payer_raw), clean(plan_raw)
        canonical = (self._alias_match(payer_c, self.aliases)
                     or self._alias_match(plan_c, self.aliases)
                     or self._alias_match(payer_c, self.program_aliases)
                     or self._alias_match(plan_c, self.program_aliases))
        method, score = "alias", 100.0
        if canonical is None and payer_c:
            best = process.extractOne(payer_c, self._alias_choices,
                                      scorer=fuzz.token_sort_ratio, score_cutoff=FUZZY_CUTOFF)
            if best:
                canonical, method, score = self._all[best[2]][1], "fuzzy", float(best[1])
        if canonical is None:
            canonical, method, score = (payer_c.title() or "Unknown payer"), "unresolved", None
        plan_type = classify_plan_type(payer_raw, plan_raw, canonical in self.medicaid_mco)
        programs = {name for _, name in self.program_aliases}
        if plan_type == "medicare" and canonical not in programs and method != "unresolved":
            # A private carrier's Medicare business is Medicare Advantage.
            plan_type = "medicare_advantage"
        if canonical == "Medicare" and plan_type == "medicare_advantage":
            # A private Medicare Advantage plan whose carrier we don't know.
            canonical, method, score = "Medicare Advantage (unlisted carrier)", "unresolved", None
        if plan_type == "unknown" and method != "unresolved":
            method = "ambiguous"
        return Resolution(canonical, plan_type, method, score)


def _status(method: str) -> str:
    return {"reviewed": "reviewed", "ambiguous": "ambiguous",
            "unresolved": "unresolved"}.get(method, "auto")


def read_payer_map(path: Path) -> list[dict[str, str]]:
    if not Path(path).exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_payer_map(mapping, path: Path) -> dict[str, int]:
    """Merge resolved pairs into data/payer_map.csv; returns counts by status.

    `mapping` has hospital_id, payer_raw, plan_raw, payer_name, plan_type,
    match_method and match_score columns (one row per hospital and pair).
    Hand-checked rows keep their payer, plan type, status and note; every
    other row is rewritten from the current resolution. Pairs no longer in
    the data are kept, so loading fewer hospitals never drops reviewed work.
    """
    existing = {(r["payer_raw"], r["plan_raw"]): r for r in read_payer_map(path)}
    seen: dict[tuple[str, str], dict] = {}
    for r in mapping.itertuples():
        key = (r.payer_raw, r.plan_raw)
        if key in seen:
            seen[key]["hospitals"].add(r.hospital_id)
            continue
        old = existing.get(key, {})
        hospitals = {h for h in (old.get("hospitals") or "").split(";") if h}
        hospitals.add(r.hospital_id)
        if (old.get("status") or "") in CHECKED_STATUSES:
            row = {**old}
        else:
            score = "" if r.match_score is None or r.match_score != r.match_score \
                else f"{r.match_score:.0f}"
            row = {"payer_raw": r.payer_raw, "plan_raw": r.plan_raw,
                   "payer_name": r.payer_name, "plan_type": r.plan_type,
                   "status": _status(r.match_method), "match_method": r.match_method,
                   "match_score": score, "note": old.get("note", "")}
        row["hospitals"] = hospitals
        seen[key] = row
    for key, old in existing.items():
        if key not in seen:
            seen[key] = {**old, "hospitals": {h for h in (old.get("hospitals") or "").split(";")
                                              if h}}
    rows = sorted(seen.values(), key=lambda r: (r["payer_name"].lower(), r["payer_raw"],
                                                r["plan_raw"]))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MAP_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            row["hospitals"] = ";".join(sorted(row["hospitals"]))
            w.writerow(row)
            counts[row.get("status") or "reviewed"] = counts.get(row.get("status") or "reviewed", 0) + 1
    return counts


def score_against_review(resolver: PayerResolver,
                         rows: list[dict[str, str]]) -> tuple[int, int]:
    """How many hand-checked rows the automatic rules get right on their own.

    A `reviewed` row counts when the rules give the same payer and plan type;
    an `ambiguous` row counts when the rules also leave the plan type
    unknown. Returns (agreeing, checked).
    """
    agree = total = 0
    for row in rows:
        status = row.get("status") or "reviewed"
        if status not in CHECKED_STATUSES or row["plan_raw"] == "*":
            continue
        total += 1
        auto = resolver.resolve(row["payer_raw"], row["plan_raw"], use_reviewed=False)
        if status == "ambiguous":
            agree += auto.plan_type == "unknown"
        else:
            agree += (auto.payer_name, auto.plan_type) == (row["payer_name"], row["plan_type"])
    return agree, total
