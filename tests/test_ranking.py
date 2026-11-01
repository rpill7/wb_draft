import inspect
from collections import Counter
from types import SimpleNamespace

import pandas as pd
import pytest
from fastmcp import Client
from pydantic import ValidationError

from ma_engine import ranking
from ma_engine.clients.data_client import get_client
from ma_engine.config import DEFAULT_TARGET, REPORT_COUNT
from ma_engine.mcp_server.server import mcp
from ma_engine.models import Target
from ma_engine.prepare import NON_REGIONAL_GEOGRAPHIES, build_similarity

HCS = "Healthcare Services"
TARGET = Target(sector=HCS, ev_mm=200, ownership="Private", geography="Any region",
                margin_profile="strong")


# ---------- small fixture client ----------

def deal(tid, key, sector=HCS, size=200.0, year=2023, outcome="Closed",
         buyer_type="Financial Sponsor", ownership="Private", geography="Midwest",
         margin="above", tags=("Platform Build",), ev_ebitda=12.0, ev_revenue=2.5):
    return {
        "transaction_id": tid, "acquirer_key": key, "acquirer_type": buyer_type,
        "sector": sector, "deal_size_mm": size, "deal_year": year, "outcome": outcome,
        "is_closed": outcome == "Closed", "target_ownership_pre": ownership,
        "geography": geography, "is_regional": geography not in NON_REGIONAL_GEOGRAPHIES,
        "margin_vs_sector": margin, "rationale_tags": list(tags),
        "ev_ebitda_multiple": ev_ebitda, "ev_revenue_multiple": ev_revenue,
    }


def fake_client(rows):
    frame = pd.DataFrame(rows)
    profiles = {
        key: {"display_name": key.title(), "acquirer_type": group["acquirer_type"].iloc[0],
              "acquirer_types_seen": sorted(set(group["acquirer_type"]))}
        for key, group in frame.groupby("acquirer_key")
    }
    return SimpleNamespace(transactions=frame, sector_similarity=build_similarity(),
                           sector_stats=get_client().sector_stats, acquirer_profiles=profiles)


def ranked(rows, target=TARGET, **kwargs):
    return ranking.rank_acquirers(target, client=fake_client(rows), **kwargs)["data"]["buyers"]


def by_key(buyers):
    return {b["acquirer_key"]: b for b in buyers}


def signal(buyer, name):
    return next(s for s in buyer["signals"] if s["name"] == name)


# ---------- fixture tests ----------

def test_strong_direct_match_beats_weak_unrelated():
    rows = [deal(f"A-{i}", "alpha", size=s) for i, s in enumerate([180.0, 210.0, 250.0])]
    rows += [deal("B-1", "beta", sector="Dental", size=5000.0, year=2015, ownership="Public",
                  geography="National", margin="below", tags=())]
    rows += [deal("C-1", "pharma only", sector="Pharma/Biotech")]
    buyers = ranked(rows)
    assert [b["acquirer_key"] for b in buyers] == ["alpha", "beta"]
    assert buyers[0]["score"]["value"] > buyers[1]["score"]["value"]


def test_closed_deals_count_and_pending_do_not():
    rows = [deal("A-1", "alpha"), deal("A-2", "alpha", year=2022)]
    rows += [deal("G-1", "gamma"), deal("G-2", "gamma", year=2022)]
    rows += [deal(f"G-P{i}", "gamma", outcome=o) for i, o in enumerate(["Pending", "Withdrawn"])]
    rows += [deal("P-1", "pending only", outcome="Pending")]
    buyers = by_key(ranked(rows))
    assert set(buyers) == {"alpha", "gamma"}
    assert buyers["alpha"]["score"] == buyers["gamma"]["score"]
    assert buyers["gamma"]["weak_signal_ids"] == ["G-P0", "G-P1"]
    assert buyers["gamma"]["supporting_closed_ids"] == ["G-1", "G-2"]
    assert any("did not close" in w for w in buyers["gamma"]["warnings"])
    assert signal(buyers["gamma"], "repeat_activity")["value"] == signal(
        buyers["alpha"], "repeat_activity")["value"]


