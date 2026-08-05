"""The edge cases that make hospital files hard to compare, one test each.

'MA' ambiguity, a percentage stored in the dollar column, per-diem rows, a
code with several descriptions, and the payer map / coverage report that
`hpc check --require-payer-map` relies on.
"""
import csv

import duckdb
import pandas as pd
import pytest

from hpc import build, method
from hpc.cli import main
from hpc.db import apply_schema
from hpc.resolve import PayerResolver, read_payer_map, write_payer_map
from tests.conftest import ROOT

STG = ["hospital_id", "description", "code", "code_type", "setting", "billing_class",
       "modifiers", "gross_charge", "cash_price", "payer_name", "plan_name",
       "negotiated_dollar", "negotiated_percentage", "negotiated_algorithm",
       "estimated_amount", "methodology", "notes"]


@pytest.fixture
def con():
    c = duckdb.connect()
    apply_schema(c)
    for h in ("a", "b"):
        c.execute("INSERT INTO dim_hospital (hospital_id, hospital_name, encoding) "
                  "VALUES (?, ?, 'tall')", [h, f"Hospital {h}"])
    yield c
    c.close()


def stage(con, **values):
    row = {k: None for k in STG}
    row.update(hospital_id="a", description="Colonoscopy", code="45378", code_type="CPT",
               setting="outpatient", billing_class="facility", payer_name="Aetna",
               plan_name="PPO", gross_charge="5000")
    row.update(values)
    con.execute(f"INSERT INTO stg_rate VALUES ({','.join('?' * len(STG))})",
                [row[k] for k in STG])


def rebuild(con, resolver=None):
    return build.rebuild(con, resolver or build.default_resolver(ROOT))


# --- 'MA' ambiguity -------------------------------------------------------

def test_ma_plan_is_flagged_and_kept_out_of_known_plan_types(con):
    stage(con, payer_name="HUMANA", plan_name="MA", negotiated_dollar="900",
          methodology="fee schedule")
    stage(con, payer_name="HUMANA", plan_name="MEDICARE ADVANTAGE PPO",
          negotiated_dollar="950", methodology="fee schedule")
    rebuild(con)
    rows = con.execute("""
        SELECT f.plan_raw, pl.plan_type, f.quality_flag FROM fact_rate f
        JOIN dim_plan pl USING (plan_id) ORDER BY f.plan_raw
    """).fetchall()
    assert rows == [("MA", "unknown", "plan_type_ambiguous"),
                    ("MEDICARE ADVANTAGE PPO", "medicare_advantage", "ok")]


def test_ambiguous_row_in_the_map_overrides_an_auto_guess():
    resolver = PayerResolver(
        {"Aetna": ["AETNA"]},
        [{"payer_raw": "AETNA", "plan_raw": "AETNA MA", "payer_name": "Aetna",
          "plan_type": "", "status": "ambiguous"},
         {"payer_raw": "AETNA", "plan_raw": "AETNA MA HMO", "payer_name": "Aetna",
          "plan_type": "medicare_advantage", "status": "reviewed"}])
    assert resolver.resolve("AETNA", "AETNA MA").plan_type == "unknown"
    assert resolver.resolve("AETNA", "AETNA MA HMO").method == "reviewed"
    # auto rows are suggestions only and never override the rules
    auto = PayerResolver({"Aetna": ["AETNA"]}, [
        {"payer_raw": "AETNA", "plan_raw": "PPO", "payer_name": "Somebody Else",
         "plan_type": "medicaid", "status": "auto"}])
    assert auto.resolve("AETNA", "PPO").payer_name == "Aetna"


# --- percent stored as dollars ---------------------------------------------

def test_percent_in_the_dollar_field_is_compared_as_a_percentage():
    out = method.classify([
        # no gross charge: "$0.76" can only be 76%
        {"negotiated_dollar": "$0.76", "methodology": "percent of total billed charges"},
        # a gross charge: dollar / gross gives the real percentage
        {"negotiated_dollar": ".76", "gross_charge": "2.85",
         "methodology": "percent of total billed charges"},
        # a real 69-cent fee schedule amount stays in dollars
        {"negotiated_dollar": "0.69", "gross_charge": "1.20", "methodology": "fee schedule"},
    ])
    assert list(out["pricing_method"]) == ["percent_of_charges"] * 2 + ["flat_dollar"]
    assert list(out["compare_unit"]) == ["pct_of_charges"] * 2 + ["usd"]
    assert out["amount"].tolist() == pytest.approx([76.0, 26.67, 0.69])
    assert out["pct_in_dollar"].tolist() == [True, False, False]
    assert out["pct_derived"].tolist() == [False, True, False]


def test_percent_of_medicare_is_not_a_percent_of_charges():
    out = method.classify([
        {"negotiated_percentage": "120", "methodology": "other",
         "negotiated_algorithm": "120% of current Medicare fee schedule"},
        {"negotiated_percentage": "135", "negotiated_algorithm": "APC rate x 135%"},
        {"negotiated_percentage": "65", "negotiated_algorithm": "65% of billed charges"},
    ])
    assert list(out["pricing_method"]) == ["other", "other", "percent_of_charges"]
    assert out["pct_of_fee_schedule"].tolist() == [True, True, False]


def test_dollar_methodology_with_only_a_percentage_is_not_compared():
    out = method.classify([{"negotiated_percentage": "80", "methodology": "case rate"}])
    assert out.loc[0, "pricing_method"] == "other"
    assert bool(out.loc[0, "method_value_mismatch"])


