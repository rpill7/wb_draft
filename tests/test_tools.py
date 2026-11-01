import pytest
from fastmcp import Client

from ma_engine.mcp_server import tools
from ma_engine.mcp_server.server import mcp
from ma_engine.data.prepare import fmt_money, fmt_multiple, fmt_pct

HCS = "Healthcare Services"
MID = "$100M–$400M"
VALID_IDS = tools.get_client().transaction_ids


def all_results():
    return [
        tools.get_dataset_overview(),
        tools.get_sector_overview(HCS),
        tools.get_acquirer_profile("blackstone"),
        tools.get_acquirer_deals("blackstone"),
        tools.get_valuation_comps(HCS, "sector_only", MID),
        tools.get_valuation_comps(HCS, "sector_plus_adjacent", "All sizes"),
        tools.search_deals(sectors=[HCS], size_band=MID),
    ]


def assert_deal_displays(deal):
    for column, fmt in [("deal_size_mm", fmt_money), ("ev_ebitda_multiple", fmt_multiple),
                        ("ev_revenue_multiple", fmt_multiple), ("ebitda_margin_pct", fmt_pct)]:
        assert deal[column]["display"] == fmt(deal[column]["value"])


def test_every_result_has_shape_and_valid_source_ids():
    for result in all_results():
        assert set(result) == {"data", "source_ids", "warnings"}
        assert set(result["source_ids"]) <= VALID_IDS
    assert all(r["source_ids"] for r in all_results()[1:])


def test_dataset_overview_lists_allowed_values():
    data = tools.get_dataset_overview()["data"]
    assert data["row_count"] == 500
    assert sum(data["sectors"].values()) == 500
    assert data["outcomes"]["Rumored"] == 10
    assert data["size_bands"]["labels"] == ["Under $100M", MID, "$400M–$1B", "Over $1B"]


def test_sector_overview():
    result = tools.get_sector_overview(HCS)
    data = result["data"]
    assert data["deal_count"]["value"] == 46 and len(result["source_ids"]) == 46
    assert data["ebitda_margin_median"]["display"] == "15.5%"
    adjacent = [s["sector"] for s in data["other_sectors"] if s["is_adjacent"]]
    assert adjacent == ["Physician Groups", "Behavioral Health", "Home Health/Hospice", "Dental"]
    assert HCS not in [s["sector"] for s in data["other_sectors"]]
    first = data["other_sectors"][0]
    assert set(first) == {"sector", "is_adjacent", "assumption"}
    assert first["assumption"]["closeness_display"] == "0.8" and first["assumption"]["reason"]
    assert any("assumption" in w and "interpretation" in w for w in result["warnings"])


def test_acquirer_profile_normalizes_key_and_warns_on_non_closed():
    result = tools.get_acquirer_profile("  BlackStone ")
    assert result["data"]["acquirer_key"] == "blackstone"
    assert result["data"]["deal_count"]["value"] == 25
    assert any("MA-2023-0456" in w and "Rumored" in w for w in result["warnings"])


def test_acquirer_deals_closed_only_by_default_and_newest_first():
    result = tools.get_acquirer_deals("blackstone", limit=25)
    deals = result["data"]["deals"]
    assert len(deals) == 22 and all(d["is_closed"] for d in deals)
    assert any("closed_only=false" in w for w in result["warnings"])
    order = [(d["deal_year"], d["deal_quarter"]) for d in deals]
    assert order == sorted(order, reverse=True)
    for deal in deals:
        assert_deal_displays(deal)

    everything = tools.get_acquirer_deals("blackstone", closed_only=False, limit=25)
    outcomes = {d["outcome"] for d in everything["data"]["deals"]}
    assert len(everything["data"]["deals"]) == 25 and "Withdrawn" in outcomes
    assert tools.NOT_CLOSED_WARNING in everything["warnings"]


def test_acquirer_deals_sector_filter():
    deals = tools.get_acquirer_deals("blackstone", sectors=[HCS])["data"]["deals"]
    assert {d["transaction_id"] for d in deals} == {"MA-2021-0195", "MA-2021-0407"}
    assert all(d["sector"] == HCS for d in deals)
    order = [(d["deal_year"], d["deal_quarter"]) for d in deals]
    assert order == sorted(order, reverse=True)


