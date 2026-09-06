"""The Power BI project in report/ against the Parquet files `hpc export` writes.

Every TMDL column must point at an exported column of a matching type, every
measure must reference columns and measures that exist, every relationship
must join a unique key, and every visual must use fields the model has. Run
against a fresh export of the fixture database, and against the committed
docs/data/ files when they are there.
"""
import json
import re
from pathlib import Path

import duckdb
import pytest

from hpc.export import TABLES, export

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "report" / "HospitalPrices.SemanticModel" / "definition"
REPORT = ROOT / "report" / "HospitalPrices.Report" / "definition"

# Parquet physical type (as DuckDB reports it) -> TMDL dataType.
TMDL_TYPES = {"VARCHAR": "string", "INTEGER": "int64", "BIGINT": "int64",
              "DOUBLE": "double", "BOOLEAN": "boolean"}


def parse_table(path: Path) -> dict:
    """A small reader for the parts of a TMDL table file the checks need."""
    table = {"columns": {}, "measures": {}, "partition_files": []}
    current = None
    for line in path.read_text().splitlines():
        depth = len(line) - len(line.lstrip("\t"))
        text = line.strip()
        if depth == 0 and text.startswith("table "):
            table["name"] = text.split(" ", 1)[1].strip("'")
        elif depth == 1 and text.startswith("column "):
            current = {"kind": "column", "name": text.split(" ", 1)[1].strip("'")}
            table["columns"][current["name"]] = current
        elif depth == 1 and text.startswith("measure "):
            m = re.match(r"measure\s+'?([^'=]+?)'?\s*=\s*(.*)$", text)
            current = {"kind": "measure", "name": m.group(1), "dax": m.group(2)}
            table["measures"][current["name"]] = current
        elif depth == 1:
            current = None
        elif current and current["kind"] == "measure" and depth >= 3:
            current["dax"] += "\n" + text
        elif current and depth == 2 and ":" in text:
            key, value = text.split(":", 1)
            current[key.strip()] = value.strip()
        files = re.findall(r'"([\w]+)\.parquet"', line)
        table["partition_files"] += files
    return table


@pytest.fixture(scope="module")
def model() -> dict:
    tables = {}
    for path in sorted((MODEL / "tables").glob("*.tmdl")):
        t = parse_table(path)
        tables[t["name"]] = t
    rels = re.findall(r"fromColumn:\s*(\w+)\.(\w+)\s+toColumn:\s*(\w+)\.(\w+)",
                      (MODEL / "relationships.tmdl").read_text())
    refs = re.findall(r"^ref table (\w+)", (MODEL / "model.tmdl").read_text(), re.M)
    return {"tables": tables, "relationships": rels, "refs": refs}


@pytest.fixture(scope="module")
def fixture_export(loaded_db, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("export")
    export(loaded_db, out, title="fixtures", memory_limit="256MB", threads=1)
    return out / "data"


DATA_DIRS = ["fixture_export"] + (["committed"] if (ROOT / "docs/data/rate.parquet").exists()
                                  else [])


@pytest.fixture(params=DATA_DIRS)
def data_dir(request) -> Path:
    if request.param == "committed":
        return ROOT / "docs" / "data"
    return request.getfixturevalue("fixture_export")


def parquet_columns(data_dir: Path, table: str) -> dict[str, str]:
    con = duckdb.connect()
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{data_dir / table}.parquet')"
                       ).fetchall()
    return {name: typ for name, typ, *_ in rows}


def test_model_lists_every_exported_table(model):
    assert set(model["tables"]) == set(TABLES)
    assert set(model["refs"]) == set(TABLES)
    for name, t in model["tables"].items():
        assert t["partition_files"] == [name], f"{name} partition reads {t['partition_files']}"
    assert "ParquetFolder" in (MODEL / "expressions.tmdl").read_text()


def test_every_tmdl_column_is_an_exported_column(model, data_dir):
    for name, t in model["tables"].items():
        exported = parquet_columns(data_dir, name)
        for col in t["columns"].values():
            src = col.get("sourceColumn")
            assert src in exported, f"{name}.{col['name']}: {src} not in {name}.parquet"
            assert col.get("dataType") == TMDL_TYPES[exported[src]], \
                f"{name}.{col['name']} is {col.get('dataType')} but Parquet has {exported[src]}"
        # and nothing exported is missing from the model
        missing = set(exported) - {c.get("sourceColumn") for c in t["columns"].values()}
        assert not missing, f"{name}.parquet columns not in the model: {missing}"


