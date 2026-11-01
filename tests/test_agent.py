"""Agent, engine and client tests: a scripted fake LLM with the real MCP server and tools.

No test calls a real LLM. The fake reads the real tool results in the conversation to choose
its next tools and to write drafts from real display strings and IDs.
"""

import asyncio
import json
import re
from contextlib import nullcontext
from types import SimpleNamespace

import openai
import pytest
from openai.types import Model
from openai.types.chat import ChatCompletion

from ma_engine.research import agent, engine
from ma_engine import config
from ma_engine.validation import guardrails
from ma_engine.research.agent import ReportError, agent_tools, research_buyer
from ma_engine.clients.llm_client import (EMPTY_SUPPORTED_WARNING, OPTIONAL_PARAMETERS, LLMClient,
                                          LLMResult, _parse)
from ma_engine.clients.mcp_client import MCPClient
from ma_engine.mcp_server import tools
from ma_engine.mcp_server.server import mcp as server
from ma_engine.models import BuyerReport, CommitteeReview, ReportDraft

HCS = "Healthcare Services"
MID = "$100M–$400M"
RANKING = tools.rank_acquirers()["data"]
TARGET = RANKING["target"]
ENTRIES = {b["acquirer_key"]: b for b in RANKING["buyers"]}
RELEVANT = [s["sector"] for s in TARGET["relevant_sectors"]]
KEY_PATTERN = re.compile(r'acquirer_key "([^"]+)"')


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- reading the conversation ----------

def buyer_key(messages) -> str | None:
    match = KEY_PATTERN.search(messages[0]["content"]) if messages[0]["role"] == "system" else None
    return match.group(1) if match else None


def research_round(messages) -> int:
    return 1 + sum(1 for m in messages if m["role"] == "assistant")


def results(messages) -> dict[str, list[dict]]:
    """Successful tool results seen so far, grouped by what they contain."""
    out = {"profile": [], "comps": [], "deals": [], "errors": []}
    for m in messages:
        if m["role"] != "tool":
            continue
        result = json.loads(m["content"])
        data = result.get("data") or {}
        kind = ("errors" if "error" in result else "profile" if "closed_by_sector" in data
                else "comps" if "ev_ebitda" in data else "deals" if "deals" in data else None)
        if kind:
            out[kind].append(result)
    return out


# ---------- scripted research and writing ----------

def evidence_policy(messages) -> list:
    """Profile and narrow comps first; then react to how much direct history the buyer has."""
    key, round_no = buyer_key(messages), research_round(messages)
    if round_no == 1:
        return [("get_acquirer_profile", {"acquirer_key": key}),
                ("get_valuation_comps", {"sector": HCS, "scope": "sector_only", "size_band": MID})]
    if round_no == 2:
        profile = results(messages)["profile"][0]["data"]
        direct = profile["closed_by_sector"].get(HCS, {}).get("value", 0)
        if direct >= 2:
            return [("get_acquirer_deals", {"acquirer_key": key, "sectors": [HCS]})]
        return [("get_valuation_comps", {"sector": HCS, "scope": "sector_plus_adjacent",
                                         "size_band": MID}),
                ("get_acquirer_deals", {"acquirer_key": key, "sectors": RELEVANT})]
    return []


def fact(text, *ids):
    return {"text": text, "kind": "fact", "source_ids": list(ids)}


def good_draft(messages) -> dict:
    entry = ENTRIES[buyer_key(messages)]
    found = results(messages)
    profile, comps = found["profile"][0]["data"], found["comps"][0]["data"]
    deals = [d for r in found["deals"] for d in r["data"]["deals"]
             if d["deal_size_mm"]["display"] and d["ev_ebitda_multiple"]["display"]
             and d["num_bidders"]["display"]]
    deal = deals[0]
    size = next(s for s in entry["signals"] if s["name"] == "size_fit")
    name, start = entry["display_name"], entry["starting_conviction"]
    median = comps["ev_ebitda"]["median"]
    return {
        "summary": fact(f"{name} has closed deals near the target's size.",
                        entry["supporting_closed_ids"][0]),
        "acquirer_overview": [fact(
            f"{name} is a {profile['acquirer_type']} with {profile['deal_count']['display']} deals "
            f"in the dataset, {profile['closed_count']['display']} of them closed.",
            *profile["closed_count"]["source_ids"][:3])],
        "strategic_fit": [
            fact(size["explanation"].split(";")[0] + ".", *size["source_ids"][:3]),
            {"text": "Its record near the target's size suggests a familiar cheque size.",
             "kind": "interpretation", "source_ids": size["source_ids"][:1]},
        ],
        "precedent_activity": [fact(
            f"{name} acquired {deal['target_company']} in {deal['deal_year']} for "
            f"{deal['deal_size_mm']['display']} at {deal['ev_ebitda_multiple']['display']}.",
            deal["transaction_id"])],
        "valuation_context": [fact(
            f"Closed {comps['sector']} deals of {comps['size_band']} had a median EV/EBITDA of "
            f"{median['display']} (n={comps['ev_ebitda']['sample_size']['display']}).",
            median["stat_id"])],
        "risk_flags": [
            {"risk": "Competitive process", "explanation": f"{deal['target_company']} drew "
             f"{deal['num_bidders']['display']} bidders.", "kind": "fact",
             "source_ids": [deal["transaction_id"]]},
            {"risk": "Current appetite", "explanation": "The dataset does not show current fund "
             "size or appetite.", "kind": "unknown", "source_ids": []},
        ],
        "conviction": {"level": start["level"], "reason": start["reason"].split(". ")[0] + ".",
                       "source_ids": entry["supporting_closed_ids"][:2]},
    }


