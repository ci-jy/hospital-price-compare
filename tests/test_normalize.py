"""Pricing-method classification and percent handling on single staged rows."""
import duckdb
import pytest

from hpc import build
from hpc.db import apply_schema
from hpc.ingest import stage_file
from tests.conftest import FIXTURES, ROOT

STG = ["hospital_id", "description", "code", "code_type", "setting", "billing_class",
       "modifiers", "gross_charge", "cash_price", "payer_name", "plan_name",
       "negotiated_dollar", "negotiated_percentage", "negotiated_algorithm",
       "estimated_amount", "methodology", "notes"]


@pytest.fixture
def con():
    c = duckdb.connect()
    apply_schema(c)
    c.execute("INSERT INTO dim_hospital (hospital_id, hospital_name, encoding) "
              "VALUES ('h', 'Test', 'tall')")
    yield c
    c.close()


def rate(con, **values):
    row = {k: None for k in STG}
    row.update(hospital_id="h", description="x", code="A9270", code_type="HCPCS",
               setting="both", billing_class="facility", payer_name="MOLINA MEDICAID", plan_name="MOLINA IM PLUS MEDICAID")
    row.update(values)
    con.execute(f"INSERT INTO stg_rate VALUES ({','.join('?' * len(STG))})",
                [row[k] for k in STG])
    build.rebuild(con, build.default_resolver(ROOT))
    return con.execute("""
        SELECT pricing_method, compare_amount, compare_unit, quality_flag, negotiated_dollar
        FROM fact_rate""").fetchone()


def test_real_76_cent_percent_rate_is_compared_as_a_percentage(con):
    # Saint Alphonsus Nampa, clobazam 10 mg: $2.85 charge, 26.78% billed, "$.76".
    stage_file(con, FIXTURES / "tall_saint_alphonsus_nampa.csv", "h")
    build.rebuild(con, build.default_resolver(ROOT))
    got = con.execute("""
        SELECT pricing_method, compare_amount, compare_unit, quality_flag, negotiated_dollar
        FROM fact_rate f JOIN dim_service USING (service_id) WHERE code = 'A9270'
    """).fetchone()
    assert got == ("percent_of_charges", pytest.approx(26.78), "pct_of_charges", "ok",
                   pytest.approx(0.76))


def test_76_cents_without_percentage_is_derived_from_gross(con):
    got = rate(con, gross_charge="2.85", negotiated_dollar=".76",
               methodology="percent of total billed charges")
    assert got[:4] == ("percent_of_charges", pytest.approx(26.67), "pct_of_charges",
                       "pct_derived")


def test_dollar_sign_fraction_without_gross_is_a_percentage(con):
    got = rate(con, negotiated_dollar="$0.76", methodology="percent of total billed charges")
    assert got[:4] == ("percent_of_charges", pytest.approx(76.0), "pct_of_charges",
                       "pct_in_dollar_field")
    assert got[4] is None


def test_sub_dollar_fee_schedule_stays_in_dollars(con):
    got = rate(con, gross_charge="1.2", negotiated_dollar="0.69", methodology="fee schedule")
    assert got[:4] == ("flat_dollar", pytest.approx(0.69), "usd", "ok")


def test_fractional_percentage_is_rescaled(con):
    got = rate(con, gross_charge="100", negotiated_percentage="0.76",
               methodology="percent of total billed charges")
    assert got[1] == pytest.approx(76.0)
    assert got[3] == "pct_rescaled"


def test_percent_read_from_notes(con):
    got = rate(con, gross_charge="3788.32", negotiated_dollar="2996.56",
               methodology="percent of total billed charges",
               notes="Reimbursement will be 79.1% of billable gross charges.")
    assert got[1] == pytest.approx(79.1)


@pytest.mark.parametrize("methodology, algorithm, dollar, pct, expected", [
    ("case rate", None, "6199", None, "case_rate"),
    ("per diem", None, "3100", None, "per_diem"),
    ("fee schedule", None, "302.6", None, "flat_dollar"),
    ("other", "Per diem rate of $2,500 for days 1-3", "2500", None, "per_diem"),
    ("other", "APC Price ($0.33). Final payment is multiplied by 105%.", "0.35", None, "flat_dollar"),
    (None, None, None, "65", "percent_of_charges"),
    ("other", "see contract", None, None, "other"),
])
def test_pricing_method(con, methodology, algorithm, dollar, pct, expected):
    got = rate(con, gross_charge="5000", methodology=methodology, negotiated_algorithm=algorithm,
               negotiated_dollar=dollar, negotiated_percentage=pct)
    assert got[0] == expected


def test_rate_above_gross_charge_is_flagged(con):
    got = rate(con, gross_charge="100", negotiated_dollar="150", methodology="fee schedule")
    assert got[3] == "above_gross"


def test_unknown_method_is_never_compared(con):
    got = rate(con, gross_charge="100", methodology="other", negotiated_algorithm="see contract")
    assert got[1] is None
    assert got[3] == "method_unknown"


def test_missing_billing_class_is_assumed_facility(con):
    got = rate(con, billing_class=None, gross_charge="100", negotiated_dollar="50",
               methodology="fee schedule")
    assert got[3] == "billing_class_assumed"
    assert con.execute("SELECT billing_class FROM fact_rate").fetchone()[0] == "facility"