def test_valuation_comps_sector_only_mid_band():
    result = tools.get_valuation_comps(HCS, "sector_only", MID)
    data = result["data"]
    assert data["ev_ebitda"]["sample_size"]["value"] == 10
    assert data["ev_ebitda"]["median"]["display"] == "12.1x"
    assert data["ev_revenue"]["median"]["display"] == "2.9x"
    assert data["ebitda_margin_median"]["display"] == "15.2%"
    assert len(result["source_ids"]) == 10
    closed = tools.get_client().transactions.set_index("transaction_id").loc[result["source_ids"]]
    assert closed["is_closed"].all()
    for stat in ["p25", "median", "p75"]:
        assert data["ev_ebitda"][stat]["display"] == fmt_multiple(data["ev_ebitda"][stat]["value"])


def test_valuation_comps_adjacent_scope_is_wider():
    data = tools.get_valuation_comps(HCS, "sector_plus_adjacent", MID)["data"]
    assert data["ev_ebitda"]["sample_size"]["value"] == 75
    assert len(data["sectors_included"]) == 5


def test_search_deals_filters_and_caps():
    result = tools.search_deals(sectors=[HCS], size_band=MID, limit=25)
    deals = result["data"]["deals"]
    assert len(deals) == 12
    assert all(d["sector"] == HCS and d["size_band"] == MID for d in deals)
    assert tools.NOT_CLOSED_WARNING in result["warnings"]

    capped = tools.search_deals()
    assert capped["data"]["returned"] == tools.DEFAULT_LIMIT
    assert any("Showing 10 of 500" in w for w in capped["warnings"])
    assert tools.search_deals(limit=25)["data"]["returned"] == 25


def test_empty_results_are_normal_responses():
    result = tools.search_deals(sectors=["Dental"], outcomes=["Rumored"], geography="Great Plains",
                                size_band="Over $1B")
    assert result["data"]["deals"] == [] and result["source_ids"] == []
    assert "No matching deals for these filters." in result["warnings"]


@pytest.mark.parametrize("call, message", [
    (lambda: tools.get_sector_overview("Hospitals"), "Unknown sector 'Hospitals'"),
    (lambda: tools.get_valuation_comps(HCS, "everything", MID), "Unknown scope"),
    (lambda: tools.get_valuation_comps(HCS, "sector_only", "$200M"), "Unknown size_band"),
    (lambda: tools.search_deals(outcomes=["Completed"]), "Unknown outcome"),
    (lambda: tools.search_deals(acquirer_type="PE"), "Unknown acquirer_type"),
    (lambda: tools.search_deals(deal_types=["Buyout"]), "Unknown deal_type"),
    (lambda: tools.search_deals(geography="Texas"), "Unknown geography"),
    (lambda: tools.search_deals(size_band="All sizes"), "Unknown size_band"),
    (lambda: tools.get_acquirer_deals("blackstone", sectors=["Dentistry"]), "Unknown sector"),
])
def test_unknown_values_raise_with_allowed_list(call, message):
    with pytest.raises(ValueError, match="Allowed values") as error:
        call()
    assert message in str(error.value)


def test_unknown_acquirer_suggests_keys():
    with pytest.raises(ValueError, match="Keys containing 'black'") as error:
        tools.get_acquirer_profile("Black")
    assert "blackstone" in str(error.value)
    with pytest.raises(ValueError, match="Use search_deals"):
        tools.get_acquirer_deals("zzz unknown buyer")


@pytest.mark.parametrize("limit", [0, 26, -1, True, 2.5])
def test_limit_is_validated(limit):
    with pytest.raises(ValueError, match="limit must be"):
        tools.search_deals(limit=limit)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_fastmcp_in_memory_client():
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert names == {"get_dataset_overview", "get_sector_overview", "get_acquirer_profile",
                         "get_acquirer_deals", "get_valuation_comps", "search_deals",
                         "rank_acquirers"}

        result = await client.call_tool("get_valuation_comps", {
            "sector": HCS, "scope": "sector_only", "size_band": MID,
        })
        assert result.structured_content["data"]["ev_ebitda"]["median"]["display"] == "12.1x"

        failed = await client.call_tool("get_sector_overview", {"sector": "Hospitals"},
                                        raise_on_error=False)
        assert failed.is_error
        assert "Unknown sector 'Hospitals'" in failed.content[0].text


def test_search_distinguishes_complete_from_truncated_results():
    complete = tools.search_deals(sectors=[HCS], size_band=MID, outcomes=["Closed"], limit=25)["data"]
    assert complete["returned"] == complete["total_matches"] == 10
    assert complete["truncated"] is False
    limited = tools.search_deals(sectors=[HCS], size_band=MID, outcomes=["Closed"], limit=2)["data"]
    assert limited["returned"] == 2 and limited["total_matches"] == 10
    assert limited["truncated"] is True
