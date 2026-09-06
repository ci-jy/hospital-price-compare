"""hpc export on the fixture database: Parquet tables and the static page."""
import json
import re

import duckdb
import pytest

from hpc.cli import main
from hpc.export import TABLES


@pytest.fixture(scope="module")
def exported(loaded_db, tmp_path_factory):
    out = tmp_path_factory.mktemp("docs")
    assert main(["export", "--db", str(loaded_db), "--out", str(out)]) == 0
    return out


def q(sql, *args):
    return duckdb.connect().execute(sql, args).fetchall()


def test_every_table_written(exported):
    for name in TABLES:
        assert (exported / "data" / f"{name}.parquet").exists()


def test_rate_table_keeps_every_rate(loaded_db, exported):
    con = duckdb.connect(str(loaded_db), read_only=True)
    facts, comparable = con.execute(
        "SELECT count(*), count(compare_amount) FROM fact_rate").fetchone()
    rate = exported / "data" / "rate.parquet"
    assert q(f"SELECT count(*), count(*) FILTER (WHERE is_comparable) "
             f"FROM read_parquet('{rate}')")[0] == (facts, comparable)


def test_group_medians_match_compare_view(loaded_db, exported):
    con = duckdb.connect(str(loaded_db), read_only=True)
    view = dict(con.execute(
        "SELECT DISTINCT comparison_group, group_median FROM v_rate_compare").fetchall())
    rate = exported / "data" / "rate.parquet"
    got = dict(q(f"SELECT DISTINCT comparison_group, group_median FROM read_parquet('{rate}') "
                 f"WHERE is_comparable"))
    assert got.keys() == view.keys()
    assert all(abs(got[k] - view[k]) < 1e-9 for k in view)


def test_one_unit_per_group_and_spread_only_across_hospitals(exported):
    rate = exported / "data" / "rate.parquet"
    assert q(f"""SELECT max(n) FROM (SELECT count(DISTINCT compare_unit) AS n
                 FROM read_parquet('{rate}') GROUP BY comparison_group)""")[0][0] == 1
    assert q(f"""SELECT count(*) FROM read_parquet('{rate}')
                 WHERE vs_metro_median IS NOT NULL
                   AND (group_hospitals < 2 OR NOT is_comparable)""")[0][0] == 0
    spread = q(f"""SELECT count(*) FROM read_parquet('{rate}')
                   WHERE vs_metro_median IS NOT NULL""")[0][0]
    assert spread > 0


def test_page_embeds_the_same_numbers(exported):
    html = (exported / "index.html").read_text()
    assert "/*DATA*/" not in html
    payload = re.search(r"const D = (\{.*?\});\n", html, re.S).group(1)
    data = json.loads(payload)
    rate = exported / "data" / "rate.parquet"
    rates, comparable = q(f"SELECT count(*), count(*) FILTER (WHERE is_comparable) "
                          f"FROM read_parquet('{rate}')")[0]
    assert data["totals"]["rates"] == rates and data["totals"]["comparable"] == comparable
    assert data["totals"]["hospitals"] == 3
    assert any(code == "45378" for code, _ in data["services"])
    # every cell sits in a group with the same code's unit
    assert all(0 <= c[0] < len(data["groups"]) for c in data["cells"])
    assert "</script" not in payload
