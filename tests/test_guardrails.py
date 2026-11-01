import copy
import json

import pytest
from pydantic import ValidationError

from ma_engine import config
from ma_engine.validation import guardrails
from ma_engine.data import ranking
from ma_engine.clients.data_client import comps_stat_id
from ma_engine.validation.guardrails import ID_PATTERN, build_evidence, check, number_tokens
from ma_engine.mcp_server import tools
from ma_engine.models import BuyerReport, ReportDraft

HCS = "Healthcare Services"
MID = "$100M–$400M"
EBITDA_ONLY = comps_stat_id(HCS, "sector_only", MID, "ev_ebitda", "median")
EBITDA_PLUS = comps_stat_id(HCS, "sector_plus_adjacent", MID, "ev_ebitda", "median")
CALLS = [
    ("get_acquirer_profile", {"acquirer_key": "blackstone"}),
    ("get_acquirer_deals", {"acquirer_key": "blackstone", "closed_only": False, "limit": 25}),
    ("get_valuation_comps", {"sector": HCS, "scope": "sector_only", "size_band": MID}),
    ("get_valuation_comps", {"sector": HCS, "scope": "sector_plus_adjacent", "size_band": MID}),
    ("search_deals", {"sectors": [HCS], "outcomes": ["Pending"], "limit": 3}),
]


def fact(text, *ids):
    return {"text": text, "kind": "fact", "source_ids": list(ids)}


VALID = {
    "summary": fact("Blackstone has closed Healthcare Services precedents near the target size, "
                    "including Evergreen Care Solutions.", "MA-2021-0195"),
    "acquirer_overview": [
        fact("Blackstone is a Financial Sponsor with 25 deals in the dataset, 22 of them closed.",
             "MA-2021-0195", "MA-2021-0407"),
        fact("Its closed deals are led by Health Insurance (6), Revenue Cycle (4) and Health IT (3).",
             "MA-2015-0475", "MA-2019-0162", "MA-2017-0305"),
    ],
    "strategic_fit": [
        fact("4 of 7 relevant closed deals were within 2x of the target's $200.0M (between "
             "$100.0M and $400.0M).", "MA-2018-0122", "MA-2021-0195", "MA-2021-0407", "MA-2021-0453"),
        {"text": "Repeat mid-sized provider-services platforms suggest a regional Healthcare "
                 "Services business matches its pattern.", "kind": "interpretation",
         "source_ids": ["MA-2021-0195"]},
    ],
    "precedent_activity": [
        fact("Blackstone acquired Evergreen Care Solutions (MA-2021-0195) for $299.0M at 14.6x and "
             "Peak Medical Management (MA-2021-0407) for $341.7M at 10.0x, both in 2021.",
             "MA-2021-0195", "MA-2021-0407"),
        fact("Blackstone was the would-be acquirer of Green Care Providers in MA-2020-0040, which "
             "was Withdrawn.", "MA-2020-0040"),
    ],
    "valuation_context": [
        fact("Closed Healthcare Services deals of $100M–$400M had a median EV/EBITDA of 12.1x "
             "(n=10).", EBITDA_ONLY, "MA-2021-0195", "MA-2021-0407"),
        fact("Adding adjacent sectors, the median EV/EBITDA is 12.4x (n=75).", EBITDA_PLUS,
             "MA-2018-0122"),
    ],
    "risk_flags": [
        {"risk": "Deals that did not close", "explanation": "MA-2020-0040 was Withdrawn and "
         "MA-2023-0456 is Rumored.", "kind": "fact", "source_ids": ["MA-2020-0040", "MA-2023-0456"]},
        {"risk": "Older activity", "explanation": "The most recent relevant closed deal was in "
         "2022.", "kind": "fact", "source_ids": ["MA-2022-0319"]},
        {"risk": "Current appetite", "explanation": "The dataset does not show the buyer's current "
         "fund size or appetite.", "kind": "unknown", "source_ids": []},
    ],
    "conviction": {"level": "High", "reason": "Score 0.665 with 4 relevant closed deals within 2x "
                   "of the target EV, above the High bar of 0.603.",
                   "source_ids": ["MA-2021-0195", "MA-2021-0407"]},
}


@pytest.fixture(scope="module")
def entry():
    buyers = tools.rank_acquirers()["data"]["buyers"]
    return next(b for b in buyers if b["acquirer_key"] == "blackstone")


@pytest.fixture(scope="module")
def evidence(entry):
    results = [{"tool": t, "arguments": a, "result": getattr(tools, t)(**a)} for t, a in CALLS]
    return build_evidence(entry, results)


