"""Resolve each hospital's payer and plan names to a canonical payer and plan type.

Hospitals spell payers their own way ("BLUE CROSS IDAHO [1007]", "Blue Cross ",
"BCI HMO") and none of the encodings has a plan-type field, so plan type is
read out of the free text. Resolution runs in this order:

1. reviewed rows in config/payer_map.csv (exact payer_raw, plan_raw or '*')
2. alias rules from config/payers.yaml, longest alias first, on the payer
   name and then on the plan name (some files put "COMMERCIAL" in the payer
   column and the real network in the plan column)
3. RapidFuzz similarity against every alias, for typos and suffixes
4. otherwise the cleaned raw name is kept as its own payer and marked
   'unresolved' so `hpc payers` can list it for review
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
              "workers_comp", "military", "commercial")

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
                "SENIOR", "DUAL", "MMP")
_MEDICAID_PATTERNS = ("MEDICAID", "MCD", "MMCP", "MLTSS", "CHIP", "MGMCD", "IDHW",
                      "MEDICAID COUNTY", "BLUE CROSS IDAHO MEDICAID", "TRUE BLUE")
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
        return "medicaid" if medicaid_mco else "medicare_advantage"
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
    method: str          # reviewed | alias | fuzzy | unresolved
    score: float | None = None


class PayerResolver:
    def __init__(self, payers: dict, reviewed: list[dict[str, str]] | None = None):
        self.medicaid_mco: set[str] = set()
        self.aliases: list[tuple[str, str]] = []   # (alias, canonical)
        for name, spec in payers.items():
            if isinstance(spec, dict):
                aliases = spec.get("aliases", [])
                if spec.get("medicaid_mco"):
                    self.medicaid_mco.add(name)
            else:
                aliases = spec
            for a in [name, *aliases]:
                self.aliases.append((clean(a), name))
        # Longest alias wins: "BLUE CROSS FEDERAL" before "BLUE CROSS".
        self.aliases.sort(key=lambda a: -len(a[0]))
        self._alias_choices = [a for a, _ in self.aliases]
        self.reviewed = {(r["payer_raw"].strip(), r["plan_raw"].strip()): r
                         for r in reviewed or []}

    @classmethod
    def from_files(cls, payers_yaml: Path, payer_map_csv: Path | None = None) -> "PayerResolver":
        payers = yaml.safe_load(Path(payers_yaml).read_text())["payers"]
        reviewed = []
        if payer_map_csv and Path(payer_map_csv).exists():
            with open(payer_map_csv, newline="") as f:
                reviewed = list(csv.DictReader(f))
        return cls(payers, reviewed)

    def _alias_match(self, text: str) -> str | None:
        for alias, canonical in self.aliases:
            if alias and _has(text, alias):
                return canonical
        return None

    def resolve(self, payer_raw: str, plan_raw: str) -> Resolution:
        payer_raw, plan_raw = (payer_raw or "").strip(), (plan_raw or "").strip()
        hit = self.reviewed.get((payer_raw, plan_raw)) or self.reviewed.get((payer_raw, "*"))
        if hit:
            return Resolution(hit["payer_name"], hit["plan_type"], "reviewed", 100.0)

        payer_c, plan_c = clean(payer_raw), clean(plan_raw)
        canonical = self._alias_match(payer_c) or self._alias_match(plan_c)
        method, score = "alias", 100.0
        if canonical is None and payer_c:
            best = process.extractOne(payer_c, self._alias_choices,
                                      scorer=fuzz.token_sort_ratio, score_cutoff=FUZZY_CUTOFF)
            if best:
                canonical, method, score = self.aliases[best[2]][1], "fuzzy", float(best[1])
        if canonical is None:
            canonical, method, score = (payer_c.title() or "Unknown payer"), "unresolved", None
        plan_type = classify_plan_type(payer_raw, plan_raw, canonical in self.medicaid_mco)
        return Resolution(canonical, plan_type, method, score)