# --- per-diem rows -----------------------------------------------------------

def test_per_diem_rows_are_classified_from_field_or_text():
    out = method.classify([
        {"negotiated_dollar": "3100", "methodology": "per diem", "setting": "inpatient"},
        {"negotiated_dollar": "2500", "methodology": "other",
         "negotiated_algorithm": "Daily rate for days 1-3"},
        {"negotiated_dollar": "2500", "negotiated_algorithm": "$2,500 per day"},
    ])
    assert list(out["pricing_method"]) == ["per_diem"] * 3
    assert list(out["compare_unit"]) == ["usd"] * 3
    assert out["method_inferred"].tolist() == [False, True, True]


def test_per_diem_and_case_rate_never_share_a_ranking(con):
    stage(con, code="470", code_type="MS-DRG", setting="inpatient",
          negotiated_dollar="3100", methodology="per diem")
    stage(con, hospital_id="b", code="470", code_type="MS-DRG", setting="inpatient",
          negotiated_dollar="31000", methodology="case rate")
    rebuild(con)
    groups = con.execute("""
        SELECT pricing_method, group_rates, group_median FROM v_rate_compare
        ORDER BY pricing_method
    """).fetchall()
    assert groups == [("case_rate", 1, 31000.0), ("per_diem", 1, 3100.0)]


# --- one code, many descriptions --------------------------------------------

def test_duplicate_descriptions_for_one_code_are_one_service(con):
    for i, desc in enumerate(["COLONOSCOPY DX", "Colonoscopy, flexible; diagnostic",
                              "HC COLONOSCOPY FLEX DX", "COLONOSCOPY DX"]):
        stage(con, hospital_id="ab"[i % 2], description=desc,
              negotiated_dollar=str(1000 + i), methodology="fee schedule")
    rebuild(con)
    assert con.execute("SELECT code, description, raw_descriptions FROM dim_service"
                       ).fetchall() == [("45378", "COLONOSCOPY DX", 3)]
    # all four rates sit in one comparison group across both hospitals
    assert con.execute("SELECT DISTINCT group_rates, group_hospitals FROM v_rate_compare"
                       ).fetchall() == [(4, 2)]


# --- payer map and coverage -------------------------------------------------

def _mapping(rows):
    return pd.DataFrame(rows, columns=["hospital_id", "payer_raw", "plan_raw", "payer_name",
                                       "plan_type", "match_method", "match_score"])


def test_payer_map_keeps_reviewed_rows_and_adds_new_ones(tmp_path):
    path = tmp_path / "payer_map.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["payer_raw", "plan_raw", "payer_name", "plan_type", "status", "note"])
        w.writerow(["COMMERCIAL", "HB AMPS", "AMPS", "commercial", "reviewed", "checked"])
    counts = write_payer_map(_mapping([
        ("a", "COMMERCIAL", "HB AMPS", "Commercial", "commercial", "unresolved", None),
        ("b", "COMMERCIAL", "HB AMPS", "Commercial", "commercial", "unresolved", None),
        ("a", "HUMANA", "MA", "Humana", "unknown", "ambiguous", None),
        ("a", "AETNNA", "PPO", "Aetna", "commercial", "fuzzy", 91.7),
    ]), path)
    rows = {(r["payer_raw"], r["plan_raw"]): r for r in read_payer_map(path)}
    assert rows[("COMMERCIAL", "HB AMPS")]["payer_name"] == "AMPS"
    assert rows[("COMMERCIAL", "HB AMPS")]["hospitals"] == "a;b"
    assert rows[("COMMERCIAL", "HB AMPS")]["note"] == "checked"
    assert rows[("HUMANA", "MA")]["status"] == "ambiguous"
    assert rows[("AETNNA", "PPO")]["match_score"] == "92"
    assert counts == {"reviewed": 1, "ambiguous": 1, "auto": 1}
    # a second load with fewer hospitals keeps every row
    write_payer_map(_mapping([("a", "AETNNA", "PPO", "Aetna", "commercial", "fuzzy", 91.7)]),
                    path)
    assert len(read_payer_map(path)) == 3


def test_require_payer_map_fails_on_missing_names_and_writes_report(loaded_db, tmp_path,
                                                                    capsys):
    import shutil
    db = tmp_path / "copy.duckdb"
    shutil.copy(loaded_db, db)
    c = duckdb.connect(str(db), read_only=True)
    pairs = c.execute("SELECT DISTINCT hospital_id, payer_raw, plan_raw, 'x' AS payer_name, "
                      "'commercial' AS plan_type, 'alias' AS match_method, "
                      "100.0 AS match_score FROM map_payer_plan").df()
    c.close()
    write_payer_map(pairs.iloc[1:], tmp_path / "payer_map.csv")
    report = tmp_path / "coverage.md"
    args = ["check", "--db", str(db), "--require-payer-map", "--report", str(report)]
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "FAIL  payer map covers every raw name" in out

    # complete the map and mark one row as checked: now it passes
    write_payer_map(pairs, tmp_path / "payer_map.csv")
    rows = read_payer_map(tmp_path / "payer_map.csv")
    rows[0]["status"] = "reviewed"
    with open(tmp_path / "payer_map.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    assert main(args) == 0
    text = report.read_text()
    assert "| West Valley |" in text
    assert "Rates with a comparable amount" in text
