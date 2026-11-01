import csv
import json

import pandas as pd
import pytest

from ma_engine.data import prepare
from ma_engine.config import RAW_CSV

BASE_ROW = {
    "transaction_id": "MA-2020-0001",
    "target_company": "Alpha Care",
    "acquirer": "Blackstone",
    "sector": "Healthcare Services",
    "sub_sector": "Healthcare Services",
    "deal_year": "2020",
    "deal_quarter": "Q1",
    "deal_type": "Platform Investment",
    "geography": "Midwest",
    "financing_type": "All Cash",
    "deal_size_mm": "150.0",
    "target_revenue_mm": "100.0",
    "target_ebitda_mm": "15.0",
    "ebitda_margin_pct": "15.0",
    "revenue_growth_pct": "10.0",
    "ev_ebitda_multiple": "10.0",
    "ev_revenue_multiple": "1.5",
    "synergy_pct_of_deal": "5.0",
    "outcome": "Closed",
    "strategic_rationale_tags": "Scale|Geographic Expansion",
    "num_bidders": "2",
    "days_to_close": "120.0",
    "acquirer_type": "Financial Sponsor",
    "target_ownership_pre": "Private",
}


def make_raw(*overrides: dict) -> pd.DataFrame:
    """All-text fixture frame, like `load_raw` returns."""
    rows = [{**BASE_ROW, **o} for o in overrides]
    return pd.DataFrame(rows, columns=prepare.EXPECTED_COLUMNS).astype(str)


def fixture_raw() -> pd.DataFrame:
    return make_raw(
        {"transaction_id": "MA-2020-0001", "deal_size_mm": "100.0", "ev_ebitda_multiple": "10.0",
         "ev_revenue_multiple": "1.0", "ebitda_margin_pct": "10.0"},
        {"transaction_id": "MA-2021-0002", "deal_size_mm": "200.0", "ev_ebitda_multiple": "13.25",
         "ev_revenue_multiple": "2.0", "ebitda_margin_pct": "20.0"},
        {"transaction_id": "MA-2022-0003", "deal_size_mm": "300.0", "ev_ebitda_multiple": "-1",
         "ev_revenue_multiple": "3.0", "ebitda_margin_pct": "30.0", "acquirer": " blackstone "},
        {"transaction_id": "MA-2023-0004", "deal_size_mm": "250.0", "ev_ebitda_multiple": "9.0",
         "outcome": "Withdrawn", "days_to_close": ""},
        {"transaction_id": "MA-2024-0005", "deal_size_mm": "260.0", "ev_ebitda_multiple": "8.0",
         "outcome": "Rumored", "days_to_close": "", "acquirer": "Blackstone Inc"},
    )


# 1
def test_missing_column_gives_clear_error():
    raw = make_raw({}).drop(columns=["ev_ebitda_multiple"])
    with pytest.raises(ValueError, match="Missing expected columns: \\['ev_ebitda_multiple'\\]"):
        prepare.prepare(raw)


def test_missing_file_gives_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="Raw CSV not found"):
        prepare.load_raw(tmp_path / "nope.csv")


# 2
def test_duplicate_and_empty_ids_are_reported():
    raw = make_raw({"transaction_id": "MA-1"}, {"transaction_id": "MA-1"}, {"transaction_id": " "})
    report = prepare.check_ids(raw)
    assert report == {"unique": False, "empty_id_csv_lines": [4], "duplicate_ids": ["MA-1"]}
    with pytest.raises(ValueError, match="duplicates: \\['MA-1'\\]"):
        prepare.prepare(raw)


# 3
def test_name_normalization_groups_variants_but_not_different_companies():
    assert prepare.normalize_acquirer("  Bain   Capital ") == "bain capital"
    assert prepare.normalize_acquirer("BAIN CAPITAL") == "bain capital"
    assert prepare.normalize_acquirer("Bain Capital Ventures") != "bain capital"
    profiles = prepare.prepare(fixture_raw())["acquirer_profiles"]
    assert sorted(profiles) == ["blackstone", "blackstone inc"]
    assert profiles["blackstone"]["name_variants"] == ["Blackstone", "blackstone"]
    assert profiles["blackstone"]["display_name"] == "Blackstone"


# 4
def test_non_closed_outcomes_are_kept_and_marked():
    tx = prepare.prepare(fixture_raw())["transactions"]
    assert len(tx) == 5
    by_id = tx.set_index("transaction_id")
    assert by_id.loc["MA-2023-0004", "is_closed"] == False  # noqa: E712
    assert by_id.loc["MA-2024-0005", "outcome"] == "Rumored"
    assert by_id.loc["MA-2024-0005", "is_closed"] == False  # noqa: E712
    assert tx["is_closed"].sum() == 3


# 5
def test_tags_parse():
    assert prepare.parse_tags(" Scale | Cross-sell ||Platform Build ") == [
        "Scale", "Cross-sell", "Platform Build"]
    assert prepare.parse_tags("") == []
    tx = prepare.prepare(fixture_raw())["transactions"]
    assert json.loads(tx["rationale_tags"].iloc[0]) == ["Scale", "Geographic Expansion"]
    assert tx["strategic_rationale_tags"].iloc[0] == "Scale|Geographic Expansion"


