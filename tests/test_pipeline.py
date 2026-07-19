"""End to end on the three fixture hospitals: ingest, check, compare."""
import duckdb

from hpc.cli import main


def test_every_hospital_and_encoding_loads(loaded_db):
    con = duckdb.connect(str(loaded_db), read_only=True)
    rows = con.execute("SELECT hospital_id, encoding, staged_rows FROM dim_hospital "
                       "ORDER BY hospital_id").fetchall()
    assert [(h, e) for h, e, _ in rows] == [("saint-alphonsus-nampa", "tall"),
                                            ("stlukes-nampa", "wide"),
                                            ("west-valley", "json")]
    assert all(n > 0 for *_, n in rows)
    staged = con.execute("SELECT count(*) FROM stg_rate").fetchone()[0]
    assert con.execute("SELECT count(*) FROM fact_rate").fetchone()[0] == staged


def test_no_comparison_group_mixes_methods(loaded_db):
    con = duckdb.connect(str(loaded_db), read_only=True)
    assert con.execute("SELECT max(methods_in_group), max(units_in_group) "
                       "FROM v_compare_summary").fetchone() == (1, 1)
    # Percent-of-charges rates and dollar rates exist for the same code, separately.
    methods = {m for (m,) in con.execute(
        "SELECT DISTINCT pricing_method FROM v_rate_compare WHERE code = '45378'").fetchall()}
    assert {"percent_of_charges", "flat_dollar", "case_rate"} <= methods


def test_check_command_passes(loaded_db, capsys):
    assert main(["check", "--db", str(loaded_db), "--min-hospitals", "3",
                 "--min-encodings", "3"]) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_check_command_fails_on_too_few_hospitals(loaded_db, capsys):
    assert main(["check", "--db", str(loaded_db), "--min-hospitals", "4"]) == 1
    assert "FAIL  hospital coverage" in capsys.readouterr().out


def test_compare_prints_every_hospital_with_method_and_flag(loaded_db, capsys):
    assert main(["compare", "--db", str(loaded_db), "--code", "45378", "--limit", "0"]) == 0
    out = capsys.readouterr().out
    for name in ("St. Luke's Nampa", "Saint Alphonsus Nampa", "West Valley"):
        assert name in out
    assert "percent of billed charges" in out and "case rate" in out
    assert "% of charges" in out and "$6,199.00" in out


def test_compare_csv_output(loaded_db, capsys):
    assert main(["compare", "--db", str(loaded_db), "--code", "45378", "--format", "csv"]) == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert "pricing_method" in header and "quality_flag" in header


def test_compare_unknown_code(loaded_db, capsys):
    assert main(["compare", "--db", str(loaded_db), "--code", "00000"]) == 1
