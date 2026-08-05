import pytest

from hpc.resolve import PayerResolver, classify_plan_type, clean
from tests.conftest import ROOT


@pytest.fixture(scope="module")
def resolver():
    return PayerResolver.from_files(ROOT / "config" / "payers.yaml",
                                    ROOT / "data" / "payer_map.csv")


def test_clean_strips_ids_and_campus_prefix():
    assert clean("AETNA [1021]") == "AETNA"
    assert clean("HB NAMPA BP AETNA") == "BP AETNA"
    assert clean("BLUE CROSS - AK (PREMERA)") == "BLUE CROSS - AK"


@pytest.mark.parametrize("payer, plan, canonical, plan_type", [
    # Same carrier spelled three ways by three hospitals.
    ("BLUE CROSS IDAHO [1007]", "HB NAMPA SLHP BLUE CROSS CAREPOINT QHP", "Blue Cross of Idaho", "exchange"),
    ("Blue Cross", "PPO", "Blue Cross of Idaho", "commercial"),
    ("BLUE CROSS - ID", "BLUE CROSS OF IDAHO CONNECTED CARE", "Blue Cross of Idaho", "commercial"),
    # Longest alias wins.
    ("BLUE CROSS FEDERAL [1008]", "HB NAMPA BCI PPO FEP", "Blue Cross Federal Employee Program", "commercial"),
    # Generic payer column, network named in the plan column.
    ("COMMERCIAL [1028]", "HB NAMPA CIGNA", "Cigna", "commercial"),
    # Carrier names beat the program word "MEDICARE"; a carrier's Medicare plan is MA.
    ("AETNA MEDICARE TRANSPLANT", "MEDICARE", "Aetna", "medicare_advantage"),
    ("Blue Cross ", "MMCP", "Blue Cross of Idaho", "medicaid"),
    ("ID COUNTY PAYETTE", "MEDICAID COUNTY", "Idaho Medicaid", "medicaid"),
    ("Coventry First Health", "WCOMP", "First Health / Coventry", "workers_comp"),
])
def test_alias_resolution(resolver, payer, plan, canonical, plan_type):
    r = resolver.resolve(payer, plan)
    assert (r.payer_name, r.plan_type) == (canonical, plan_type)


def test_ma_means_medicaid_for_a_medicaid_only_payer(resolver):
    assert resolver.resolve("Molina", "MA").plan_type == "medicaid"


def test_bare_ma_is_left_ambiguous_not_guessed(resolver):
    # Humana sells Medicare Advantage and, in other states, Medicaid plans.
    r = resolver.resolve("Humana", "MA")
    assert (r.payer_name, r.plan_type, r.method) == ("Humana", "unknown", "ambiguous")
    # Spelled out, it is not ambiguous.
    assert resolver.resolve("Humana", "MA PPO").plan_type == "medicare_advantage"


def test_ma_with_medicaid_in_text_is_medicaid():
    assert classify_plan_type("Some Plan", "MA MEDICAID") == "medicaid"


def test_reviewed_mapping_wins(resolver):
    r = resolver.resolve("COMMERCIAL [1028]", "HB NAMPA AMPS")
    assert (r.payer_name, r.method) == ("Advanced Medical Pricing Solutions", "reviewed")


def test_fuzzy_match_catches_typos(resolver):
    r = resolver.resolve("AETNNA", "PPO")
    assert (r.payer_name, r.method) == ("Aetna", "fuzzy")
    assert r.score >= 88


def test_unknown_payer_is_kept_and_marked(resolver):
    r = resolver.resolve("ZEBRA BENEFITS TRUST", "PPO")
    assert r.method == "unresolved"
    assert r.payer_name == "Zebra Benefits Trust"


def test_unnamed_medicare_advantage_carrier(resolver):
    r = resolver.resolve("ADVANTRA FREEDOM MEDICARE ADVANTAGE", "MEDICARE")
    assert r.plan_type == "medicare_advantage"
    assert r.method == "unresolved"
