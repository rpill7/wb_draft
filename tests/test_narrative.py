"""Statistic citations, source numbers, cut-off repair and the narrative report.

No test calls a real LLM. The sentences below are taken from the real run
outputs/runs/20261031-214048, where the checker treated p25/p75 as 25 and 75
and rejected an unknown that quoted the target's own $200.0M.
"""

import json

import pytest

from ma_engine import agent, guardrails
from ma_engine.clients.data_client import comps_stat_id, get_client, rank_stat_id
from ma_engine.clients.llm_client import json_was_cut_off
from ma_engine.mcp_server import tools
from ma_engine.models import ReportDraft, Statement
from test_agent import FakeLLM, write_attempt
from test_guardrails import CALLS, EBITDA_ONLY, HCS, MID, fact, make

P25_SENTENCE = ("Healthcare Services, $100M–$400M, closed deals only: median EV/EBITDA 12.1x "
                "(n=10), p25 10.3x, p75 14.4x.")
UNKNOWN_TARGET = ("Historical activity does not show current appetite for a $200.0M Healthcare "
                  "Services target, so this is unknown.")


def test_stat_ids_are_stable_and_resolve():
    median = comps_stat_id(HCS, "sector_only", MID, "ev_ebitda", "median")
    assert median == "ST-COMPS-healthcare-services-sector_only-100-400-ev_ebitda-median"
    record = get_client().stat_record(median)
    assert record["display"] == "12.1x" and record["n"] == 10
    assert record["calculation"] == "median of EV/EBITDA"
    assert record["filters"]["closed_only"] is True and record["filters"]["size_band"] == MID
    assert record["source_ids"]
    again = comps_stat_id(HCS, "sector_only", MID, "ev_ebitda", "median")
    assert get_client().stat_record(again) == record

    shown = tools.get_valuation_comps(HCS, "sector_only", MID)["data"]["ev_ebitda"]
    assert shown["median"]["stat_id"] == median
    assert shown["median"]["display"] == "12.1x" and shown["p25"]["display"] == "10.3x"
    assert shown["p75"]["stat_id"] == comps_stat_id(HCS, "sector_only", MID, "ev_ebitda", "p75")


def test_key_metrics_come_from_the_buyer_profile():
    metrics = get_client().key_metrics("blackstone")
    assert [item["stat_id"] for item in metrics] == [
        rank for rank in (
            "ST-PROFILE-blackstone-closed-count",
            "ST-PROFILE-blackstone-closed-deal-size-median",
            "ST-PROFILE-blackstone-closed-deal-size-smallest",
            "ST-PROFILE-blackstone-closed-deal-size-largest",
            "ST-PROFILE-blackstone-closed-ev-ebitda-median",
            "ST-PROFILE-blackstone-closed-ev-revenue-median",
            "ST-PROFILE-blackstone-closed-private-target-count",
            "ST-PROFILE-blackstone-closed-median-bidders",
            "ST-PROFILE-blackstone-closed-median-days-to-close",
        )
    ]
    profile = tools.get_acquirer_profile("blackstone")["data"]
    assert profile["key_metrics"] == metrics
    assert profile["closed_deal_size"]["median"]["stat_id"] == metrics[1]["stat_id"]
    assert metrics[1]["display"] and metrics[1]["n"] and metrics[1]["source_ids"]


def test_ranking_signal_id_resolves_from_the_ranking():
    ranking = tools.rank_acquirers()["data"]
    buyer = next(item for item in ranking["buyers"] if item["acquirer_key"] == "blackstone")
    signal = next(item for item in buyer["signals"] if item["name"] == "size_fit")
    assert signal["stat_id"] == rank_stat_id("blackstone", "size_fit")
    assert signal["stat_id"] == "ST-RANK-blackstone-size_fit"
    record = get_client().stat_record(signal["stat_id"])
    assert record["calculation"] == signal["explanation"]
    assert record["display"] == signal["value_display"]
    assert record["source_ids"] == signal["source_ids"]


def _comps_ids():
    data = tools.get_valuation_comps(HCS, "sector_only", MID)["data"]["ev_ebitda"]
    return data["median"]["stat_id"], data["p25"]["stat_id"], data["p75"]["stat_id"]


def _buyer_evidence():
    ranked = tools.rank_acquirers()["data"]
    buyer = next(item for item in ranked["buyers"] if item["acquirer_key"] == "blackstone")
    results = [{"tool": name, "arguments": args, "result": getattr(tools, name)(**args)}
               for name, args in CALLS]
    return buyer, guardrails.build_evidence(buyer, results)


