import zipfile

import pytest

from hpc.ingest.detect import UnknownFormat, detect_format, extract_zip, sniff_bytes
from tests.conftest import FIXTURES


@pytest.mark.parametrize("name, expected", [
    ("wide_stlukes_nampa.csv", "wide"),
    ("tall_saint_alphonsus_nampa.csv", "tall"),
    ("west_valley.json", "json"),
])
def test_detects_each_encoding(name, expected):
    assert detect_format(FIXTURES / name) == expected


def test_zip_is_detected_and_extracted(tmp_path):
    archive = tmp_path / "hospital.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(FIXTURES / "tall_saint_alphonsus_nampa.csv", "820200896_standardcharges.csv")
    assert detect_format(archive) == "zip"
    out = extract_zip(archive, tmp_path / "hospital")
    assert out.name == "hospital.csv"
    assert detect_format(out) == "tall"


def test_csv_without_payer_columns_is_rejected():
    with pytest.raises(UnknownFormat):
        sniff_bytes(b"a,b\n1,2\ndescription,code|1,code|1|type\n")
