from pathlib import Path

import pytest

from hpc.config import Hospital, Metro
from hpc.pipeline import ingest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXTURE_FILES = {
    "stlukes-nampa": FIXTURES / "wide_stlukes_nampa.csv",
    "saint-alphonsus-nampa": FIXTURES / "tall_saint_alphonsus_nampa.csv",
    "west-valley": FIXTURES / "west_valley.json",
}


def fixture_metro(db: Path) -> Metro:
    return Metro(
        name="test metro",
        database=db,
        hospitals=[
            Hospital("stlukes-nampa", "St. Luke's Nampa", "Nampa", "x", "x"),
            Hospital("saint-alphonsus-nampa", "Saint Alphonsus Nampa", "Nampa", "x", "x"),
            Hospital("west-valley", "West Valley", "Caldwell", "x", "x"),
        ],
        codes=None,
        memory_limit="256MB",
        threads=1,
        root=ROOT,
    )


@pytest.fixture(scope="session")
def loaded_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("db") / "fixtures.duckdb"
    ingest(fixture_metro(db), files=FIXTURE_FILES)
    return db