def test_adjacent_sector_buyer_survives_and_far_sector_does_not():
    rows = [deal("D-1", "delta", sector="Physician Groups"),
            deal("D-2", "delta", sector="Dental"),
            deal("R-1", "revenue cycle", sector="Revenue Cycle")]
    buyers = ranked(rows)
    assert [b["acquirer_key"] for b in buyers] == ["delta"]
    assert any("adjacent sectors only" in w for w in buyers[0]["warnings"])


def test_size_fit_treats_over_and_under_the_same():
    rows = [deal("U-1", "under", size=100.0), deal("O-1", "over", size=400.0)]
    buyers = by_key(ranked(rows))
    assert signal(buyers["under"], "size_fit")["value"] == pytest.approx(
        signal(buyers["over"], "size_fit")["value"])
    assert buyers["under"]["score"]["value"] == pytest.approx(buyers["over"]["score"]["value"])


def test_one_deal_buyer_does_not_beat_steady_record():
    rows = [deal("S-1", "single")]
    rows += [deal(f"R-{i}", "record", year=2018 + i, margin="below" if i % 2 else "above")
             for i in range(6)]
    buyers = ranked(rows)
    assert buyers[0]["acquirer_key"] == "record"
    single = by_key(buyers)["single"]
    assert single["starting_conviction"]["level"] == "Low"
    assert any("Only 1 relevant closed deal" in w for w in single["warnings"])


def test_repeat_activity_counts_direct_deals_more_than_adjacent():
    rows = [deal(f"D-{i}", "direct", year=2020 + i) for i in range(2)]
    rows += [deal(f"A-{i}", "adjacent", sector="Dental", year=2020 + i) for i in range(2)]
    buyers = by_key(ranked(rows))
    direct, adjacent = (signal(buyers[k], "repeat_activity") for k in ("direct", "adjacent"))
    assert direct["value"] == pytest.approx(2 / (2 + ranking.REPEAT_HALF_CREDIT), abs=1e-4)
    assert adjacent["value"] == pytest.approx(1.4 / (1.4 + ranking.REPEAT_HALF_CREDIT), abs=1e-4)
    assert direct["explanation"].startswith(
        "2 direct + 0 adjacent relevant closed deal(s) = 2.0 weighted")
    assert adjacent["explanation"].startswith(
        "0 direct + 2 adjacent relevant closed deal(s) = 1.4 weighted")
    assert buyers["direct"]["direct_closed_count"]["value"] == 2
    assert buyers["adjacent"]["direct_closed_count"]["value"] == 0


def test_signals_show_weight_and_scored_sector_fit():
    rows = [deal("D-1", "mixed"), deal("A-1", "mixed", sector="Dental")]
    buyer = ranked(rows)[0]
    for s in buyer["signals"]:
        assert s["weight_display"] == f"{ranking.WEIGHTS[s['name']]:.2f}"
    fit = signal(buyer, "sector_fit")
    assert "average sector closeness 0.85 before the blend" in fit["explanation"]
    assert f"{fit['value_display']} after it (the value scored)" in fit["explanation"]


def test_sensitivity_variants_and_constants_restored():
    rows = [deal("A-1", "alpha"), deal("A-2", "alpha", year=2022),
            deal("D-1", "dental", sector="Dental"), deal("R-1", "revenue", sector="Revenue Cycle")]
    before = (dict(ranking.WEIGHTS), ranking.PRIOR_DEALS, ranking.MIN_PER_TYPE)
    table = {r["variant"]: r for r in ranking.sensitivity(TARGET, client=fake_client(rows))}
    assert (ranking.WEIGHTS, ranking.PRIOR_DEALS, ranking.MIN_PER_TYPE) == before
    assert len(table) == 6 + len(ranking.WEIGHTS)
    assert table["base"]["top"] == ["Alpha", "Dental"] and table["base"]["overlap"] == "2/2"
    assert table["adjacency 0.5"]["names_in"] == ["Revenue"]
    assert table["adjacency 0.75"]["names_out"] == ["Dental"]
    assert table["adjacency 0.75"]["overlap"] == "1/2"


def test_margin_improvement_counts_only_for_any_margin_profile():
    rows = [deal("M-1", "margin", tags=("Margin Improvement",)), deal("M-2", "margin", tags=())]
    strong = signal(ranked(rows)[0], "rationale_fit")
    anyone = signal(ranked(rows, target=TARGET.model_copy(update={"margin_profile": "any"}))[0],
                    "rationale_fit")
    assert strong["source_ids"] == [] and "Margin Improvement" not in strong["explanation"]
    assert anyone["source_ids"] == ["M-1"] and "Margin Improvement 1" in anyone["explanation"]
    assert anyone["value"] > strong["value"]