def test_percentile_labels_are_not_numbers():
    entry, evidence = _buyer_evidence()
    tokens = guardrails.number_tokens(
        "p25 10.3x, p75 14.4x, 25th percentile, 75th percentile, median, interquartile")
    assert tokens == {"10.3x", "14.4x"}
    median_id, p25_id, p75_id = _comps_ids()

    def bare(draft):
        draft["valuation_context"][0] = fact(P25_SENTENCE, "MA-2021-0195")

    issues = " ".join(guardrails.check(make(entry, bare), evidence))
    assert '"25"' not in issues and '"75"' not in issues
    assert f"Cite {median_id} for 12.1x" in issues
    assert f"Cite {p25_id} for 10.3x" in issues
    assert f"Cite {p75_id} for 14.4x" in issues

    def cited(draft):
        draft["valuation_context"][0] = fact(P25_SENTENCE, median_id, p25_id, p75_id)

    assert guardrails.check(make(entry, cited), evidence) == []


def test_ranking_explanation_numbers_are_backed_by_the_signal_id():
    """A real miss: 'within 2x' was rejected after the writer cited the ranking signal."""
    entry, _evidence = _buyer_evidence()
    ev = guardrails.build_evidence(entry, [])
    size = next(signal for signal in entry["signals"] if signal["name"] == "size_fit")
    report = make(entry, lambda draft: draft["strategic_fit"].__setitem__(
        0, fact(size["explanation"].split(";")[0] + ".", size["stat_id"])))
    issues = [issue for issue in guardrails.check(report, ev) if issue.startswith("strategic_fit[0]")]
    assert issues == [], issues


def test_wrong_statistic_fails_and_the_right_one_passes():
    entry, evidence = _buyer_evidence()
    revenue = comps_stat_id(HCS, "sector_only", MID, "ev_revenue", "median")

    def wrong(draft):
        draft["valuation_context"][0] = fact(
            "Closed Healthcare Services deals had a median EV/EBITDA of 12.1x (n=10).", revenue)

    issues = guardrails.check(make(entry, wrong), evidence)
    assert any(f"Cite {EBITDA_ONLY} for 12.1x" in issue for issue in issues)

    def right(draft):
        draft["valuation_context"][0] = fact(
            "Closed Healthcare Services deals had a median EV/EBITDA of 12.1x (n=10).", EBITDA_ONLY)

    assert guardrails.check(make(entry, right), evidence) == []


def test_target_number_is_allowed_in_an_unknown():
    entry, _evidence = _buyer_evidence()
    target = tools.rank_acquirers()["data"]["target"]
    with_target = guardrails.build_evidence(entry, [
        {"tool": "rank_acquirers", "arguments": {}, "result": {"data": {"target": target}}},
        *[{"tool": name, "arguments": args, "result": getattr(tools, name)(**args)}
          for name, args in CALLS]])

    def edit(draft):
        draft["risk_flags"][2] = {"risk": "Current appetite", "explanation": UNKNOWN_TARGET,
                                  "kind": "unknown", "source_ids": []}

    assert guardrails.check(make(entry, edit), with_target) == []


def test_sources_cover_every_citation_and_no_number_is_unbacked():
    entry, evidence = _buyer_evidence()
    report = guardrails.apply_check(make(entry), evidence)
    ranking = tools.rank_acquirers()["data"]
    sources = guardrails.build_sources([report], ranking)
    numbers, entries = sources["numbers"], sources["entries"]
    cited = [sid for sid in guardrails.citation_ids([report])]
    assert list(numbers) == list(dict.fromkeys(sid for sid in cited if sid in numbers))
    assert [item["number"] for item in entries] == list(range(1, len(entries) + 1))
    assert {item["id"] for item in entries} == set(numbers)
    assert all(item["number"] == numbers[item["id"]] for item in entries)
    deal = next(item for item in entries if item["kind"] == "deal")
    assert deal["record"]["transaction_id"] == deal["id"]
    assert "target_company" in deal["record"] and "acquirer" in deal["record"]
    stat = next(item for item in entries if item["kind"] == "statistic")
    assert set(stat["record"]) >= {"stat_id", "calculation", "filters", "n", "value", "display",
                                   "source_ids"}
    first = report.draft.summary.source_ids[0]
    later = next(sid for sid in cited if sid != first)
    assert numbers[first] == 1 and numbers[later] > numbers[first]


