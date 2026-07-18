"""Loading config/metro.yaml."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Hospital:
    id: str
    name: str
    city: str
    domain: str
    location: str


@dataclass
class Metro:
    name: str
    database: Path
    hospitals: list[Hospital]
    codes: set[str] | None = None
    memory_limit: str = "1GB"
    threads: int = 2
    root: Path = field(default_factory=Path.cwd)


def load_metro(path: str | Path) -> Metro:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    # Paths inside the YAML are relative to the project root (config/..).
    root = path.resolve().parent.parent
    codes = None
    if raw.get("codes_file"):
        with open(root / raw["codes_file"], newline="") as f:
            codes = {row["code"].strip() for row in csv.DictReader(f)}
    db = raw.get("database", "data/hpc.duckdb")
    duck = raw.get("duckdb", {})
    return Metro(
        name=raw["metro"],
        database=root / db,
        hospitals=[Hospital(**h) for h in raw["hospitals"]],
        codes=codes,
        memory_limit=str(duck.get("memory_limit", "1GB")),
        threads=int(duck.get("threads", 2)),
        root=root,
    )