def test_missing_multiple_warns_and_is_not_zeroed():
    rows = [deal("M-1", "missing", ev_ebitda=None), deal("M-2", "missing")]
    buyer = ranked(rows)[0]
    assert any("no stated EV/EBITDA multiple: M-1" in w for w in buyer["warnings"])


# ---------- real data ----------

@pytest.fixture(scope="module")
def default_result():
    return ranking.rank_acquirers(Target.default())


def test_target_model_validates_and_matches_config():
    assert Target.default().model_dump() == {**DEFAULT_TARGET, "ev_mm": 200.0}
    for bad in [{"sector": "Hospitals"}, {"ev_mm": 0}, {"ownership": "Family"},
                {"geography": "Texas"}, {"margin_profile": "weak"}]:
        with pytest.raises(ValidationError):
            Target(**{**DEFAULT_TARGET, **bad})


def test_shape_order_and_breakdown(default_result):
    assert set(default_result) == {"data", "source_ids", "warnings"}
    data = default_result["data"]
    buyers = data["buyers"]
    assert len(buyers) == 15 and [b["rank"] for b in buyers] == list(range(1, 16))
    by_score = sorted(buyers, key=lambda b: b["score_rank"])
    keys = [(-b["score"]["value"], -b["relevant_closed_count"]["value"], b["acquirer_key"])
            for b in by_score]
    assert keys == sorted(keys)
    assert sum(data["buyer_type_mix"].values()) == 15
    assert sum(ranking.WEIGHTS.values()) == pytest.approx(1.0)
    for buyer in buyers:
        assert [s["name"] for s in buyer["signals"]] == list(ranking.WEIGHTS)
        total = sum(s["contribution"] for s in buyer["signals"])
        assert total == pytest.approx(buyer["score"]["value"], abs=1e-3)
        assert all(0 <= s["value"] <= 1 for s in buyer["signals"])
        assert buyer["starting_conviction"]["rule"] == ranking.CONVICTION_RULE


def test_identical_output_on_repeat(default_result):
    assert ranking.rank_acquirers(Target.default()) == default_result


def test_excluded_keys_remove_buyers(default_result):
    top = default_result["data"]["buyers"][0]["acquirer_key"]
    result = ranking.rank_acquirers(Target.default(), excluded_keys=[top.upper()])
    keys = [b["acquirer_key"] for b in result["data"]["buyers"]]
    assert top not in keys and result["data"]["excluded_keys"] == [top]
    assert keys[0] == default_result["data"]["buyers"][1]["acquirer_key"]
    with pytest.raises(ValueError, match="Unknown acquirer_key"):
        ranking.rank_acquirers(Target.default(), excluded_keys=["no such buyer"])


def test_no_buyer_names_in_ranking_code():
    source = inspect.getsource(ranking).lower()
    names = {name.lower() for p in get_client().acquirer_profiles.values()
             for name in [p["display_name"], *p["name_variants"]]}
    assert not [name for name in names if name in source]


def test_all_conviction_levels_in_default_top_10(default_result):
    top = default_result["data"]["buyers"][:REPORT_COUNT]
    assert {b["starting_conviction"]["level"] for b in top} == {"High", "Medium", "Low"}


def test_conviction_follows_percentile_bars_and_near_size_rule(default_result):
    bars = default_result["data"]["conviction_thresholds"]
    assert bars["high"]["value"] >= bars["medium"]["value"]
    for buyer in ranking.score_candidates(Target.default()):
        conviction = buyer["starting_conviction"]
        score, near = buyer["score"]["value"], buyer["near_size_closed_count"]["value"]
        if near < ranking.MIN_NEAR_SIZE_DEALS:
            assert conviction["level"] == "Low"
        elif score >= bars["high"]["value"]:
            assert conviction["level"] == "High"
        elif score >= bars["medium"]["value"]:
            assert conviction["level"] == "Medium"
        else:
            assert conviction["level"] == "Low"
        assert bars["high"]["display"] in conviction["reason"]
        assert bars["medium"]["display"] in conviction["reason"]