def test_followup_keeps_existing_numbers_and_appends_new_ones():
    entry, evidence = _buyer_evidence()
    report = guardrails.apply_check(make(entry), evidence)
    sources = guardrails.build_sources([report])
    extra_id = "MA-2015-0001"
    assert extra_id not in sources["numbers"]
    # Use a real id that this report did not cite, if one exists in the dataset.
    real = next(tid for tid in get_client().transaction_ids if tid not in sources["numbers"])
    extra = Statement(text="A later deal.", kind="fact", source_ids=[real])
    extended = guardrails.extend_sources(sources, [extra])
    first = report.draft.summary.source_ids[0]
    assert extended["numbers"][first] == sources["numbers"][first]
    assert extended["numbers"][real] == max(sources["numbers"].values()) + 1
    assert all(item["number"] == extended["numbers"][item["id"]] for item in extended["entries"])


def test_reports_markdown_has_sections_and_a_sources_appendix():
    entry, evidence = _buyer_evidence()
    first = guardrails.apply_check(make(entry), evidence)
    second = first.model_copy(update={"rank": 2, "display_name": "Other Buyer",
                                      "acquirer_key": "other", "status": "failed", "draft": None,
                                      "error": "provider error", "issues": ["provider error"]})
    ranking = tools.rank_acquirers()["data"]
    sources = guardrails.build_sources([first], ranking)
    text = guardrails.render_narrative([first, second], target=None, ranking=ranking,
                                       sources=guardrails.build_sources([first, second], ranking))
    assert text.startswith("# Buyer reports")
    assert "## Executive summary" in text and "## Ranked shortlist" in text
    assert "## Sources" in text
    for title in ("Acquirer Overview", "Strategic Fit Thesis", "Precedent Activity",
                  "Valuation Context", "Risk Flags", "Conviction"):
        assert f"### {title}" in text
    assert "Other Buyer" in text and "Failed" in text and "provider error" in text
    assert f"[{sources['entries'][0]['number']}]" in text
    assert "### [1]" in text
    # A marker in the prose is a number that exists in the appendix.
    assert f"[{sources['numbers'][first.draft.summary.source_ids[0]]}]" in text


def test_json_eof_is_a_cut_off():
    assert json_was_cut_off('{"summary": {"text": "hi"')
    assert not json_was_cut_off('{"summary": 1}')
    assert not json_was_cut_off("{not json")


class _CutoffLLM(FakeLLM):
    async def chat(self, messages, tools=None, tool_choice=None, response_schema=None,
                   use_cache=True, max_tokens=None):
        result = await super().chat(messages, tools, tool_choice, response_schema, use_cache,
                                    max_tokens)
        if response_schema is ReportDraft and write_attempt(messages) == 0:
            result.content = '{"summary":'
            result.parsed = None
            result.parse_error = "cut off"
            result.truncated = True
            result.finish_reason = "length"
        return result


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_cut_off_write_is_repaired_with_the_shorter_message():
    from ma_engine.clients.mcp_client import MCPClient
    from ma_engine.mcp_server.server import mcp as server

    ranking = tools.rank_acquirers()["data"]
    buyer = next(item for item in ranking["buyers"] if item["acquirer_key"] == "blackstone")
    llm = _CutoffLLM()
    async with MCPClient(transport=server) as mcp:
        report, _, trace = await agent.research_buyer(buyer, ranking["target"], llm, mcp)
    assert report.status == "verified", report.issues
    repair = next(call for call in llm.calls if call["schema"] is ReportDraft
                  and write_attempt(call["messages"]) == 1)
    assert agent.CUT_OFF_ISSUE in repair["messages"][-1]["content"]
    assert trace["repair_attempts"] == 1
    research = [call for call in llm.calls if call["schema"] is None]
    writes = [call for call in llm.calls if call["schema"] is ReportDraft]
    assert research and all(call["max_tokens"] == agent.RESEARCH_MAX_TOKENS for call in research)
    assert writes and all(call["max_tokens"] == agent.WRITE_MAX_TOKENS for call in writes)


def test_quality_check_accepts_ranking_stat_ids():
    entry, evidence = _buyer_evidence()
    ranking = tools.rank_acquirers()["data"]
    signal = next(item for item in entry["signals"] if item["name"] == "size_fit")
    report = guardrails.apply_check(make(entry, lambda draft: draft["summary"].update(
        source_ids=[signal["stat_id"], *draft["summary"]["source_ids"]])), evidence)
    metrics = guardrails.quality_metrics([report], ranking)
    assert signal["stat_id"] not in metrics["invalid_citation_ids"]