# 6
def test_comps_use_closed_positive_multiples_and_separate_sample_sizes():
    out = prepare.prepare(fixture_raw())
    comps = out["valuation_comps"]
    row = comps[(comps["sector"] == "Healthcare Services") & (comps["scope"] == "sector_only")
                & (comps["size_band"] == "All sizes")].iloc[0]
    assert row["deal_count"] == 3
    assert row["ev_ebitda_count"] == 2  # MA-2022-0003 has a negative multiple
    assert row["ev_revenue_count"] == 3
    assert json.loads(row["ev_ebitda_source_ids"]) == ["MA-2020-0001", "MA-2021-0002"]
    assert row["sample_warning"] == "Thin sample (fewer than 3 deals): EV/EBITDA n=2"
    invalid = out["data_quality"]["invalid_values"]
    assert invalid == [{"transaction_id": "MA-2022-0003", "column": "ev_ebitda_multiple",
                        "value": "-1", "problem": "must be positive"}]
    tx = out["transactions"].set_index("transaction_id")
    assert tx.loc["MA-2022-0003", "ev_ebitda_multiple"] == "-1"  # raw kept, not zeroed


# 7
def test_medians_and_display_strings():
    assert prepare.fmt_money(185) == "$185.0M"
    assert prepare.fmt_money(14353.7) == "$14,353.7M"
    assert prepare.fmt_multiple(13.25) == "13.3x"
    assert prepare.fmt_pct(24.05) == "24.1%"
    assert prepare.fmt_pct(None) is None
    out = prepare.prepare(fixture_raw())
    comps = out["valuation_comps"]
    row = comps[(comps["sector"] == "Healthcare Services") & (comps["scope"] == "sector_only")
                & (comps["size_band"] == "$100M–$400M")].iloc[0]
    assert row["ev_ebitda_median"] == 11.63
    assert row["ev_ebitda_median_display"] == "11.6x"
    assert row["ev_revenue_median_display"] == "2.0x"
    assert row["ebitda_margin_median_display"] == "20.0%"
    size = out["acquirer_profiles"]["blackstone"]["closed_deal_size"]
    assert size["median"]["display"] == "$200.0M"
    assert size["largest"]["display"] == "$300.0M"


def test_display_is_rounded_once_from_the_true_value():
    frame = pd.DataFrame({"transaction_id": ["A", "B", "C"], "m": [2.0, 2.09, 3.0]})
    out = prepare._distribution(frame, "m", "m", prepare.fmt_multiple, 2)
    assert out["m_p25"] == 2.05  # true p25 is 2.045
    assert out["m_p25_display"] == "2.0x"  # not "2.1x" from the already-rounded 2.05
    pair = pd.DataFrame({"transaction_id": ["A", "B"], "m": [9.1, 9.2]})
    assert prepare.median_stat(pair, "m", prepare.fmt_multiple, 2)["display"] == "9.2x"  # 9.15


# 8
def test_statistics_carry_source_ids():
    out = prepare.prepare(fixture_raw())
    profile = out["acquirer_profiles"]["blackstone"]
    assert profile["deal_count"]["source_ids"] == ["MA-2020-0001", "MA-2021-0002",
                                                   "MA-2022-0003", "MA-2023-0004"]
    assert profile["closed_count"]["source_ids"] == ["MA-2020-0001", "MA-2021-0002",
                                                     "MA-2022-0003"]
    assert profile["non_closed_by_outcome"]["Withdrawn"]["source_ids"] == ["MA-2023-0004"]
    assert profile["closed_ev_ebitda_median"]["source_ids"] == ["MA-2020-0001", "MA-2021-0002"]
    assert profile["closed_ev_ebitda_median"]["n"] == 2
    assert profile["closed_deal_size"]["smallest"]["source_ids"] == ["MA-2020-0001"]
    stats = out["sector_stats"].set_index("sector").loc["Healthcare Services"]
    assert len(json.loads(stats["source_ids"])) == 5
    assert json.loads(stats["closed_source_ids"]) == ["MA-2020-0001", "MA-2021-0002", "MA-2022-0003"]


# 9
def test_two_runs_give_byte_identical_outputs(tmp_path):
    raw_csv = tmp_path / "raw.csv"
    fixture_raw().to_csv(raw_csv, index=False)
    prepare.write_outputs(raw_csv, tmp_path / "a")
    prepare.write_outputs(raw_csv, tmp_path / "b")
    files = sorted(p.name for p in (tmp_path / "a").iterdir())
    assert len(files) == 6
    for name in files:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_similarity_table_is_complete_and_symmetric():
    sim = prepare.build_similarity()
    assert len(sim) == len(prepare.SECTORS) ** 2
    scores = sim.set_index(["sector_a", "sector_b"])["closeness"]
    for a in prepare.SECTORS:
        assert scores[(a, a)] == 1.0
        for b in prepare.SECTORS:
            assert scores[(a, b)] == scores[(b, a)]
            assert 0 <= scores[(a, b)] <= 1


def test_real_csv_keeps_every_row_and_id(tmp_path):
    out = prepare.write_outputs(RAW_CSV, tmp_path)
    with open(RAW_CSV, newline="", encoding="utf-8") as f:
        raw_rows = list(csv.reader(f))
    with open(tmp_path / "transactions.csv", newline="", encoding="utf-8") as f:
        out_rows = list(csv.reader(f))
    assert len(out["transactions"]) == 500
    assert len(out_rows) == len(raw_rows) == 501
    assert all(o[:24] == r[:24] for o, r in zip(out_rows, raw_rows))
    assert out["data_quality"]["transaction_id_checks"]["unique"] is True