@pytest.fixture(scope="module")
def pending_id(evidence):
    return next(i for i in sorted(evidence.allowed_ids) if evidence.outcomes[i] == "Pending")


def make(entry, edit=None) -> BuyerReport:
    draft = copy.deepcopy(VALID)
    if edit:
        edit(draft)
    return BuyerReport.from_ranking(entry, ReportDraft.model_validate(draft))


def issues_for(entry, evidence, edit):
    return check(make(entry, edit), evidence)


def assert_one_issue(issues, *fragments):
    assert len(issues) == 1, issues
    for fragment in fragments:
        assert fragment in issues[0], issues[0]


# ---------- models ----------

def test_draft_schema_is_flat_and_closed():
    schema = ReportDraft.model_json_schema()
    text = json.dumps(schema)
    assert "anyOf" not in text and "oneOf" not in text
    assert set(schema["properties"]) == {"summary", "acquirer_overview", "strategic_fit",
                                         "precedent_activity", "valuation_context", "risk_flags",
                                         "conviction"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["risk_flags"]["maxItems"] == 4


def test_ai_cannot_set_status_or_identity():
    with pytest.raises(ValidationError):
        ReportDraft.model_validate({**VALID, "status": "verified"})
    with pytest.raises(ValidationError):
        ReportDraft.model_validate({**VALID, "acquirer_key": "kkr"})


def test_unchecked_report_is_not_verified(entry):
    report = make(entry)
    assert report.status == "not_verified" and report.issues


# ---------- the valid report ----------

def test_valid_report_passes(entry, evidence):
    report = guardrails.apply_check(make(entry), evidence)
    assert report.issues == []
    assert report.status == "verified"
    assert guardrails.length_warnings(report) == []


def test_evidence_uses_display_strings_not_raw_values(evidence):
    assert "14.4x" in evidence.allowed_numbers and "14.35" not in evidence.allowed_numbers
    assert "0.66" in evidence.allowed_numbers and "0.6645" not in evidence.allowed_numbers


# ---------- structure ----------

def test_missing_section(entry, evidence):
    issues = issues_for(entry, evidence, lambda d: d.update(strategic_fit=[]))
    assert_one_issue(issues, "strategic_fit: is empty")


def test_one_risk_flag(entry, evidence):
    issues = issues_for(entry, evidence, lambda d: d.update(risk_flags=d["risk_flags"][:1]))
    assert_one_issue(issues, "risk_flags: has 1 risk flag(s)")


def test_duplicate_risks(entry, evidence):
    def edit(d):
        d["risk_flags"][2] = {**d["risk_flags"][1], "risk": "  older ACTIVITY "}
    assert_one_issue(issues_for(entry, evidence, edit), "risk_flags[2]: repeats the risk",
                     "risk_flags[1]")


def test_conviction_moved_two_steps(entry, evidence):
    issues = issues_for(entry, evidence, lambda d: d["conviction"].update(level="Low"))
    assert_one_issue(issues, "conviction: level Low is 2 steps from the starting level High",
                     "Medium, High")


def test_conviction_one_step_passes(entry, evidence):
    assert issues_for(entry, evidence, lambda d: d["conviction"].update(level="Medium")) == []


# ---------- citations ----------

def test_fact_without_id(entry, evidence):
    issues = issues_for(entry, evidence, lambda d: d["acquirer_overview"][1].update(source_ids=[]))
    assert_one_issue(issues, "acquirer_overview[1]: is a fact but cites no transaction ID")


def test_interpretation_without_id(entry, evidence):
    issues = issues_for(entry, evidence, lambda d: d["strategic_fit"][1].update(source_ids=[]))
    assert_one_issue(issues, "strategic_fit[1]: is an interpretation but cites no transaction ID")


def test_risk_fact_without_id(entry, evidence):
    issues = issues_for(entry, evidence, lambda d: d["risk_flags"][1].update(source_ids=[]))
    assert_one_issue(issues, "risk_flags[1]: is a fact but cites no transaction ID")


def test_id_not_in_evidence(entry, evidence):
    outside = next(i for i in sorted(evidence.outcomes) if i not in evidence.allowed_ids)
    issues = issues_for(entry, evidence,
                        lambda d: d["acquirer_overview"][0]["source_ids"].append(outside))
    assert_one_issue(issues, f"acquirer_overview[0]: cites {outside}",
                     "not in the tool results for this buyer")


def test_made_up_id(entry, evidence):
    issues = issues_for(entry, evidence,
                        lambda d: d["acquirer_overview"][0]["source_ids"].append("MA-2099-9999"))
    assert_one_issue(issues, "cites MA-2099-9999, which does not exist in the dataset")


def test_malformed_id(entry, evidence):
    issues = issues_for(entry, evidence,
                        lambda d: d["acquirer_overview"][0]["source_ids"].append("MA-21-195"))
    assert_one_issue(issues, '"MA-21-195" is not a transaction ID')


def test_id_in_text_must_be_cited(entry, evidence):
    issues = issues_for(entry, evidence,
                        lambda d: d["precedent_activity"][0]["source_ids"].remove("MA-2021-0407"))
    assert_one_issue(issues, "precedent_activity[0]: the text mentions MA-2021-0407")


def test_conviction_reason_without_id(entry, evidence):
    issues = issues_for(entry, evidence, lambda d: d["conviction"].update(source_ids=[]))
    assert_one_issue(issues, "conviction: the reason cites no transaction ID")


# ---------- numbers ----------

def test_wrong_number_format(entry, evidence):
    def edit(d):
        d["precedent_activity"][0]["text"] = d["precedent_activity"][0]["text"].replace(
            "$299.0M", "$299M")
    assert_one_issue(issues_for(entry, evidence, edit), 'number "$299M"',
                     'Did you mean "$299.0M"?')


def test_number_not_in_evidence(entry, evidence):
    def edit(d):
        d["acquirer_overview"][0]["text"] += " Its typical cheque is $123.4M."
    assert_one_issue(issues_for(entry, evidence, edit), 'acquirer_overview[0]: number "$123.4M"')


def test_commas_are_normalized(entry, evidence):
    for written in ["$14,353.7M", "$14353.7M"]:
        def edit(d, written=written):
            d["precedent_activity"].append(fact(
                f"Its largest closed deal was Clearwater Pharma at {written}.", "MA-2019-0498"))
        assert issues_for(entry, evidence, edit) == []


def test_transaction_id_digits_are_not_numbers():
    assert number_tokens("See MA-2021-0195 and MA-2020-0040.") == set()


def test_tokenizer_splits_ranges_and_units():
    assert number_tokens("$100M–$400M, 2022–2024, 12.1x, 15.5%, 0.67, $1.2B, n=10") == {
        "$100M", "$400M", "2022", "2024", "12.1x", "15.5%", "0.67", "$1.2B", "10"}


def test_tokenizer_skips_quarters_and_ordinals_and_keeps_minus():
    assert number_tokens("-12.1x, Q1, 1st") == {"-12.1x"}
    assert number_tokens("in Q3 2021, its 2nd and 23rd deals, -$5.0M and -4.2%") == {
        "2021", "-$5.0M", "-4.2%"}
    assert number_tokens("2022-2024 and $1.0M-$4.0M, a 3-year hold") == {
        "2022", "2024", "$1.0M", "$4.0M", "3"}


def test_quarter_label_and_negative_number_in_a_report(entry, evidence):
    def quarter(d):
        d["precedent_activity"][0]["text"] = d["precedent_activity"][0]["text"].replace(
            "both in 2021", "both in Q3 2021, its 1st and 2nd deals")
    assert issues_for(entry, evidence, quarter) == []

    def negative(d):
        d["valuation_context"][0]["text"] = d["valuation_context"][0]["text"].replace(
            "12.1x", "-12.1x")
    assert_one_issue(issues_for(entry, evidence, negative), '"-12.1x" does not appear')


def test_sector_assumption_number_cannot_be_cited_as_a_fact(entry):
    overview = tools.get_sector_overview(HCS)
    ev = build_evidence(entry, [{"tool": "get_sector_overview", "arguments": {"sector": HCS},
                                 "result": overview}])
    health_it = next(s for s in overview["data"]["other_sectors"] if s["sector"] == "Health IT")
    closeness = health_it["assumption"]["closeness_display"]
    assert closeness in ev.allowed_numbers
    assert not any(closeness in s.numbers and s.ids for s in ev.sources)
    sector_id = overview["source_ids"][0]

    def edit(d):
        d["risk_flags"][1] = {"risk": "Distant sector", "kind": "fact", "source_ids": [sector_id],
                              "explanation": f"Health IT has closeness {closeness} to {HCS}."}
    issues = check(make(entry, edit), ev)
    assert any(i.startswith("risk_flags[1]") and "hand-written assumption" in i for i in issues)


# ---------- outcome wording ----------

def test_withdrawn_deal_described_as_acquired(entry, evidence):
    def edit(d):
        d["precedent_activity"][1]["text"] = "Blackstone acquired Green Care Providers (MA-2020-0040)."
    assert_one_issue(issues_for(entry, evidence, edit),
                     "precedent_activity[1]: cites MA-2020-0040 (Withdrawn)", '"acquired"',
                     "own statement")


def test_pending_deal_described_as_completed(entry, evidence, pending_id):
    def edit(d):
        d["risk_flags"][1] = {"risk": "Open deal", "explanation": f"{pending_id} was completed.",
                              "kind": "fact", "source_ids": [pending_id]}
    assert_one_issue(issues_for(entry, evidence, edit), f"cites {pending_id} (Pending)",
                     '"completed"')


def test_acquirer_is_not_a_completion_word():
    assert not guardrails.COMPLETION_PATTERN.search("the would-be acquirer of the target")
    assert guardrails.COMPLETION_PATTERN.search("its ACQUISITION  OF the clinic")


# ---------- valuation ----------

def test_valuation_cites_non_closed_deal(entry, evidence, pending_id):
    issues = issues_for(entry, evidence,
                        lambda d: d["valuation_context"][1]["source_ids"].append(pending_id))
    assert_one_issue(issues, "valuation_context[1]: valuation statements must cite closed deals "
                     "only", f"{pending_id} (Pending)")


def test_valuation_text_saying_closed_with_pending_deal_fails_twice(entry, evidence, pending_id):
    issues = issues_for(entry, evidence,
                        lambda d: d["valuation_context"][0]["source_ids"].append(pending_id))
    assert len(issues) == 2
    assert 'uses completion word "Closed"' in issues[0]
    assert "valuation statements must cite closed deals only" in issues[1]


def test_valuation_without_sample_size(entry, evidence):
    def edit(d):
        d["valuation_context"][1]["text"] = "Adding adjacent sectors, the median EV/EBITDA is 12.4x."
    assert_one_issue(issues_for(entry, evidence, edit),
                     "valuation_context[1]: valuation fact has no sample size")


def test_valuation_interpretation_needs_sample_size(entry, evidence):
    def edit(d):
        d["valuation_context"].append({"text": "Blackstone's 17.2x median paid sits beside the "
                                               "12.1x comps median.", "kind": "interpretation",
                                       "source_ids": ["ST-PROFILE-blackstone-closed-ev-ebitda-median",
                                                      EBITDA_ONLY, "MA-2021-0195"]})
    assert_one_issue(issues_for(entry, evidence, edit),
                     "valuation_context[2]: valuation interpretation has no sample size")


def test_valuation_interpretation_must_cite_closed_deals(entry, evidence, pending_id):
    def edit(d):
        d["valuation_context"][1].update(kind="interpretation")
        d["valuation_context"][1]["source_ids"].append(pending_id)
    assert_one_issue(issues_for(entry, evidence, edit),
                     "valuation statements must cite closed deals only", f"{pending_id} (Pending)")


def test_valuation_sample_size_not_in_evidence(entry, evidence):
    def edit(d):
        d["valuation_context"][1]["text"] = d["valuation_context"][1]["text"].replace("n=75", "n=77")
    assert_one_issue(issues_for(entry, evidence, edit),
                     "valuation_context[1]: n=77 is not the sample size", "has n=75")


# ---------- claim-to-source binding (review swapped sentences) ----------

def replace_precedent(text, *ids):
    return lambda d: d["precedent_activity"].__setitem__(0, fact(text, *ids))


def test_swapped_price_multiple_and_target(entry, evidence):
    issues = issues_for(entry, evidence, replace_precedent(
        "In 2021, Blackstone paid $299.0M at 14.6x for Evergreen Care Solutions.", "MA-2021-0407"))
    assert issues == [
        'precedent_activity[0]: "$299.0M" belongs to MA-2021-0195, but the statement cites '
        "MA-2021-0407. Cite MA-2021-0195 or use the figures for MA-2021-0407 ($341.7M, "
        "EV/EBITDA 10.0x, EV/Revenue 1.5x, 2021).",
        'precedent_activity[0]: "14.6x" belongs to MA-2021-0195, but the statement cites '
        "MA-2021-0407. Cite MA-2021-0195 or use the figures for MA-2021-0407 ($341.7M, "
        "EV/EBITDA 10.0x, EV/Revenue 1.5x, 2021).",
        'precedent_activity[0]: "Evergreen Care Solutions" is the target of MA-2021-0195, but '
        "the statement cites MA-2021-0407 (Peak Medical Management). Cite MA-2021-0195 or name "
        "the cited deal's target.",
    ]


def test_correct_figures_for_the_cited_deal_pass(entry, evidence):
    assert issues_for(entry, evidence, replace_precedent(
        "In 2021, Blackstone paid $341.7M at 10.0x for Peak Medical Management, a SPAC Merger.",
        "MA-2021-0407")) == []


def test_closed_deal_described_as_withdrawn(entry, evidence):
    issues = issues_for(entry, evidence, replace_precedent(
        "Blackstone's deal for Peak Medical Management was Withdrawn.", "MA-2021-0407"))
    assert_one_issue(issues, 'precedent_activity[0]: says "Withdrawn", but no cited deal is '
                     "Withdrawn (MA-2021-0407 is Closed)")


def test_named_closed_deal_beside_a_withdrawn_one(entry, evidence):
    issues = issues_for(entry, evidence, replace_precedent(
        "Peak Medical Management was Withdrawn.", "MA-2021-0407", "MA-2020-0040"))
    assert_one_issue(issues, "names Peak Medical Management (MA-2021-0407, Closed) in a "
                     "statement that says Withdrawn")


def test_wrong_deal_type(entry, evidence):
    issues = issues_for(entry, evidence, replace_precedent(
        "Peak Medical Management was a 2021 Platform Investment.", "MA-2021-0407"))
    assert_one_issue(issues, 'says "Platform Investment", but no cited deal is a Platform '
                     "Investment (MA-2021-0407 is a SPAC Merger)")


def test_plain_prose_is_not_a_deal_type(entry, evidence):
    def edit(d):
        d["strategic_fit"][1]["text"] = ("The target would be a new platform or an add-on, a "
                                         "strategic acquisition in either case.")
    assert issues_for(entry, evidence, edit) == []


def test_sample_size_of_another_comp_set(entry, evidence):
    def edit(d):
        d["valuation_context"][1]["text"] = "Adding adjacent sectors, the median EV/EBITDA is 12.4x (n=10)."
    assert_one_issue(issues_for(entry, evidence, edit),
                     "valuation_context[1]: n=10 is not the sample size", "has n=75")


def test_other_buyers_comp_id_as_precedent(entry, evidence):
    other = "MA-2018-0470"  # in the Healthcare Services comps; a New Mountain Capital deal
    assert other in evidence.allowed_ids and other not in evidence.buyer_ids
    issues = issues_for(entry, evidence, replace_precedent(
        "Blackstone acquired a Healthcare Services business in 2018.", other))
    assert_one_issue(issues, f"precedent_activity[0]: cites {other}, which is not this buyer's "
                     "deal (New Mountain Capital")


def test_fit_resting_only_on_comp_ids(entry, evidence):
    def edit(d):
        d["strategic_fit"][1]["source_ids"] = ["MA-2018-0470"]
    assert_one_issue(issues_for(entry, evidence, edit),
                     "strategic_fit[1]: cites only comps or other buyers' deals (MA-2018-0470)")


def test_comps_figure_in_precedent(entry, evidence):
    issues = issues_for(entry, evidence, replace_precedent(
        "Blackstone acquired Evergreen Care Solutions at 12.1x.", "MA-2021-0195"))
    assert_one_issue(issues, "Cite ST-COMPS-healthcare-services-sector_only-100-400-ev_ebitda-median "
                     "for 12.1x")


def test_statistic_number_needs_one_of_its_ids(entry, evidence):
    # MA-2018-0122 is in the sector-plus-adjacent comps only, not the sector-only set behind 12.1x.
    issues = issues_for(entry, evidence,
                        lambda d: d["valuation_context"][0].update(source_ids=["MA-2018-0122"]))
    assert len(issues) == 2, issues
    assert "Cite ST-COMPS-healthcare-services-sector_only-100-400-ev_ebitda-median for 12.1x" in issues[0]
    assert "valuation_context[0]: n=10 is not the sample size" in issues[1]


def test_single_deal_statistic(entry, evidence):
    def edit(d):
        d["acquirer_overview"][1] = fact("Its smallest closed deal was $20.7M.", "MA-2021-0195")
    assert_one_issue(issues_for(entry, evidence, edit), '"$20.7M" belongs to MA-2017-0305')


def test_year_must_be_a_cited_deals_year(entry, evidence):
    issues = issues_for(entry, evidence, replace_precedent(
        "Blackstone acquired Peak Medical Management in 2019.", "MA-2021-0407"))
    assert_one_issue(issues, '"2019" is not the year of any cited deal (MA-2021-0407 is 2021)')


def test_target_block_numbers_need_no_citation(entry, evidence):
    target = tools.rank_acquirers()["data"]["target"]
    with_target = build_evidence(entry, [{"tool": "rank_acquirers", "arguments": {},
                                          "result": {"data": {"target": target}}}, *[
        {"tool": t, "arguments": a, "result": getattr(tools, t)(**a)} for t, a in CALLS]])

    def edit(d):
        d["precedent_activity"][0]["text"] += " The target is a $200.0M business above 15.5%."
    assert issues_for(entry, with_target, edit) == []
    assert issues_for(entry, evidence, edit) != []


# ---------- no false failures on correct reports built from real tool output ----------

def realistic_draft(entry, target, results) -> dict:
    """A full report written the way the prompt asks, every figure copied from `results`."""
    name, band = entry["display_name"], target["size_band"]
    p, only, plus, closed_rows, all_rows = (r["result"]["data"] for r in results[:5])
    sig = {s["name"]: s for s in entry["signals"]}
    size, sectors = p["closed_deal_size"], list(p["closed_by_sector"].items())[:3]
    e1, e2 = only["ev_ebitda"], plus["ev_ebitda"]
    non_closed = [r for r in all_rows["deals"] if not r["is_closed"]][:2]
    return {
        "summary": fact(f"{name} has closed deals in or next to {target['sector']} near the "
                        f"target's {target['ev_mm']['display']}.",
                        entry["supporting_closed_ids"][0]),
        "acquirer_overview": [
            fact(f"{name} is a {p['acquirer_type']} with {p['deal_count']['display']} deals in the "
                 f"dataset, {p['closed_count']['display']} of them closed.",
                 *p["closed_count"]["source_ids"]),
            fact("Its closed deals are led by " + ", ".join(f"{s} ({v['display']})"
                                                            for s, v in sectors) + ".",
                 *[v["source_ids"][0] for _, v in sectors]),
            fact(f"Its median closed deal size is {size['median']['display']} (n="
                 f"{size['median']['n']}), from {size['smallest']['display']} to "
                 f"{size['largest']['display']}.", size["median"]["stat_id"],
                 size["smallest"]["stat_id"], size["largest"]["stat_id"],
                 *size["median"]["source_ids"]),
            fact(f"Its deals run from {p['first_deal_year']['display']} to "
                 f"{p['last_deal_year']['display']}.", *p["first_deal_year"]["source_ids"],
                 *p["last_deal_year"]["source_ids"]),
        ],
        "strategic_fit": [
            fact(sig["size_fit"]["explanation"].split(";")[0] + ".", *sig["size_fit"]["source_ids"]),
            fact(sig["sector_fit"]["explanation"].split(";")[0] + ".",
                 *sig["sector_fit"]["source_ids"]),
            {"text": f"The target's {target['ev_mm']['display']} sits in the {band} band, with a "
                     f"margin above {target['strong_margin_threshold']['display']}.",
             "kind": "interpretation", "source_ids": entry["supporting_closed_ids"][:1]},
        ],
        "precedent_activity": [
            fact(f"In {r['deal_quarter']} {r['deal_year']}, {name} acquired {r['target_company']}, "
                 f"a {r['sector']} {r['deal_type']}, for {r['deal_size_mm']['display']} at "
                 f"{r['ev_ebitda_multiple']['display']}.", r["transaction_id"])
            for r in closed_rows["deals"][:2]
        ] + [fact(f"{name}'s {r['deal_year']} deal for {r['target_company']} was {r['outcome']}.",
                  r["transaction_id"]) for r in non_closed],
        "valuation_context": [
            fact(f"Closed {target['sector']} deals of {band} had a median EV/EBITDA of "
                 f"{e1['median']['display']} (n={e1['sample_size']['display']}).",
                 e1["median"]["stat_id"], *e1["source_ids"]),
            fact(f"Adding adjacent sectors, EV/EBITDA runs {e2['p25']['display']} to "
                 f"{e2['p75']['display']}, median {e2['median']['display']} "
                 f"(n={e2['sample_size']['display']}).", e2["p25"]["stat_id"], e2["median"]["stat_id"],
                 e2["p75"]["stat_id"], *e2["source_ids"]),
            {"text": f"{name} paid a median {p['closed_ev_ebitda_median']['display']} (n="
                     f"{p['closed_ev_ebitda_median']['n']}), beside the {e1['median']['display']} "
                     f"comps median (n={e1['sample_size']['display']}).", "kind": "interpretation",
             "source_ids": [p["closed_ev_ebitda_median"]["stat_id"], e1["median"]["stat_id"],
                            *p["closed_ev_ebitda_median"]["source_ids"][:2]]},
        ],
        "risk_flags": [
            {"risk": "Competitive processes", "explanation": "Its closed deals had a median of "
             f"{p['closed_median_bidders']['display']} bidders (n={p['closed_median_bidders']['n']}).",
             "kind": "fact", "source_ids": p["closed_count"]["source_ids"]},
            {"risk": "Size mismatch", "explanation": f"Its median closed deal of "
             f"{size['median']['display']} compares with the target's {target['ev_mm']['display']}.",
             "kind": "interpretation", "source_ids": [size["median"]["stat_id"],
                                                    *size["median"]["source_ids"][:2]]},
        ],
        "conviction": {"level": entry["starting_conviction"]["level"],
                       "reason": entry["starting_conviction"]["reason"].split(". ")[0] + ".",
                       "source_ids": entry["supporting_closed_ids"][:2]},
    }


@pytest.mark.parametrize("key", ["blackstone", "atrium health", "vns health"])
def test_realistic_correct_report_passes(key):
    ranked = tools.rank_acquirers()["data"]
    target, entry = ranked["target"], next(b for b in ranking.rank_acquirers(ranking.Target.default(), limit=100)["data"]["buyers"] if b["acquirer_key"] == key)
    sectors = [s["sector"] for s in target["relevant_sectors"]]
    calls = [("get_acquirer_profile", {"acquirer_key": key}),
             ("get_valuation_comps", {"sector": HCS, "scope": "sector_only", "size_band": MID}),
             ("get_valuation_comps", {"sector": HCS, "scope": "sector_plus_adjacent",
                                      "size_band": MID}),
             ("get_acquirer_deals", {"acquirer_key": key, "sectors": sectors}),
             ("get_acquirer_deals", {"acquirer_key": key, "closed_only": False, "limit": 25})]
    results = [{"tool": t, "arguments": a, "result": getattr(tools, t)(**a)} for t, a in calls]
    ev = build_evidence(entry, [{"tool": "rank_acquirers", "arguments": {},
                                 "result": {"data": {"target": target}}}, *results])
    draft = ReportDraft.model_validate(realistic_draft(entry, target, results))
    assert check(BuyerReport.from_ranking(entry, draft), ev) == []


# ---------- company names ----------

def test_company_name_not_in_evidence(entry, evidence):
    assert "Welsh Carson" not in evidence.allowed_names

    def edit(d):
        d["strategic_fit"][1]["text"] += " Welsh Carson follows a similar pattern."
    assert_one_issue(issues_for(entry, evidence, edit), 'strategic_fit[1]: names "Welsh Carson"')


def test_company_name_in_evidence_passes(entry, evidence):
    assert "Sterling Revenue Cycle" in evidence.allowed_names
    assert guardrails.company_names("Blackstone bought North Smile Group.") == {
        "Blackstone", "North Smile Group"}


# ---------- unknowns ----------

def test_unknown_with_number(entry, evidence):
    def edit(d):
        d["risk_flags"][2]["explanation"] = "The data does not show its plans after 2024."
    assert_one_issue(issues_for(entry, evidence, edit),
                     "risk_flags[2]: unknown statements must not contain numbers (found 2024)")


def test_unknown_with_id(entry, evidence):
    issues = issues_for(entry, evidence,
                        lambda d: d["risk_flags"][2].update(source_ids=["MA-2021-0195"]))
    assert_one_issue(issues, "risk_flags[2]: unknown statements must not cite transaction IDs")


# ---------- length ----------

def test_long_statement_is_a_warning_not_an_issue(entry, evidence):
    report = make(entry, lambda d: d["strategic_fit"][1].update(text="word " * 70))
    assert check(report, evidence) == []
    assert guardrails.length_warnings(report) == [
        "strategic_fit[1]: 70 words; keep statements under 60."]


# ---------- rendering ----------

def all_cited(report: BuyerReport) -> set[str]:
    return {i for item in guardrails._items(report.draft) for i in item.source_ids}


def test_render_shows_exactly_the_cited_ids(entry, evidence):
    report = guardrails.apply_check(make(entry), evidence)
    md = guardrails.render_markdown(report)
    cited = all_cited(report)
    assert set(ID_PATTERN.findall(md)) == {i for i in cited if i.startswith("MA-")}
    assert all(f"[{i}]" in md for i in cited)
    assert "[MA-2023-0456]" in md and "*Interpretation:*" in md and "*Unknown:*" in md
    assert "Not verified" not in md
    titles = ["Acquirer Overview", "Strategic Fit Thesis", "Precedent Activity",
              "Valuation Context", "Risk Flags", "Conviction"]
    positions = [md.index(f"### {t}") for t in titles]
    assert positions == sorted(positions)


def test_render_not_verified_banner(entry, evidence):
    report = guardrails.apply_check(make(entry, lambda d: d.update(strategic_fit=[])), evidence)
    md = guardrails.render_markdown(report)
    assert report.status == "not_verified"
    assert "> **Not verified.**" in md and "strategic_fit: is empty" in md


def test_render_run_has_summary_table(entry, evidence):
    report = guardrails.apply_check(make(entry), evidence)
    md = guardrails.render_run_markdown([report])
    assert "| 1 | Blackstone | Financial Sponsor | High | Verified |" in md


# ---------- quality metrics and make check ----------

def second_report(entry, evidence) -> BuyerReport:
    def edit(d):
        d["strategic_fit"] = [fact("Dental and behavioral clinics in the Southeast dominate its "
                                   "closed history, with Platform Build tags.", "MA-2021-0453")]
        d["conviction"]["level"] = "Medium"
    report = guardrails.apply_check(make(entry, edit), evidence)
    return report.model_copy(update={"acquirer_key": "other", "display_name": "Other Buyer",
                                     "acquirer_type": "Strategic"})


def test_quality_metrics_good_run(entry, evidence, monkeypatch):
    monkeypatch.setattr(config, "REPORT_COUNT", 2)
    reports = [guardrails.apply_check(make(entry), evidence), second_report(entry, evidence)]
    metrics = guardrails.quality_metrics(reports)
    assert metrics["passed"], metrics["summary"]
    assert metrics["invalid_citation_count"] == 0 and metrics["facts_cited_share"] == 1.0
    assert metrics["conviction_mix"] == {"High": 1, "Medium": 1, "Low": 0}
    assert metrics["buyer_type_mix"] == {"Financial Sponsor": 1, "Strategic": 1}


def test_quality_metrics_catch_problems(entry, evidence):
    first = guardrails.apply_check(make(entry), evidence)
    twin = first.model_copy(update={"acquirer_key": "twin", "display_name": "Twin"})
    bad = make(entry, lambda d: d["acquirer_overview"][0]["source_ids"].append("MA-2099-9999"))
    metrics = guardrails.quality_metrics([first, twin, bad])
    assert metrics["similar_pairs"][0]["similarity"] == 1.0
    assert metrics["invalid_citation_count"] == 1
    assert metrics["conviction_mix"] == {"High": 3, "Medium": 0, "Low": 0}
    assert not any(metrics["summary"][k] for k in
                   ["no_invalid_citations", "conviction_varies", "no_similar_pairs"])
    assert not metrics["passed"]


def test_quality_metrics_fail_on_unverified_or_missing_reports(entry, evidence, monkeypatch):
    monkeypatch.setattr(config, "REPORT_COUNT", 2)
    good = [guardrails.apply_check(make(entry), evidence), second_report(entry, evidence)]
    short = guardrails.quality_metrics(good[:1])
    assert not short["summary"]["full_report_count"] and not short["passed"]
    unverified = good[1].model_copy(update={"status": "not_verified", "issues": ["x"]})
    metrics = guardrails.quality_metrics([good[0], unverified])
    assert metrics["summary"]["full_report_count"]
    assert not metrics["summary"]["all_reports_verified"] and not metrics["passed"]
    assert metrics["not_verified"] == ["other"]


def write_run(runs_dir, run_id, reports):
    folder = runs_dir / run_id
    folder.mkdir(parents=True)
    payload = {"run_id": run_id, "target": config.DEFAULT_TARGET,
               "reports": [r.model_dump() for r in reports]}
    (folder / "reports.json").write_text(json.dumps(payload), encoding="utf-8")


def test_main_passes_on_latest_good_run(tmp_path, monkeypatch, capsys, entry, evidence):
    monkeypatch.setattr(config, "REPORT_COUNT", 2)
    good = [guardrails.apply_check(make(entry), evidence), second_report(entry, evidence)]
    write_run(tmp_path, "20261031-170000", good[:1])
    write_run(tmp_path, "20261031-173000", good)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    guardrails.main()
    out = capsys.readouterr().out
    assert "Run 20261031-173000: 2 reports" in out and "Result: PASS" in out


def test_main_fails_on_bad_run(tmp_path, monkeypatch, capsys, entry, evidence):
    write_run(tmp_path, "20261031-173000", [guardrails.apply_check(make(entry), evidence)])
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        guardrails.main()
    assert exit_info.value.code == 1
    assert "Result: FAIL (1 of 10 reports; conviction_varies)" in capsys.readouterr().out


def test_main_names_unverified_reports(tmp_path, monkeypatch, capsys, entry, evidence):
    monkeypatch.setattr(config, "REPORT_COUNT", 2)
    first = guardrails.apply_check(make(entry), evidence)
    bad = second_report(entry, evidence).model_copy(update={"status": "not_verified"})
    write_run(tmp_path, "20261031-173000", [first, bad])
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        guardrails.main()
    assert "Result: FAIL (1 report(s) not verified)" in capsys.readouterr().out


def test_main_without_runs_exits_non_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "missing")
    with pytest.raises(SystemExit) as exit_info:
        guardrails.main()
    assert exit_info.value.code == 1
    assert "No runs found" in capsys.readouterr().out