def bad_draft(messages) -> dict:
    """A draft quoting a money figure that no tool returned."""
    draft = good_draft(messages)
    draft["precedent_activity"][0]["text"] += " It paid $185M."
    return draft


def write_attempt(messages) -> int:
    """0 for the first draft, 1 after one repair prompt, and so on."""
    return sum(1 for m in messages if m["role"] == "user") - 2


class FakeLLM:
    """Same interface as LLMClient; responses are scripted from the conversation."""

    model = "fake/model"

    def __init__(self, research=evidence_policy, write=None, committee=None, fail_keys=(),
                 error=lambda: RuntimeError("provider error")):
        self.research = research
        self.write = write or (lambda messages: good_draft(messages))
        self.committee = committee
        self.fail_keys = set(fail_keys)
        self.error = error
        self.calls = []
        self.totals = {"calls": 0, "api_calls": 0, "cache_hits": 0, "prompt_tokens": 0,
                       "completion_tokens": 0, "cost_usd": 0.0}

    async def supported_parameters(self) -> set[str]:
        return {"max_tokens"}

    async def chat(self, messages, tools=None, tool_choice=None, response_schema=None,
                   use_cache=True, max_tokens=None) -> LLMResult:
        self.calls.append({"messages": list(messages), "tools": tools, "tool_choice": tool_choice,
                           "schema": response_schema, "key": buyer_key(messages),
                           "max_tokens": max_tokens})
        self.totals["calls"] += 1
        await asyncio.sleep(0)
        # Failing buyers fail at the write step, after research, so a partial trace exists.
        if buyer_key(messages) in self.fail_keys and response_schema is ReportDraft:
            raise self.error()
        if response_schema is None:
            calls = self.research(messages)
            return LLMResult(content=None if calls else "I have enough evidence.", tool_calls=[
                {"id": f"call_{len(self.calls)}_{i}", "name": name,
                 "arguments": args if isinstance(args, str) else json.dumps(args)}
                for i, (name, args) in enumerate(calls)])
        payload = (self.write(messages) if response_schema is ReportDraft or response_schema.__name__ == "ReportSectionRepair"
                   else self.committee(messages))
        if response_schema.__name__ == "ReportSectionRepair" and isinstance(payload, dict):
            payload = {k: v for k, v in payload.items() if k in response_schema.model_fields}
        content = payload if isinstance(payload, str) else json.dumps(payload)
        parsed, error = _parse(content, response_schema)
        return LLMResult(content=content, tool_calls=[], parsed=parsed, parse_error=error)


async def run_one(key, llm, transport=server):
    async with MCPClient(transport=transport) as mcp:
        return await research_buyer(ENTRIES[key], TARGET, llm, mcp)


def tool_path(trace) -> list[tuple]:
    """Tool calls as (round, name, scope). Sorted, because calls in one round finish in any order."""
    return sorted((c["round"], c["tool"], c["arguments"].get("scope")) for c in trace["tool_calls"])


# ---------- MCP client ----------

@pytest.mark.anyio
async def test_stdio_tool_listing_and_chat_format():
    async with MCPClient() as mcp:
        names = [t["function"]["name"] for t in mcp.tools]
        assert sorted(names) == sorted(["get_dataset_overview", "get_sector_overview",
                                        "get_acquirer_profile", "get_acquirer_deals",
                                        "get_valuation_comps", "search_deals", "rank_acquirers"])
        comps = next(t for t in mcp.tools if t["function"]["name"] == "get_valuation_comps")
        assert comps["type"] == "function"
        assert "sample size" in comps["function"]["description"]
        assert comps["function"]["parameters"]["properties"]["scope"]["enum"] == [
            "sector_only", "sector_plus_adjacent"]
        assert "rank_acquirers" not in [t["function"]["name"] for t in agent_tools(mcp.tools)]
        assert mcp.sessions_opened == 1