def test_rank_and_score_rank_are_consistent(default_result):
    data = default_result["data"]
    buyers = data["buyers"]
    head, tail = buyers[:REPORT_COUNT], buyers[REPORT_COUNT:]
    for part in (head, tail):
        assert [b["score_rank"] for b in part] == sorted(b["score_rank"] for b in part)
    promoted, displaced = set(data["promoted_keys"]), set(data["displaced_keys"])
    expected_head = ({b["acquirer_key"] for b in ranking.score_candidates(Target.default())
                      [:REPORT_COUNT]} - displaced) | promoted
    assert {b["acquirer_key"] for b in head} == expected_head
    for buyer in buyers:
        moved = buyer["acquirer_key"] in promoted | displaced | set(data["reserve_added_keys"])
        assert (buyer["selection_note"] is not None) == moved


def test_default_promotions_have_direct_deals_and_reserves_hold_both_types(default_result):
    data = default_result["data"]
    buyers = by_key(data["buyers"])
    assert data["promoted_keys"]
    assert all(buyers[k]["direct_closed_count"]["value"] > 0 for k in data["promoted_keys"])
    reserves = data["buyers"][REPORT_COUNT:]
    assert {b["acquirer_type"] for b in reserves} == set(ranking.BUYER_TYPES)


def test_conviction_reason_uses_three_decimals(default_result):
    bars = default_result["data"]["conviction_thresholds"]
    assert all(len(bars[level]["display"].split(".")[1]) == 3 for level in ("high", "medium"))
    for buyer in default_result["data"]["buyers"]:
        reason = buyer["starting_conviction"]["reason"]
        expected = f"{ranking.round_half_up(buyer['score']['value'], 3):.3f}"
        assert reason.startswith(f"Score {expected};")
        assert f"High bar {bars['high']['display']} " in reason
    # Scores that round to the same 2-decimal value are now told apart.
    assert ranking._three_places(0.6148) == "0.615" and ranking._three_places(0.6098) == "0.610"


# ---------- buyer-type coverage (fixtures) ----------

def type_pool(sponsors: int, strategics: int, strategic_size: float = 2000.0):
    rows = []
    for i in range(sponsors):
        rows += [deal(f"S{i}-{j}", f"sponsor {i:02d}", year=2020 + j) for j in range(4)]
    for i in range(strategics):
        rows += [deal(f"T{i}-1", f"strategic {i}", sector="Dental", size=strategic_size,
                      buyer_type="Strategic")]
    return rows


def test_missing_type_is_promoted_into_top_10_with_fallback_warning():
    result = ranking.rank_acquirers(TARGET, client=fake_client(type_pool(12, 4)))
    data, buyers = result["data"], result["data"]["buyers"]
    head = buyers[:REPORT_COUNT]
    assert Counter(b["acquirer_type"] for b in head)["Strategic"] == ranking.MIN_PER_TYPE
    assert data["promoted_keys"] == ["strategic 0", "strategic 1", "strategic 2"]
    assert data["displaced_keys"] == ["sponsor 09", "sponsor 08", "sponsor 07"]
    assert len(buyers) == 15
    promoted = by_key(buyers)["strategic 0"]
    assert promoted["score_rank"] == 13
    assert promoted["selection_note"] == (
        "Included to ensure at least 3 Strategic buyers in the top 10; score rank 13 of 16 "
        "candidates. It has no closed Healthcare Services deal; no Strategic candidate with one "
        "was left.")
    assert [b["acquirer_key"] for b in buyers[REPORT_COUNT:REPORT_COUNT + 3]] == [
        "sponsor 07", "sponsor 08", "sponsor 09"]
    assert ("Not enough Strategic candidates have a closed Healthcare Services deal, so 3 "
            "Strategic buyer(s) without one were added to the top 10 on score alone: Strategic 0, "
            "Strategic 1, Strategic 2.") in result["warnings"]


def test_coverage_prefers_strategic_with_a_direct_deal():
    rows = type_pool(12, 4) + [deal("T9-1", "strategic direct", size=5000.0, year=2012,
                                    buyer_type="Strategic", ownership="Public",
                                    geography="National", margin="below", tags=())]
    result = ranking.rank_acquirers(TARGET, client=fake_client(rows))
    data, buyers = result["data"], by_key(result["data"]["buyers"])
    direct = buyers["strategic direct"]
    assert direct["score_rank"] > buyers["strategic 2"]["score_rank"]
    assert data["promoted_keys"] == ["strategic direct", "strategic 0", "strategic 1"]
    assert direct["selection_note"].endswith("It has 1 closed Healthcare Services deal(s).")
    assert any("so 2 Strategic buyer(s) without one were added to the top 10" in w
               for w in result["warnings"])


