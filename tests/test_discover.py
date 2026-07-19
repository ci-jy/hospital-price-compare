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