@pytest.mark.anyio
async def test_stdio_concurrent_calls_on_one_session():
    keys = ["blackstone", "kkr", "gtcr", "vista equity", "bain capital", "nordic capital"]
    async with MCPClient() as mcp:
        out = await asyncio.gather(*(mcp.call_tool("get_acquirer_profile", {"acquirer_key": k})
                                     for k in keys))
        assert [o["data"]["acquirer_key"] for o in out] == keys
        assert all(o == tools.get_acquirer_profile(k) for o, k in zip(out, keys))
        assert mcp.sessions_opened == 1


@pytest.mark.anyio
async def test_tool_errors_are_results_not_exceptions():
    async with MCPClient() as mcp:
        bad_sector = await mcp.call_tool("get_sector_overview", {"sector": "Hospitals"})
        bad_type = await mcp.call_tool("get_acquirer_deals", {"acquirer_key": "kkr", "limit": "x"})
        unknown = await mcp.call_tool("no_such_tool", {})
    assert "Unknown sector 'Hospitals'" in bad_sector["error"]
    assert "limit" in bad_type["error"]
    assert "no_such_tool" in unknown["error"]


# ---------- agent loop ----------

@pytest.mark.anyio
async def test_tool_results_go_back_to_model_and_report_is_verified():
    llm = FakeLLM()
    report, evidence, trace = await run_one("nordic capital", llm)
    assert report.status == "verified", report.issues
    second_round = llm.calls[1]["messages"]
    tool_messages = [m for m in second_round if m["role"] == "tool"]
    assert len(tool_messages) == 2
    assert json.loads(tool_messages[0]["content"])["data"]["acquirer_key"] == "nordic capital"
    assert {m["tool_call_id"] for m in tool_messages} == {
        c["id"] for c in second_round[-3]["tool_calls"]}
    final = llm.calls[-1]
    assert final["schema"] is ReportDraft and final["tool_choice"] == "none" and final["tools"]
    assert [r["tool"] for r in evidence][0] == "rank_acquirers"
    assert trace["stop_reason"] == "model finished research"
    assert all(c["status"] == "ok" and c["duration_ms"] >= 0 for c in trace["tool_calls"])
    assert [c["purpose"] for c in trace["llm_calls"]] == [
        "research round 1", "research round 2", "research round 3", "write report"]


@pytest.mark.anyio
async def test_two_tool_paths_depend_on_evidence():
    _, _, strong = await run_one("nordic capital", FakeLLM())
    _, _, thin = await run_one("vns health", FakeLLM())
    assert tool_path(strong) == [(1, "get_acquirer_profile", None),
                                 (1, "get_valuation_comps", "sector_only"),
                                 (2, "get_acquirer_deals", None)]
    assert tool_path(thin) == [(1, "get_acquirer_profile", None),
                               (1, "get_valuation_comps", "sector_only"),
                               (2, "get_acquirer_deals", None),
                               (2, "get_valuation_comps", "sector_plus_adjacent")]
    deals = next(call for call in thin["tool_calls"] if call["tool"] == "get_acquirer_deals")
    assert deals["arguments"]["sectors"] == RELEVANT


@pytest.mark.anyio
async def test_tool_error_and_invalid_arguments_reach_the_model():
    def policy(messages):
        extra = [("get_sector_overview", {"sector": "Hospitals"}),
                 ("get_acquirer_deals", "{not json"),
                 ("rank_acquirers", {})]
        return extra + evidence_policy(messages) if research_round(messages) == 1 \
            else evidence_policy(messages)

    llm = FakeLLM(research=policy)
    report, evidence, trace = await run_one("nordic capital", llm)
    errors = [json.loads(m["content"])["error"] for m in llm.calls[1]["messages"]
              if m["role"] == "tool" and "error" in json.loads(m["content"])]
    assert any("Unknown sector 'Hospitals'" in e for e in errors)
    assert any("must be a JSON object" in e for e in errors)
    assert any("Unknown tool 'rank_acquirers'" in e for e in errors)
    assert [c["status"] for c in trace["tool_calls"]][:3] == ["error", "error", "error"]
    assert all(r["tool"] != "get_sector_overview" for r in evidence)
    assert report.status == "verified", report.issues


@pytest.mark.anyio
async def test_stops_at_max_tool_rounds():
    def endless(messages):
        return [("get_acquirer_profile", {"acquirer_key": buyer_key(messages)}),
                ("get_valuation_comps", {"sector": HCS, "scope": "sector_only", "size_band": MID}),
                ("get_acquirer_deals", {"acquirer_key": buyer_key(messages), "sectors": [HCS]})]

    llm = FakeLLM(research=endless)
    report, _, trace = await run_one("nordic capital", llm)
    research_calls = [c for c in llm.calls if c["schema"] is None]
    assert len(research_calls) == config.MAX_TOOL_ROUNDS
    assert trace["rounds"] == config.MAX_TOOL_ROUNDS
    assert "maximum" in trace["stop_reason"]
    assert llm.calls[-1]["schema"] is ReportDraft
    assert report.status == "verified", report.issues


