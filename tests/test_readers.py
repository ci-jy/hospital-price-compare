"""Each reader against a trimmed excerpt of the real file."""
import csv
import json

import duckdb
import pytest

from hpc.db import apply_schema
from hpc.ingest import stage_file
from hpc.ingest.wide import payer_columns
from tests.conftest import FIXTURES


@pytest.fixture
def con():
    c = duckdb.connect()
    apply_schema(c)
    yield c
    c.close()


def staged(con, hospital_id):
    return con.execute("SELECT * FROM stg_rate WHERE hospital_id = ?", [hospital_id]).df()


def expected_wide_pairs(path):
    """Count payer|plan groups with a negotiated value, straight from the CSV."""
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r), next(r)
        header = next(r)
        out = []
        for row in r:
            pairs = set()
            for h, v in zip(header, row):
                parts = h.split("|")
                if len(parts) == 4 and parts[0] == "standard_charge" and v.strip() \
                        and parts[3] in ("negotiated_dollar", "negotiated_percentage",
                                         "negotiated_algorithm"):
                    pairs.add((parts[1], parts[2]))
            out.append((row[3], row[header.index("setting")], len(pairs)))
        return out


def test_wide_file_unpivots_one_row_per_payer_plan(con):
    path = FIXTURES / "wide_stlukes_nampa.csv"
    fmt, meta, n = stage_file(con, path, "stlukes-nampa")
    assert fmt == "wide"
    assert meta["version"] == "3.0.0"
    expected = expected_wide_pairs(path)
    assert n == sum(k for _, _, k in expected)
    df = staged(con, "stlukes-nampa")
    for code, setting, k in expected:
        assert len(df[(df.code == code) & (df.setting == setting)]) == k


def test_wide_unpivot_keeps_values_together(con):
    stage_file(con, FIXTURES / "wide_stlukes_nampa.csv", "stlukes-nampa")
    row = con.execute("""
        SELECT negotiated_dollar, negotiated_percentage, methodology, gross_charge
        FROM stg_rate WHERE code = '45378' AND setting = 'both'
          AND payer_name = 'AETNA [1021]' AND plan_name = 'HB NAMPA BP AETNA'
    """).fetchall()
    assert row == [("2007.2", "80", "percent of total billed charges", "2509")]


def test_wide_payer_columns_skip_gross_and_min_max():
    header = ["standard_charge|gross", "standard_charge|Min",
              "standard_charge|A [1]|P|negotiated_dollar", "additional_payer_notes|A [1]|P"]
    assert payer_columns(header) == header[2:]


def test_tall_file_keeps_comparable_code(con):
    path = FIXTURES / "tall_saint_alphonsus_nampa.csv"
    fmt, _, n = stage_file(con, path, "sa")
    assert fmt == "tall"
    with open(path, newline="", encoding="utf-8") as f:
        assert n == sum(1 for _ in f) - 3
    df = staged(con, "sa")
    # CDM codes sit in code|1; the CPT/HCPCS code is picked from code|2.
    assert set(df.code) == {"45378", "A9270"}
    assert set(df.code_type) == {"CPT", "HCPCS"}


def test_code_filter_drops_other_codes(con):
    _, _, n = stage_file(con, FIXTURES / "tall_saint_alphonsus_nampa.csv", "sa", {"45378"})
    assert n == 12
    assert set(staged(con, "sa").code) == {"45378"}


def test_json_streams_every_payer(con):
    path = FIXTURES / "west_valley.json"
    fmt, meta, n = stage_file(con, path, "wv")
    assert fmt == "json"
    assert meta["hospital_name"] == "WEST VALLEY MEDICAL CENTER"
    doc = json.loads(path.read_text())
    expected = sum(len(sc.get("payers_information", []))
                   for item in doc["standard_charge_information"]
                   for sc in item["standard_charges"])
    assert n == expected
    df = staged(con, "wv")
    assert (df[df.code == "45378"].setting == "outpatient").all()


def test_restaging_replaces_previous_rows(con):
    stage_file(con, FIXTURES / "west_valley.json", "wv")
    _, _, n = stage_file(con, FIXTURES / "west_valley.json", "wv")
    assert con.execute("SELECT count(*) FROM stg_rate").fetchone()[0] == n