def test_prompts_ask_for_stat_ids_done_and_short_statements():
    research = agent.load_prompt("research_writer")
    repair = agent.load_prompt("repair")
    followup = agent.load_prompt("followup")
    assert "reply with only the word DONE" in research
    assert "ST-" in research and "at most 40 words" in research
    assert "summary" in research
    assert "cut off" in repair and "ST-" in repair
    assert "DONE" in followup and "ST-" in followup


def test_stage_names_cover_the_four_progress_groups():
    labels = [stage["label"] for stage in agent.RESEARCH_STAGES]
    assert labels == [
        "Identify relevant sectors and buyers",
        "Investigate buyer transaction histories",
        "Retrieve valuation comparisons",
        "Generate and verify buyer reports",
    ]
    assert all(stage["note"] for stage in agent.RESEARCH_STAGES)
    pairs = {pair for stage in agent.RESEARCH_STAGES for pair in stage["events"]}
    assert ("calculation", "ranking") in pairs
    assert ("mcp_tool", "get_valuation_comps") in pairs
    assert ("llm", "write") in pairs and ("validation", "check") in pairs


def test_sample_only_sentence_binds_to_cited_statistic():
    _, evidence = _buyer_evidence()
    item = guardrails.Item("valuation_context[0]", "valuation_context",
                           "The closed comparison sample contains n=10 deals.",
                           "fact", [EBITDA_ONLY])
    assert not guardrails._check_sample_sizes(item, evidence)
    assert guardrails._check_sample_sizes(item._replace(text="Sample n=46."), evidence)


def test_count_citations_list_exact_contributors():
    ranked = tools.rank_acquirers()["data"]
    client = get_client()
    for buyer in ranked["buyers"]:
        for field in ("relevant_closed_count", "direct_closed_count", "near_size_closed_count"):
            metric = buyer[field]
            record = client.stat_record(metric["stat_id"])
            assert record["source_ids"] == metric["source_ids"]
            assert len(record["source_ids"]) == metric["value"]
    blackstone = next(b for b in ranked["buyers"] if b["acquirer_key"] == "blackstone")
    assert len(blackstone["direct_closed_count"]["source_ids"]) == 2
    assert len(blackstone["relevant_closed_count"]["source_ids"]) == 7


def test_legacy_count_citation_does_not_claim_exact_evidence():
    from ma_engine.ui_report import stat_view_html
    html = stat_view_html({"calculation": "count of closed deals", "n": 2,
                           "display": "2", "source_ids": ["A", "B", "C"]}, {})
    assert "broader evidence" in html and "Evidence records (3)" in html
    assert "Transactions used" not in html


def test_adjacent_only_claim_rejected_when_direct_deals_exist():
    entry, evidence = _buyer_evidence()
    report = make(entry)
    report.draft.strategic_fit[0].text = "Adjacent-only evidence limits the fit."
    issues = guardrails.check(report, evidence)
    assert any("limited direct-sector evidence" in issue for issue in issues)


def test_summary_preserves_rank_when_second_report_is_not_verified():
    from ma_engine.ui_report import executive_summary_html
    from ma_engine.models import BuyerReport, Target
    ranking = tools.rank_acquirers()["data"]
    reports = [BuyerReport.from_ranking(entry, make(entry).draft)
               for entry in ranking["buyers"][:4]]
    for report in reports:
        report.status = "verified"
        report.issues = []
    reports[1].status = "not_verified"
    reports[1].issues = ["sample mismatch"]
    html = executive_summary_html({"reports": reports, "ranking": ranking,
                                   "target": Target.default()}, {}, 500)
    assert html.index(reports[0].display_name) < html.index(reports[1].display_name) < html.index(reports[2].display_name)
    assert reports[3].display_name not in html
    assert "Not verified" in html and "Review required" in html


def test_deal_preview_keeps_risk_evidence_and_missing_values_explicit():
    from ma_engine.ui_report import deal_view_html
    html = deal_view_html({"target_company": "<Example>", "num_bidders": 3,
                           "days_to_close": 101, "financing_type": "All cash"})
    assert "&lt;Example&gt;" in html and "<Example>" not in html
    assert "Bidders" in html and "Days to close" in html and "101" in html
    assert "Not recorded" in html and "All cash" in html