@pytest.mark.anyio
async def test_repeated_identical_calls_use_the_per_buyer_cache():
    def repeat(messages):
        key = buyer_key(messages)
        return [("get_acquirer_profile", {"acquirer_key": key}),
                ("get_acquirer_profile", {"acquirer_key": key}),
                ("get_valuation_comps", {"size_band": MID, "scope": "sector_only", "sector": HCS}),
                ("get_acquirer_deals", {"acquirer_key": key, "sectors": [HCS]})]

    llm, sent = FakeLLM(research=repeat), []
    async with MCPClient(transport=server) as mcp:
        real = mcp.call_tool

        async def counting(name, arguments):
            sent.append(name)
            return await real(name, arguments)

        mcp.call_tool = counting
        report, _, trace = await research_buyer(ENTRIES["nordic capital"], TARGET, llm, mcp)
    assert sorted(sent) == ["get_acquirer_deals", "get_acquirer_profile", "get_valuation_comps"]
    assert sum(c["repeated"] for c in trace["tool_calls"]) == 4 * config.MAX_TOOL_ROUNDS - 3
    last_research = [c for c in llm.calls if c["schema"] is None][-1]["messages"]
    assert any(m["role"] == "tool" and agent.REPEAT_NOTE in m["content"] for m in last_research)
    assert report.status == "verified", report.issues


@pytest.mark.anyio
async def test_repair_fixes_a_failed_report():
    def write(messages):
        return bad_draft(messages) if write_attempt(messages) == 0 else good_draft(messages)

    llm = FakeLLM(write=write)
    report, _, trace = await run_one("nordic capital", llm)
    assert trace["first_check_issues"] and trace["repair_attempts"] == 1
    repair_message = llm.calls[-1]["messages"][-1]["content"]
    assert trace["first_check_issues"][0] in repair_message
    assert llm.calls[-1]["messages"][-2]["role"] == "assistant"
    assert report.status == "verified" and report.issues == []


@pytest.mark.anyio
async def test_failed_repair_is_marked_not_verified():
    llm = FakeLLM(write=bad_draft)
    report, _, trace = await run_one("nordic capital", llm)
    assert trace["repair_attempts"] == config.MAX_REPAIR_ATTEMPTS
    assert report.status == "not_verified"
    assert any("does not appear in the tool results" in i for i in report.issues)
    assert "**Not verified.**" in guardrails.render_markdown(report)


@pytest.mark.anyio
async def test_unparseable_output_after_repair_raises():
    with pytest.raises(ReportError, match="no valid report"):
        await run_one("nordic capital", FakeLLM(write=lambda messages: "not json"))


# ---------- committee ----------

def committee_policy(moves: dict, extra=()):
    """Keep every writer level except `moves` {key: level}; reasons copy each starting reason."""
    def respond(messages):
        text = messages[-1]["content"]
        summaries = json.loads(text.split("# Buyer summaries\n", 1)[1])
        decisions = [{"acquirer_key": s["acquirer_key"],
                      "level": moves.get(s["acquirer_key"], s["proposed_level"]),
                      "reason": s["starting_reason"].split(". ")[0] + ".",
                      "source_ids": s["proposed_reason_ids"]} for s in summaries]
        return {"decisions": decisions + list(extra), "similar_pairs": [
            {"first_key": summaries[0]["acquirer_key"], "second_key": summaries[1]["acquirer_key"],
             "shared_argument": "Same size-fit argument."}]}
    return respond


@pytest.mark.anyio
async def test_committee_moves_one_step_only():
    llm = FakeLLM()
    async with MCPClient(transport=server) as mcp:
        outs = [await research_buyer(ENTRIES[k], TARGET, llm, mcp)
                for k in ["blackstone", "vns health"]]
    reports = [o[0] for o in outs]
    evidence = {r.acquirer_key: guardrails.build_evidence(ENTRIES[r.acquirer_key], o[1])
                for r, o in zip(reports, outs)}
    review = CommitteeReview.model_validate(committee_policy(
        {"blackstone": "Medium", "vns health": "High"},
        extra=[{"acquirer_key": "nobody", "level": "Low", "reason": "x", "source_ids": []}],
    )([{"content": engine.committee_prompt(reports, ENTRIES, RANKING)}]))
    updated, record = engine.apply_committee(reports, review, evidence)
    final = {r.acquirer_key: r for r in updated}
    assert final["blackstone"].draft.conviction.level == "Medium"
    assert final["blackstone"].status == "verified"
    assert final["vns health"].draft.conviction.level == "Low"
    rejected = {r["acquirer_key"]: r["issues"] for r in record["rejected"]}
    assert "2 steps" in rejected["vns health"][0]
    assert rejected["nobody"] == ["unknown acquirer_key"]
    assert record["similar_pairs"][0]["shared_argument"] == "Same size-fit argument."


# ---------- engine ----------

@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