def test_no_fallback_warning_when_direct_strategics_suffice():
    rows = type_pool(12, 0)
    rows += [deal(f"T{i}-1", f"strategic {i}", size=3000.0, buyer_type="Strategic")
             for i in range(3)]
    result = ranking.rank_acquirers(TARGET, client=fake_client(rows))
    assert sorted(result["data"]["promoted_keys"]) == ["strategic 0", "strategic 1", "strategic 2"]
    assert not any("Not enough" in w for w in result["warnings"])


def test_reserves_hold_both_types_when_available():
    result = ranking.rank_acquirers(TARGET, client=fake_client(type_pool(12, 4)))
    data, buyers = result["data"], result["data"]["buyers"]
    reserves = buyers[REPORT_COUNT:]
    assert {b["acquirer_type"] for b in reserves} == set(ranking.BUYER_TYPES)
    assert data["reserve_added_keys"] == ["strategic 3"]
    assert [b["acquirer_key"] for b in reserves] == [
        "sponsor 07", "sponsor 08", "sponsor 09", "sponsor 10", "strategic 3"]
    assert [b["rank"] for b in buyers] == list(range(1, 16))
    assert reserves[-1]["selection_note"].startswith(
        "Added as a reserve so a failed Strategic buyer can be replaced by one of the same type; "
        "score rank 16 of 16 candidates.")


def test_reserves_unchanged_when_pool_has_no_other_type():
    result = ranking.rank_acquirers(TARGET, client=fake_client(type_pool(16, 0)))
    assert result["data"]["reserve_added_keys"] == []
    assert {b["acquirer_type"] for b in result["data"]["buyers"]} == {"Financial Sponsor"}


def test_no_promotion_when_both_types_are_covered():
    rows = type_pool(6, 4, strategic_size=200.0)
    result = ranking.rank_acquirers(TARGET, client=fake_client(rows))
    data = result["data"]
    assert data["promoted_keys"] == [] and data["displaced_keys"] == []
    assert [b["rank"] for b in data["buyers"]] == [b["score_rank"] for b in data["buyers"]]
    assert all(b["selection_note"] is None for b in data["buyers"])


def test_no_promotion_when_pool_lacks_the_type():
    result = ranking.rank_acquirers(TARGET, client=fake_client(type_pool(12, 0)))
    assert result["data"]["promoted_keys"] == []
    assert any("Strategic" in w and "cannot be met" in w for w in result["warnings"])


def test_coverage_is_deterministic():
    rows = type_pool(12, 4)
    first = ranking.rank_acquirers(TARGET, client=fake_client(rows))
    assert ranking.rank_acquirers(TARGET, client=fake_client(rows)) == first


def test_every_source_id_exists(default_result):
    valid = get_client().transaction_ids
    ids = set(default_result["source_ids"])
    for buyer in default_result["data"]["buyers"]:
        ids |= set(buyer["supporting_closed_ids"]) | set(buyer["weak_signal_ids"])
        for s in buyer["signals"]:
            ids |= set(s["source_ids"])
    assert ids and ids <= valid
    closed = get_client().transactions.set_index("transaction_id")["is_closed"]
    for buyer in default_result["data"]["buyers"]:
        assert closed.loc[buyer["supporting_closed_ids"]].all()
        assert not closed.loc[buyer["weak_signal_ids"]].any()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_rank_acquirers_through_mcp(default_result):
    async with Client(mcp) as client:
        result = await client.call_tool("rank_acquirers", {"limit": 10})
        buyers = result.structured_content["data"]["buyers"]
        assert [b["acquirer_key"] for b in buyers] == [
            b["acquirer_key"] for b in default_result["data"]["buyers"][:10]]

        failed = await client.call_tool("rank_acquirers", {"sector": "Hospitals"},
                                        raise_on_error=False)
        assert failed.is_error and "Unknown sector 'Hospitals'" in failed.content[0].text