def test_measures_reference_existing_fields(model):
    tables = model["tables"]
    measures = {m for t in tables.values() for m in t["measures"]}
    assert {"Median Negotiated Rate", "Metro Median Rate", "Spread vs Metro Median",
            "Share of Comparable Rates"} <= measures
    for t in tables.values():
        for m in t["measures"].values():
            dax = re.sub(r"--[^\n]*", "", m["dax"])
            dax = re.sub(r'"[^"]*"', '""', dax)
            for table, field in re.findall(r"'?(\w+)'?\[([^\]]+)\]", dax):
                assert table in tables, f"{m['name']}: unknown table {table}"
                fields = set(tables[table]["columns"]) | set(tables[table]["measures"])
                assert field in fields, f"{m['name']}: {table}[{field}] not in the model"
            for field in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", dax):
                assert field in measures, f"{m['name']}: [{field}] is not a measure"
            for table in re.findall(r"\b(?:COUNTROWS|REMOVEFILTERS|ALL|VALUES)\s*\(\s*'?(\w+)'?\s*\)",
                                    dax):
                assert table in tables, f"{m['name']}: unknown table {table}"


def test_relationships_join_unique_keys(model, data_dir):
    con = duckdb.connect()
    for f_table, f_col, d_table, d_col in model["relationships"]:
        assert f_col in model["tables"][f_table]["columns"]
        assert d_col in model["tables"][d_table]["columns"]
        dim = f"read_parquet('{data_dir / d_table}.parquet')"
        fact = f"read_parquet('{data_dir / f_table}.parquet')"
        rows, keys = con.execute(f"SELECT count(*), count(DISTINCT {d_col}) FROM {dim}").fetchone()
        assert rows == keys, f"{d_table}.{d_col} is not unique"
        orphans = con.execute(f"""
            SELECT count(*) FROM {fact} f
            WHERE f.{f_col} IS NOT NULL AND f.{f_col} NOT IN (SELECT {d_col} FROM {dim})
        """).fetchone()[0]
        assert orphans == 0, f"{f_table}.{f_col} has {orphans} values missing from {d_table}"
    # every dimension reaches rate, so slicers filter the fact table
    related = {d for f, _, d, _ in model["relationships"] if f == "rate"}
    assert related == set(TABLES) - {"rate", "payer_string"}


def _fields(node):
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            if kind in node and "Property" in node[kind]:
                yield kind, node[kind]["Expression"]["SourceRef"]["Entity"], node[kind]["Property"]
        for v in node.values():
            yield from _fields(v)
    elif isinstance(node, list):
        for v in node:
            yield from _fields(v)


def test_report_has_three_pages_and_visuals_use_model_fields(model):
    pages = json.loads((REPORT / "pages" / "pages.json").read_text())["pageOrder"]
    assert pages == ["service_lookup", "payer_spread", "data_quality"]
    tables = model["tables"]
    for page in pages:
        visuals = list((REPORT / "pages" / page / "visuals").glob("*/visual.json"))
        assert visuals, f"page {page} has no visuals"
        for path in visuals:
            fields = list(_fields(json.loads(path.read_text())))
            assert fields, f"{path} uses no fields"
            for kind, table, prop in fields:
                key = "columns" if kind == "Column" else "measures"
                assert prop in tables[table][key], f"{path}: {table}.{prop} is not a {kind}"


def test_pbip_points_at_report_and_model():
    pbip = json.loads((ROOT / "report" / "HospitalPrices.pbip").read_text())
    assert pbip["artifacts"] == [{"report": {"path": "HospitalPrices.Report"}}]
    pbir = json.loads((ROOT / "report" / "HospitalPrices.Report" / "definition.pbir").read_text())
    target = (ROOT / "report" / "HospitalPrices.Report" /
              pbir["datasetReference"]["byPath"]["path"]).resolve()
    assert (target / "definition.pbism").exists() and (target / "definition").is_dir()