@pytest.mark.anyio
async def test_engine_full_run_saves_guardrails_shape(runs_dir, monkeypatch, capsys):
    llm = FakeLLM(committee=committee_policy({"blackstone": "Medium", "nordic capital": "Low"}))
    created = []

    def make_mcp(log_file=None):
        created.append(MCPClient(log_file=log_file))
        return created[-1]

    monkeypatch.setattr(engine, "LLMClient", lambda: llm)
    monkeypatch.setattr(engine, "MCPClient", make_mcp)
    events = []
    run = await engine.run_engine(config.DEFAULT_TARGET, progress=events.append)

    assert len(created) == 1 and created[0].sessions_opened == 1
    assert run["trace"]["mcp"]["sessions_opened"] == 1
    reports = run["reports"]
    assert [r.rank for r in reports] == list(range(1, config.REPORT_COUNT + 1))
    assert all(r.status == "verified" for r in reports), [r.issues for r in reports]
    levels = {r.acquirer_key: r.draft.conviction.level for r in reports}
    assert levels["blackstone"] == "Medium" and levels["nordic capital"] == "High"
    assert any(r["acquirer_key"] == "nordic capital" for r in run["trace"]["committee"]["rejected"])

    run_dir = runs_dir / run["run_id"]
    saved = json.loads((run_dir / "reports.json").read_text())
    assert set(saved) == {"run_id", "target", "reports", "research_title"}
    assert saved["research_title"] == "Healthcare Services buyer research"
    assert saved["target"] == config.DEFAULT_TARGET
    assert [BuyerReport.model_validate(r) for r in saved["reports"]] == reports
    assert (run_dir / "reports.md").read_text().startswith("# Buyer reports")
    trace = json.loads((run_dir / "trace.json").read_text())
    assert len(trace["buyers"]) == config.REPORT_COUNT and trace["substitutions"] == []
    assert trace["llm_totals"]["calls"] == len(llm.calls)
    messages = [e["message"] for e in events]
    assert any(m.startswith("Ranked ") for m in messages) and messages[-1].startswith("Saved run")

    with nullcontext() if trace["quality"]["passed"] else pytest.raises(SystemExit):
        guardrails.main()
    assert f"Run {run['run_id']}: {config.REPORT_COUNT} reports" in capsys.readouterr().out


@pytest.mark.anyio
async def test_engine_keeps_a_failed_buyer_in_place(runs_dir, monkeypatch):
    llm = FakeLLM(committee=committee_policy({}), fail_keys={"gtcr"})
    monkeypatch.setattr(engine, "LLMClient", lambda: llm)
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))
    run = await engine.run_engine(config.DEFAULT_TARGET)
    reports = run["reports"]
    keys = [r.acquirer_key for r in reports]
    assert keys == [b["acquirer_key"] for b in RANKING["buyers"][:config.REPORT_COUNT]]
    assert [r.rank for r in reports] == list(range(1, config.REPORT_COUNT + 1))
    failed = next(r for r in reports if r.acquirer_key == "gtcr")
    assert failed.status == "failed" and failed.draft is None
    assert "provider error" in failed.error and failed.issues == [failed.error]
    assert run["trace"]["substitutions"] == []
    assert not any(r.selection_note and "Replaced" in r.selection_note for r in reports)
    [error] = json.loads((runs_dir / run["run_id"] / "trace.json").read_text())["errors"]
    assert error["acquirer_key"] == "gtcr" and "provider error" in error["error"]
    partial = error["partial_trace"]
    assert partial["acquirer_key"] == "gtcr" and partial["tool_calls"]
    assert [c["purpose"] for c in partial["llm_calls"]][0] == "research round 1"
    assert run["trace"]["quality"]["failed"] == ["gtcr"]
    assert run["trace"]["quality"]["report_count"] == config.REPORT_COUNT - 1


def test_wrong_model_stops_the_run_once(runs_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "LLM_MODEL", "wrong/model")
    stub = StubOpenAI(["max_tokens"])
    monkeypatch.setattr(engine, "LLMClient", lambda: LLMClient(client=stub))
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))
    with pytest.raises(SystemExit) as exit_info:
        engine.main()
    assert exit_info.value.code == 1
    out = capsys.readouterr()
    assert out.err.splitlines() == ["Run stopped: LLMConfigError: LLM_MODEL 'wrong/model' is not "
                                    "in OpenRouter's model list. Check the model id in .env."]
    assert stub.requests == [] and "Researching" not in out.out and "Substitut" not in out.out
    assert list(runs_dir.iterdir()) == []


def test_run_folder_gets_a_suffix_when_the_name_is_taken(runs_dir, monkeypatch):
    fixed = SimpleNamespace(now=lambda: SimpleNamespace(strftime=lambda fmt: "20261031-170000"))
    monkeypatch.setattr(engine, "datetime", fixed)
    first, second, third = (engine._new_run_dir() for _ in range(3))
    assert [first[0], second[0], third[0]] == ["20261031-170000", "20261031-170000-2",
                                               "20261031-170000-3"]
    assert all(path.is_dir() for _, path in (first, second, third))


