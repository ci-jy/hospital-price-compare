import pytest

from hpc.discover import match_location, parse_cms_hpt
from tests.conftest import FIXTURES


def test_parses_real_cms_hpt_blocks():
    blocks = parse_cms_hpt((FIXTURES / "cms-hpt.txt").read_text())
    assert len(blocks) == 2
    assert blocks[1]["mrf-url"].endswith("saint-alphonsus-medical-center-nampa_standardcharges.zip")


def test_location_match_ignores_trailing_whitespace():
    # The real file pads location names with spaces.
    blocks = parse_cms_hpt((FIXTURES / "cms-hpt.txt").read_text())
    block = match_location(blocks, "Saint Alphonsus Regional Medical Center")
    assert "regional-medical-center-boise" in block["mrf-url"]


def test_blocks_without_blank_lines_still_split():
    text = "location-name: A\nmrf-url: https://a/x.csv\nlocation-name: B\nmrf-url: https://b/y.json\n"
    assert [b["location-name"] for b in parse_cms_hpt(text)] == ["A", "B"]


def test_unknown_location_raises():
    with pytest.raises(LookupError):
        match_location(parse_cms_hpt("location-name: A\nmrf-url: u\n"), "B")


def test_sources_csv_drops_signed_query_string(tmp_path):
    from hpc.discover import read_sources, write_sources
    out = tmp_path / "sources.csv"
    write_sources([{"hospital_id": "wv", "hospital_name": "WV", "location_name": "WV",
                    "index_url": "i", "source_page_url": "", "fetched_at": "t",
                    "mrf_url": "https://x.blob.core.windows.net/c/f.json?sv=1&sig=SECRET"}], out)
    assert read_sources(out)["wv"]["mrf_url"] == "https://x.blob.core.windows.net/c/f.json"
    assert "SECRET" not in out.read_text()


def test_fixture_has_no_real_contacts():
    text = (FIXTURES / "cms-hpt.txt").read_text()
    assert "@saintalphonsus.org" not in text