@pytest.mark.anyio
async def test_failed_ranking_leaves_no_run_folder(runs_dir, monkeypatch):
    monkeypatch.setattr(engine, "LLMClient", lambda: FakeLLM())
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))
    with pytest.raises(ValueError, match="Ranking failed"):
        await engine.run_engine(config.DEFAULT_TARGET, excluded_keys=["nobody at all"])
    assert list(runs_dir.iterdir()) == []


@pytest.mark.anyio
async def test_rate_limit_during_research_stops_without_substituting(runs_dir, monkeypatch):
    response = SimpleNamespace(status_code=429, request=None, headers={})
    llm = FakeLLM(fail_keys={"gtcr"}, error=lambda: openai.RateLimitError(
        "Rate limit exceeded", response=response, body=None))
    monkeypatch.setattr(engine, "LLMClient", lambda: llm)
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))
    events = []
    with pytest.raises(openai.RateLimitError):
        await engine.run_engine(config.DEFAULT_TARGET, progress=events.append)
    assert not any(e["message"].startswith("Substituting") for e in events)
    assert not list(runs_dir.glob("*/trace.json"))


@pytest.mark.anyio
async def test_invalid_committee_output_keeps_levels(runs_dir, monkeypatch):
    llm = FakeLLM(committee=lambda messages: "{not json")
    monkeypatch.setattr(engine, "LLMClient", lambda: llm)
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))
    run = await engine.run_engine(config.DEFAULT_TARGET)
    committee = run["trace"]["committee"]
    assert "invalid" in committee["error"] and "applied" not in committee
    for report in run["reports"]:
        assert report.draft.conviction.level == report.starting_level


# ---------- LLM client (stubbed OpenAI SDK, no network) ----------

COMMITTEE_JSON = json.dumps({"decisions": [{"acquirer_key": "kkr", "level": "Medium",
                                            "reason": "r", "source_ids": ["MA-2021-0195"]}],
                             "similar_pairs": []})


class _AsyncList:
    def __init__(self, items):
        self.items = items

    def __aiter__(self):
        async def gen():
            for item in self.items:
                yield item
        return gen()


class StubOpenAI:
    def __init__(self, supported):
        self.requests = []
        self.models = SimpleNamespace(list=lambda: _AsyncList([Model.model_validate({
            "id": "test/model", "object": "model", "created": 0, "owned_by": "test",
            "supported_parameters": supported})]))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        return ChatCompletion.model_validate({
            "id": "gen-1", "object": "chat.completion", "created": 0, "model": "test/model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": COMMITTEE_JSON}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                      "cost": 0.0012}})


@pytest.fixture
def stub_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "LLM_MODEL", "test/model")
    stub = StubOpenAI(["max_tokens", "reasoning_effort", "response_format", "structured_outputs",
                       "tools", "tool_choice"])
    return stub, LLMClient(client=stub)


@pytest.mark.anyio
async def test_llm_cache_hit_returns_same_result_without_calling_api(stub_llm):
    stub, llm = stub_llm
    messages = [{"role": "user", "content": "review"}]
    first = await llm.chat(messages, response_schema=CommitteeReview)
    second = await llm.chat(messages, response_schema=CommitteeReview)
    assert len(stub.requests) == 1
    assert not first.cache_hit and second.cache_hit
    assert (second.content, second.tool_calls, second.parsed) == (
        first.content, first.tool_calls, first.parsed)
    assert first.cost_usd == 0.0012 and first.prompt_tokens == 100 and second.cost_usd == 0.0
    await llm.chat(messages, response_schema=CommitteeReview, use_cache=False)
    assert len(stub.requests) == 2
    assert llm.totals == {"calls": 3, "api_calls": 2, "cache_hits": 1, "prompt_tokens": 200,
                          "completion_tokens": 40, "cost_usd": 0.0024}


@pytest.mark.anyio
async def test_llm_request_uses_strict_schema_and_only_supported_parameters(stub_llm):
    stub, llm = stub_llm
    result = await llm.chat([{"role": "user", "content": "x"}], response_schema=ReportDraft)
    request = stub.requests[0]
    assert "temperature" not in request and "seed" not in request
    assert request["max_tokens"] > 0
    assert request["reasoning_effort"] == config.REASONING_EFFORT
    assert result.dropped_parameters == ["seed", "temperature"]
    assert request["extra_body"] == {"provider": {"require_parameters": True}}
    schema = request["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["name"] == "ReportDraft"
    text = json.dumps(schema["schema"])
    assert "maxItems" not in text and "At most 5 items." in text
    assert result.parsed is None and "not a valid ReportDraft" in result.parse_error
    assert not list(config.CACHE_DIR.glob("*.json"))


@pytest.mark.anyio
async def test_llm_empty_supported_list_sends_no_optional_parameters(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "LLM_MODEL", "test/model")
    stub = StubOpenAI([])
    result = await LLMClient(client=stub).chat([{"role": "user", "content": "x"}],
                                               response_schema=CommitteeReview)
    [request] = stub.requests
    assert not set(OPTIONAL_PARAMETERS) & set(request)
    assert result.dropped_parameters == sorted(OPTIONAL_PARAMETERS)
    assert result.warnings == [EMPTY_SUPPORTED_WARNING]
    assert agent.llm_record("committee", result)["warnings"] == [EMPTY_SUPPORTED_WARNING]


def test_llm_client_needs_key_and_uses_config(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not set"):
        LLMClient()
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_MODEL", "test/model")
    client = LLMClient()._client
    assert str(client.base_url).rstrip("/") == config.OPENROUTER_BASE_URL
    assert client.max_retries == 0


def test_prompts_fill_every_placeholder():
    entry = dict(RANKING["buyers"][0], selection_note="Included under the balanced sensitivity scenario.")
    prompt = agent.research_prompt(entry, TARGET)
    assert "{" not in prompt.replace(json.dumps(TARGET, ensure_ascii=False), "").replace(
        json.dumps(entry, ensure_ascii=False), "")
    assert entry["selection_note"] in prompt and MID in prompt
    assert "{" not in agent.repair_prompt(["a: b"])


@pytest.mark.anyio
async def test_llm_unified_reasoning_preserves_schema_and_applies_configured_effort(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(config, 'LLM_MODEL', 'test/model')
    stub = StubOpenAI(['reasoning', 'max_tokens', 'response_format', 'structured_outputs'])
    llm = LLMClient(client=stub)
    result = await llm.chat([{'role': 'user', 'content': 'review'}], response_schema=CommitteeReview)
    request = stub.requests[0]
    assert 'reasoning_effort' not in request
    assert request['extra_body'] == {'reasoning': {'effort': config.REASONING_EFFORT, 'exclude': True},
                                     'provider': {'require_parameters': True}}
    assert 'reasoning_effort' not in result.dropped_parameters
    assert request['response_format']['json_schema']['strict'] is True


@pytest.mark.anyio
@pytest.mark.parametrize('parameters,format_type', [(['response_format'], 'json_object'), ([], None)])
async def test_structured_fallback_keeps_local_validation(tmp_path, monkeypatch, parameters, format_type):
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(config, 'LLM_MODEL', 'test/model')
    stub = StubOpenAI(parameters)
    result = await LLMClient(client=stub).chat([{'role': 'user', 'content': 'write'}], response_schema=ReportDraft)
    request = stub.requests[0]
    assert request.get('response_format', {}).get('type') == format_type
    assert 'Return only a JSON object' in request['messages'][-1]['content']
    assert result.parsed is None and result.parse_error  # invalid report is still rejected


@pytest.mark.anyio
async def test_non_tool_model_fails_before_research(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(config, 'LLM_MODEL', 'test/model')
    stub = StubOpenAI(['response_format'])
    from ma_engine.clients.llm_client import LLMConfigError
    with pytest.raises(LLMConfigError, match='tool calling'):
        await LLMClient(client=stub).validate_research_model()
    assert not stub.requests


@pytest.mark.anyio
async def test_truncated_research_result_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(config, 'LLM_MODEL', 'test/model')
    class CutOff(StubOpenAI):
        async def _create(self, **kwargs):
            response = await super()._create(**kwargs)
            response.choices[0].finish_reason = 'length'
            response.choices[0].message.content = ''
            return response
    stub = CutOff(['max_tokens'])
    client = LLMClient(client=stub)
    for _ in range(2):
        result = await client.chat([{'role': 'user', 'content': 'research'}])
        assert result.truncated and not result.cache_hit
    assert len(stub.requests) == 2
    assert not list(config.CACHE_DIR.glob('*.json'))


@pytest.mark.anyio
async def test_requested_output_budget_respects_selected_model_limit(stub_llm):
    stub, client = stub_llm
    await client.supported_parameters()
    client._max_completion_tokens = 4096
    await client.chat([{'role': 'user', 'content': 'x'}], max_tokens=16000)
    assert stub.requests[0]['max_tokens'] == 4096


@pytest.mark.anyio
async def test_partial_repair_preserves_unaffected_sections_exactly():
    initial = {}
    def write(messages):
        if write_attempt(messages) == 0:
            initial.update(bad_draft(messages))
            return initial
        updated = good_draft(messages)
        updated['summary']['text'] = 'This unrelated replacement must never be applied.'
        return updated
    llm = FakeLLM(write=write)
    report, _, trace = await run_one('nordic capital', llm)
    assert report.status == 'verified'
    assert report.draft.summary.model_dump() == initial['summary']
    assert set(llm.calls[-1]['schema'].model_fields) == {'precedent_activity'}
    assert trace['repair_attempts'] == 1


@pytest.mark.anyio
@pytest.mark.parametrize('mandatory,enabled', [(False, False), (True, True)])
async def test_on_off_reasoning_models_use_advertised_capability(tmp_path, monkeypatch, mandatory, enabled):
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(config, 'LLM_MODEL', 'test/model')
    monkeypatch.setattr(config, 'REASONING_EFFORT', 'low')
    stub = StubOpenAI(['reasoning', 'max_tokens'])
    client = LLMClient(client=stub)
    await client.supported_parameters()
    client._reasoning = {'mandatory': mandatory}
    result = await client.chat([{'role': 'user', 'content': 'x'}])
    assert stub.requests[0]['extra_body']['reasoning'] == {'enabled': enabled, 'exclude': True}
    assert result.warnings and 'on/off only' in result.warnings[0]


def test_retry_delay_respects_provider_guidance(monkeypatch):
    from types import SimpleNamespace
    from ma_engine.clients.llm_client import _retry_delay
    error = SimpleNamespace(status_code=429, response=SimpleNamespace(headers={}))
    assert _retry_delay(error, 1) == 60
    error.response.headers['retry-after'] = '12'
    assert _retry_delay(error, 1) == 12
    monkeypatch.setattr('ma_engine.clients.llm_client.time.time', lambda: 0)
    error.response.headers['retry-after'] = 'Thu, 01 Jan 1970 00:01:00 GMT'
    assert _retry_delay(error, 1) == 60
    error.response.headers['retry-after'] = 'invalid'
    assert _retry_delay(error, 1) == 60


@pytest.mark.anyio
async def test_rate_limit_retries_after_shared_cooldown(stub_llm, monkeypatch):
    stub, client = stub_llm
    clock = [100.0]
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr('ma_engine.clients.llm_client.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('ma_engine.clients.llm_client.asyncio.sleep', sleep)
    calls = []
    async def create(**kwargs):
        calls.append(clock[0])
        if len(calls) == 1:
            response = SimpleNamespace(status_code=429, request=None, headers={'retry-after': '17'})
            raise openai.RateLimitError('Slow down', response=response, body=None)
        return await stub._create(**kwargs)
    stub.chat.completions.create = create
    result = await client.chat([{'role': 'user', 'content': 'x'}], use_cache=False)
    assert calls == [100, 117]
    assert sleeps == [17]
    assert len(result.attempts) == 2 and result.attempts[-1]['error'] is None


@pytest.mark.anyio
async def test_reasoning_effort_selection_does_not_depend_on_metadata_order(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(config, 'LLM_MODEL', 'test/model')
    monkeypatch.setattr(config, 'REASONING_EFFORT', 'minimal')
    stub = StubOpenAI(['reasoning', 'max_tokens'])
    client = LLMClient(client=stub)
    await client.supported_parameters()
    client._reasoning = {'supported_efforts': ['high', 'low', 'medium']}
    await client.chat([{'role': 'user', 'content': 'x'}])
    assert stub.requests[0]['extra_body']['reasoning']['effort'] == 'low'


def test_strict_schema_has_no_ref_siblings_and_retains_statement_constraints():
    from ma_engine.clients.llm_client import strict_schema
    schema = strict_schema(ReportDraft)
    def inspect(node):
        if isinstance(node, dict):
            if '$ref' in node:
                assert set(node) == {'$ref'}
            for child in node.values():
                inspect(child)
        elif isinstance(node, list):
            for child in node:
                inspect(child)
    inspect(schema)
    summary = schema['properties']['summary']
    assert summary['additionalProperties'] is False
    assert set(summary['required']) == {'text', 'kind', 'source_ids'}
    assert summary['description'].startswith('One sentence')


def test_failed_reports_still_get_resolvable_ranking_citations():
    from types import SimpleNamespace
    reports = [SimpleNamespace(acquirer_key=entry['acquirer_key']) for entry in RANKING['buyers'][:10]]
    sources = engine.complete_ranking_sources({'numbers': {}, 'entries': []}, RANKING, reports)
    assert len(sources['numbers']) == 30
    for report in reports:
        for field in ['relevant_closed_count', 'direct_closed_count', 'near_size_closed_count']:
            source_id = ENTRIES[report.acquirer_key][field]['stat_id']
            assert source_id in sources['numbers']
    assert engine.complete_ranking_sources(sources, RANKING, reports) == sources


def test_strict_followup_schema_requires_defaulted_output_fields():
    from ma_engine.clients.llm_client import strict_schema
    from ma_engine.models import FollowupAnswer
    schema = strict_schema(FollowupAnswer)
    assert set(schema['required']) == set(schema['properties'])
    assert 'include_source_catalog' in schema['required']
    # Existing locally stored answers can still omit the defaulted flag.
    assert FollowupAnswer(statements=[]).include_source_catalog is False
