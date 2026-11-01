"""Activity events, request parsing and follow-up answers. No real LLM.

A scripted model, the same shape as tests/test_agent.py. The event test uses the real MCP
tools in memory. The follow-up save test starts the real stdio server once.
"""

import asyncio
import json
from collections import defaultdict
from pathlib import Path

import httpx2
import openai
import pytest
from test_agent import (ENTRIES, TARGET, FakeLLM, StubOpenAI, committee_policy, research_round,
                        results)

from ma_engine import agent, config, engine
from ma_engine.agent import RequestError, research_buyer
from ma_engine.clients.llm_client import LLMClient, LLMResult, _parse
from ma_engine.clients.mcp_client import MCPClient
from ma_engine.mcp_server import tools
from ma_engine.mcp_server.server import mcp as server
from ma_engine.models import Target

SECRET = "sk-test-SECRET-KEY-9f3a"
ASSIGNMENT = ("Find the 10 most likely acquirers for a regional Healthcare Services company "
              "valued at $200M EV with strong EBITDA margins.")
FIELDS = set(agent.EVENT_FIELDS)


class ScriptLLM:
    """Returns one fixed structured payload. `payload` is a dict or raw text."""

    model = "fake/model"

    def __init__(self, payload):
        self.payload = payload
        self.totals = {"calls": 0, "api_calls": 0, "cache_hits": 0, "prompt_tokens": 0,
                       "completion_tokens": 0, "cost_usd": 0.0}

    async def chat(self, messages, tools=None, tool_choice=None, response_schema=None,
                   use_cache=True, max_tokens=None):
        self.totals["calls"] += 1
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        parsed, error = (None, None)
        if response_schema is not None:
            parsed, error = _parse(content, response_schema)
        return LLMResult(content=content, tool_calls=[], parsed=parsed, parse_error=error)


class FollowLLM:
    """Tool calls from `research`, then a FollowupAnswer from `answer`."""

    model = "fake/model"

    def __init__(self, answer, research=None):
        self.answer = answer
        self.research = research or (lambda messages: (
            [("get_acquirer_deals", {"acquirer_key": "blackstone", "sectors": ["Healthcare Services"]})]
            if research_round(messages) == 1 else []))
        self.totals = {"calls": 0, "api_calls": 0, "cache_hits": 0, "prompt_tokens": 0,
                       "completion_tokens": 0, "cost_usd": 0.0}

    async def chat(self, messages, tools=None, tool_choice=None, response_schema=None,
                   use_cache=True, max_tokens=None):
        self.totals["calls"] += 1
        if response_schema is None:
            calls = self.research(messages)
            return LLMResult(content=None if calls else "I have enough evidence.", tool_calls=[
                {"id": f"c{self.totals['calls']}_{i}", "name": name, "arguments": json.dumps(args)}
                for i, (name, args) in enumerate(calls)])
        payload = self.answer(messages)
        content = payload if isinstance(payload, str) else json.dumps(payload)
        parsed, error = _parse(content, response_schema)
        return LLMResult(content=content, tool_calls=[], parsed=parsed, parse_error=error)


def _repairs(messages) -> int:
    return sum(1 for m in messages if m["role"] == "user" and "did not pass" in m["content"])


def deal_answer(messages, extra=""):
    deals = [d for row in results(messages)["deals"] for d in row["data"]["deals"]
             if d.get("is_closed") and d["deal_size_mm"]["display"]]
    deal = deals[0]
    text = (f"{deal['target_company']} in {deal['deal_year']} was a deal of "
            f"{deal['deal_size_mm']['display']}.{extra}")
    return {"statements": [{"text": text, "kind": "fact", "source_ids": [deal["transaction_id"]]}]}


def bad_answer(messages):
    return deal_answer(messages, " It paid $185M.")


def repaired_answer(messages):
    return bad_answer(messages) if not _repairs(messages) else deal_answer(messages)


def unknown_answer(messages):
    return {"statements": [{"text": "The dataset does not show current appetite.",
                            "kind": "unknown", "source_ids": []}]}


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


def one_buyer_run(tmp_path):
    """A saved run with one real report, enough for a follow-up to be checked."""

    async def build():
        async with MCPClient(transport=server) as mcp:
            report, _, buyer_trace = await research_buyer(ENTRIES["blackstone"], TARGET, FakeLLM(), mcp)
        run_dir = tmp_path / "one"
        run_dir.mkdir()
        engine.save_run(run_dir, "one", Target.default(), [report],
                        {"buyers": [buyer_trace], "events": []}, tools.rank_acquirers()["data"])
        return engine.load_run(run_dir)

    return asyncio.run(build())


# ---------- events, ranking file, saved run ----------

def test_events_match_execution_and_ranking_is_saved(runs_dir, monkeypatch):
    monkeypatch.setattr(config, "REPORT_COUNT", 2)
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", SECRET)
    seen = []
    original = MCPClient.call_tool

    async def spy(self, name, arguments):
        result = await original(self, name, arguments)
        seen.append({"name": name, "arguments": arguments, "result": result})
        return result

    monkeypatch.setattr(MCPClient, "call_tool", spy)
    monkeypatch.setattr(engine, "LLMClient", lambda: FakeLLM(committee=committee_policy({})))
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))
    events = []
    sentence = "Find buyers for a regional clinic worth $200M."
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET, progress=events.append,
                                        request=sentence))

    groups = defaultdict(list)
    for event in events:
        assert set(event) == FIELDS
        assert event["category"] in {"llm", "mcp_tool", "validation", "calculation", "app"}
        assert event["ts"].endswith("Z")
        if event["category"] == "llm" and isinstance(event["output"], str):
            assert len(event["output"]) <= agent.VISIBLE_TEXT
        groups[event["step"]].append(event)
    running = [event["step"] for event in events if event["status"] == "running"]
    assert running == list(range(1, len(running) + 1))
    for pair in groups.values():
        assert len(pair) == 2
        assert pair[0]["status"] == "running" and pair[1]["status"] in ("completed", "failed")
        assert pair[0]["step"] == pair[1]["step"]
        assert pair[0]["category"] == pair[1]["category"] and pair[0]["name"] == pair[1]["name"]
        assert pair[0]["duration_ms"] is None and pair[1]["duration_ms"] is not None

    finished = [e for e in events if e["category"] == "mcp_tool" and e["status"] != "running"]
    assert len(finished) == len(seen)
    for event, call in zip(finished, seen):
        assert event["name"] == call["name"]
        assert event["input"] == call["arguments"]
        assert event["output"] == call["result"]

    done = {(e["category"], e["name"]) for e in events if e["status"] != "running"}
    for pair in (("app", "run"), ("app", "mcp_connected"), ("mcp_tool", "rank_acquirers"),
                 ("calculation", "ranking"), ("llm", "research"), ("llm", "write"),
                 ("validation", "check"), ("llm", "committee"), ("calculation", "apply_committee"),
                 ("app", "save")):
        assert pair in done
    decisions = [e["decision"] for e in events]
    assert any(d and d.startswith("Requested ") and "get_acquirer_profile" in d for d in decisions)
    assert "No further tool calls; moved to writing" in decisions
    assert any(d and d.startswith("Guardrails found 0") for d in decisions)
    assert events[-1]["message"].startswith("Saved run")

    run_dir = runs_dir / run["run_id"]
    ranking = json.loads((run_dir / "ranking.json").read_text())
    assert ranking["candidate_count"] == run["ranking"]["candidate_count"]
    assert ranking["buyers"][0]["acquirer_key"] == run["ranking"]["buyers"][0]["acquirer_key"]
    loaded = engine.load_run(run_dir)
    assert loaded["run_id"] == run["run_id"]
    assert loaded["target"] == run["target"]
    assert loaded["research_title"] == "Healthcare Services buyer research"
    assert "research_title" not in loaded["target"].model_dump()
    for call in seen:
        assert "research_title" not in call["arguments"]
        assert "buyer research" not in json.dumps(call["arguments"])
    assert not any(event["name"] == "parse_request" for event in events)
    assert [r.acquirer_key for r in loaded["reports"]] == [r.acquirer_key for r in run["reports"]]
    assert loaded["ranking"]["candidate_count"] == ranking["candidate_count"]
    saved_events = loaded["trace"]["events"]
    assert [e["step"] for e in saved_events] == [e["step"] for e in events]
    assert saved_events[-1]["name"] == "save" and saved_events[-1]["status"] == "completed"
    assert run["trace"]["request"] == sentence
    run_steps = [event for event in events if event["name"] == "run"]
    assert run_steps[0]["status"] == "running" and run_steps[0]["input"]["request"] == sentence
    assert run_steps[1]["input"]["request"] == sentence
    assert run_steps[0]["input"]["sector"] == "Healthcare Services"
    assert run["trace"]["totals"]["mcp_tool_calls"] == len(finished)
    assert run["trace"]["totals"]["llm_calls"] == run["trace"]["llm_totals"]["calls"]
    assert SECRET not in (run_dir / "trace.json").read_text()


def test_cli_prints_event_messages(runs_dir, monkeypatch, capsys):
    monkeypatch.setattr(config, "REPORT_COUNT", 2)
    monkeypatch.setattr(engine, "LLMClient", lambda: FakeLLM(committee=committee_policy({})))
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))
    engine.main()
    out = capsys.readouterr().out
    assert "MCP server connected" in out
    assert "Ranked " in out
    assert "Saved run" in out
    assert "Wall time:" in out


# ---------- LLM retries ----------

def test_retry_then_success_is_attempt_two(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "LLM_MODEL", "test/model")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", SECRET)
    monkeypatch.setattr("ma_engine.clients.llm_client._backoff", lambda attempt: 0)

    class Flaky(StubOpenAI):
        def __init__(self):
            self.n = 0
            super().__init__(["max_tokens"])

        async def _create(self, **kwargs):
            self.n += 1
            if self.n == 1:
                self.requests.append(kwargs)
                request = httpx2.Request("POST", "https://example.com/v1/chat")
                raise openai.APIConnectionError(message=f"reset {SECRET}", request=request)
            return await super()._create(**kwargs)

    flaky = Flaky()
    events = []
    result = asyncio.run(agent.call_llm(
        LLMClient(client=flaky), [{"role": "user", "content": "x"}], purpose="parse_request",
        steps=agent.Steps(), progress=events.append, use_cache=False))
    assert flaky.n == 2
    assert [a["n"] for a in result.attempts] == [1, 2]
    assert result.attempts[0]["error"] and SECRET not in result.attempts[0]["error"]
    assert "***" in result.attempts[0]["error"]
    assert result.attempts[1]["error"] is None
    finished = [e for e in events if e["status"] == "completed"]
    assert finished[-1]["attempts"] == result.attempts
    assert finished[-1]["category"] == "llm" and finished[-1]["name"] == "parse_request"
    assert set(finished[-1]["input"]) == {"model", "message_count", "tool_count"}
    dumped = json.dumps(events)
    assert SECRET not in dumped and "Authorization" not in dumped


# ---------- request parsing ----------

def test_parse_assignment_sentence_is_the_default_target():
    payload = {"sector": "Healthcare Services", "ev_value": 200, "ev_unit": "million",
               "ownership": None, "geography": "Multi-Regional", "margin_profile": "strong"}
    events = []
    llm = ScriptLLM(payload)
    target, defaulted, title = asyncio.run(agent.parse_request(ASSIGNMENT, llm,
                                                               progress=events.append))
    assert llm.totals["calls"] == 1
    assert title is None
    assert "title" not in target.model_dump()
    assert target == Target.default()
    assert target.geography == "Any region"
    assert defaulted == ["ownership"]
    assert any(e["name"] == "parse_request" and e["category"] == "llm" and e["status"] == "completed"
               for e in events)
    assert events[-1]["name"] == "request" and events[-1]["status"] == "completed"
    assert "ownership" in events[-1]["message"]


def test_parse_named_region_and_ev_and_defaulted_fields():
    text = "Find buyers for a Northeast Healthcare Services business worth $450M EV."
    payload = {"sector": "Healthcare Services", "ev_value": 450, "ev_unit": "million",
               "ownership": None, "geography": None, "margin_profile": None}
    target, defaulted, title = asyncio.run(agent.parse_request(text, ScriptLLM(payload)))
    assert title is None
    assert (target.sector, target.geography, target.ev_mm) == ("Healthcare Services", "Northeast", 450)
    assert target.ownership == "Private" and target.margin_profile == "strong"
    assert defaulted == ["ownership", "margin_profile"]


def test_parse_rejects_a_value_the_engine_cannot_use():
    payload = {"sector": "Healthcare Services", "ev_value": 500_000, "ev_unit": "million",
               "ownership": None, "geography": None, "margin_profile": None}
    with pytest.raises(RequestError, match="cannot use"):
        asyncio.run(agent.parse_request("A company worth far too much.", ScriptLLM(payload)))


def test_parse_rejects_output_that_is_not_a_target():
    with pytest.raises(RequestError, match="Could not read a target"):
        asyncio.run(agent.parse_request("Hello.", ScriptLLM("not json at all")))


def test_parse_target_creates_one_client(monkeypatch):
    created = []
    payload = {"sector": "Healthcare Services", "ev_value": 200, "ev_unit": "million",
               "ownership": None, "geography": "Any region", "margin_profile": "strong"}

    class One(ScriptLLM):
        def __init__(self):
            created.append(self)
            super().__init__(payload)

    monkeypatch.setattr(engine, "LLMClient", One)
    target, defaulted, title = asyncio.run(engine.parse_target(ASSIGNMENT))
    assert len(created) == 1
    assert title is None
    assert target == Target.default() and defaulted == ["ownership"]


def _parse_payload(**extra):
    payload = {"sector": "Healthcare Services", "ev_value": 200, "ev_unit": "million",
               "ownership": None, "geography": "Any region", "margin_profile": "strong"}
    payload.update(extra)
    return payload


def test_parse_keeps_a_valid_title_in_one_call():
    llm = ScriptLLM(_parse_payload(title="  regional\nhealthcare buyers  "))
    target, defaulted, title = asyncio.run(agent.parse_request(ASSIGNMENT, llm))
    assert llm.totals["calls"] == 1
    assert title == "Regional healthcare buyers"
    assert defaulted == ["ownership"]
    assert target == Target.default()
    assert "title" not in target.model_dump()
    assert "research_title" not in target.model_dump()


def test_a_bad_title_does_not_fail_the_parse_or_call_again():
    for bad in ("New research", "Regional healthcare buyers.", '"Quoted buyer research"',
                "Too short", "One two three four five six seven", "Regional buyers 200",
                "Regional healthcare buyers!!!", None, ["Regional healthcare buyers"]):
        llm = ScriptLLM(_parse_payload(title=bad))
        target, _defaulted, title = asyncio.run(agent.parse_request(ASSIGNMENT, llm))
        assert llm.totals["calls"] == 1
        assert title is None
        assert target.sector == "Healthcare Services"


def test_old_and_new_parse_payloads_both_validate():
    from ma_engine.clients.llm_client import strict_schema
    from ma_engine.models import RequestFields

    old = _parse_payload()
    parsed, error = _parse(json.dumps(old), RequestFields)
    assert error is None and parsed.title is None
    new = _parse_payload(title="Regional healthcare buyers")
    parsed, error = _parse(json.dumps(new), RequestFields)
    assert error is None and parsed.title == "Regional healthcare buyers"
    bad_type, error = _parse(json.dumps(_parse_payload(title={"name": "nope"})), RequestFields)
    assert error is None and bad_type.title is None
    schema = strict_schema(RequestFields)
    assert "title" in schema["properties"]
    assert "title" in schema["required"]
    assert "default" not in schema["properties"]["title"]


def test_saved_title_reloads_and_a_missing_title_falls_back(tmp_path):
    folder = tmp_path / "20261101-014500"
    folder.mkdir()
    engine.save_run(folder, folder.name, Target.default(), [], {"events": []}, {"buyers": []},
                    research_title="Regional healthcare buyers")
    saved = json.loads((folder / "reports.json").read_text())
    assert saved["research_title"] == "Regional healthcare buyers"
    assert "research_title" not in saved["target"]
    assert "title" not in saved["target"]
    loaded = engine.load_run(folder)
    assert loaded["research_title"] == "Regional healthcare buyers"
    again = engine.load_run(folder)
    assert again["research_title"] == loaded["research_title"]
    assert (folder / "reports.json").read_text() == json.dumps(saved, indent=2, ensure_ascii=False) + "\n"

    plain = tmp_path / "plain"
    plain.mkdir()
    engine.save_run(plain, "plain", Target.default(), [], {"events": []}, {"buyers": []})
    assert json.loads((plain / "reports.json").read_text())["research_title"] == (
        "Healthcare Services buyer research")

    rejected = tmp_path / "rejected"
    rejected.mkdir()
    engine.save_run(rejected, "rejected", Target.default(), [], {"events": []}, {"buyers": []},
                    research_title="New research")
    assert engine.load_run(rejected)["research_title"] == "Healthcare Services buyer research"

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    body = {"run_id": "legacy", "target": Target.default().model_dump(), "reports": []}
    path = legacy / "reports.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    before = path.read_text()
    assert engine.load_run(legacy)["research_title"] == "Healthcare Services buyer research"
    assert path.read_text() == before


def test_fallback_keeps_the_sector_spelling():
    assert agent.fallback_research_title("Home Health/Hospice") == "Home Health/Hospice buyer research"
    assert agent.fallback_research_title("") == "Buyer research"
    assert agent.fallback_research_title(None) == "Buyer research"
    assert agent.display_research_title(
        "Home Health/Hospice buyer research", "Home Health/Hospice") == (
            "Home Health/Hospice buyer research")
    assert agent.clean_research_title("Strategic software acquirers") == "Strategic software acquirers"
    assert agent.clean_research_title("Manufacturing buyer research") == "Manufacturing buyer research"


# ---------- follow-up answers ----------

@pytest.fixture
def buyer_run(tmp_path):
    return one_buyer_run(tmp_path)


def _ask(run, answer, research=None):
    async def go():
        llm = FollowLLM(answer, research)
        async with MCPClient(transport=server) as mcp:
            return await agent.answer_followup("Which deal is closest?", run, llm, mcp)

    return asyncio.run(go())


def test_followup_cited_answer_is_verified(buyer_run):
    result = _ask(buyer_run, deal_answer)
    assert result.status == "verified" and result.issues == []
    assert result.statements[0].kind == "fact"
    cited = result.statements[0].source_ids[0]
    assert f"[{cited}]" in result.markdown
    tools_done = [e for e in result.events if e["category"] == "mcp_tool" and e["status"] == "completed"]
    assert tools_done and tools_done[0]["name"] == "get_acquirer_deals"
    assert tools_done[0]["input"] == {"acquirer_key": "blackstone", "sectors": ["Healthcare Services"]}
    assert "deals" in tools_done[0]["output"]["data"]
    assert result.totals["llm_calls"] >= 1 and "wall_time_s" in result.totals


def test_followup_works_when_a_buyer_failed(buyer_run):
    written = buyer_run["reports"][0]
    failed = written.model_copy(update={"draft": None, "status": "failed", "issues": [],
                                        "error": "ReportError: no valid report"})
    run = {**buyer_run, "reports": [written, failed]}
    summaries = agent.report_summaries(run["reports"])
    assert summaries[1]["conviction"] is None and summaries[1]["strategic_fit"] == []
    assert summaries[1]["status"] == "failed"
    result = _ask(run, deal_answer)
    assert result.status == "verified"


def test_followup_swapped_number_is_repaired(buyer_run):
    result = _ask(buyer_run, repaired_answer)
    assert result.status == "verified" and result.issues == []
    assert any(e["name"] == "repair" and e["status"] == "completed" for e in result.events)
    assert any(e["decision"] and "sent one repair" in e["decision"] for e in result.events)


def test_followup_repair_that_still_fails_is_not_verified(buyer_run):
    result = _ask(buyer_run, bad_answer)
    assert result.status == "not_verified"
    assert any("does not appear" in issue for issue in result.issues)
    assert "Not verified" in result.markdown


def test_followup_unknown_when_the_data_cannot_answer(buyer_run):
    result = _ask(buyer_run, unknown_answer, research=lambda messages: [])
    assert result.status == "verified"
    assert result.statements[0].kind == "unknown" and result.statements[0].source_ids == []
    assert not any(e["category"] == "mcp_tool" for e in result.events)


def test_run_followup_opens_one_session_and_saves(buyer_run, monkeypatch):
    created = []
    real = engine.MCPClient

    def make(log_file=None):
        client = real(log_file=log_file)
        created.append(client)
        return client

    monkeypatch.setattr(engine, "MCPClient", make)
    monkeypatch.setattr(engine, "LLMClient", lambda: FollowLLM(unknown_answer, research=lambda messages: []))
    question = "What is their current fund size?"
    reports_path = Path(buyer_run["run_dir"]) / "reports.json"
    before = reports_path.read_text()
    result = asyncio.run(engine.run_followup(question, buyer_run))
    assert len(created) == 1 and created[0].sessions_opened == 1
    assert result.status == "verified"
    assert reports_path.read_text() == before
    assert json.loads(before)["research_title"] == "Healthcare Services buyer research"
    assert not any(event.get("name") == "parse_request" for event in result.events)
    path = Path(buyer_run["run_dir"]) / "followups.json"
    saved = json.loads(path.read_text())
    assert isinstance(saved, list) and saved[0]["question"] == question
    assert saved[0]["status"] == "verified" and saved[0]["statements"][0]["kind"] == "unknown"
    assert saved[0]["events"] and saved[0]["markdown"].startswith("**Status:**")


def test_stage_groups_partition_events_and_keep_housekeeping():
    from ma_engine.ui_trace import stage_groups
    events = [
        {"step": 1, "category": "mcp_tool", "name": "rank_acquirers", "status": "running"},
        {"step": 1, "category": "mcp_tool", "name": "rank_acquirers", "status": "completed"},
        {"step": 2, "category": "llm", "name": "research", "status": "completed"},
        {"step": 3, "category": "llm", "name": "write", "status": "completed"},
        {"step": 4, "category": "app", "name": "save", "status": "completed"},
    ]
    groups = {g["id"]: g["steps"] for g in stage_groups(events)}
    assert len(groups["identify"]) == 1
    assert groups["identify"][0]["status"] == "completed"
    assert groups["investigate"][0]["step"] == 2
    assert groups["reports"][0]["step"] == 3
    assert groups["technical"][0]["step"] == 4
    assert sum(map(len, groups.values())) == 4


def test_stage_result_distinguishes_repaired_checks_from_unresolved():
    from ma_engine.ui_trace import stage_result
    events = [
        {"category": "validation", "acquirer_key": "example", "output": ["bad citation"]},
        {"category": "validation", "acquirer_key": "example", "output": []},
    ]
    group = {"id": "reports", "steps": events}
    assert "latest checks passed" in stage_result(group)
    assert "latest checks need review" not in stage_result(group)
    events[-1]["output"] = ["still wrong"]
    assert "1 latest checks need review" in stage_result(group)


def test_request_rejects_unsupported_constraints_before_model_call():
    for question in ("Find 50 likely acquirers.",
                     "Find buyers for a $200M company with a 90% EBITDA margin.",
                     "Find buyers for a company in an industry not represented in the dataset.",
                     "Find only financial sponsors that have acquired similar healthcare businesses."):
        with pytest.raises(RequestError):
            asyncio.run(agent.parse_request(question, None))


def test_average_margin_request_does_not_default_to_strong():
    fields = {"sector": "Healthcare Services", "ev_value": 350, "ev_unit": "million",
              "ownership": None, "geography": None, "margin_profile": None}
    target, defaults, _ = asyncio.run(agent.parse_request(
        "Find buyers for a $350M Healthcare Services business with average margins.", ScriptLLM(fields)))
    assert target.margin_profile == "any" and "margin_profile" not in defaults


def test_llm_timeout_stops_at_retry_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "LLM_MODEL", "test/model")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", SECRET)
    monkeypatch.setattr("ma_engine.clients.llm_client._backoff", lambda attempt: 0)
    class TimedOut(StubOpenAI):
        async def _create(self, **kwargs):
            self.requests.append(kwargs)
            raise openai.APITimeoutError(request=httpx2.Request("POST", "https://example.com/chat"))
    client = TimedOut(["max_tokens"])
    with pytest.raises(openai.APITimeoutError):
        asyncio.run(LLMClient(client=client).chat([{"role": "user", "content": "test"}], use_cache=False))
    assert len(client.requests) == config.MAX_RETRIES


def test_saved_run_context_handles_trace_counts_and_has_separate_provenance(buyer_run):
    from ma_engine.followup_context import saved_context
    from ma_engine.guardrails import build_answer_evidence, check_statements
    from ma_engine.models import Statement
    buyer_run['trace']['events'] = [{'step': 1, 'category': 'mcp_tool', 'name': 'get_acquirer_profile',
                                     'output_summary': {'source_ids': 25, 'rows': 1}}]
    context, result = saved_context(buyer_run, 'What tools did you call for Blackstone?')
    ev = build_answer_evidence(buyer_run['ranking']['target'], agent.run_entries(buyer_run), [{'result': result}])
    sid = context['run_record']['stat_id']
    assert sid.startswith('ST-RUN-') and sid in ev.stats
    assert not check_statements([Statement(text='The recorded tool returned 25 source IDs.', kind='fact', source_ids=[sid])], ev)
    assert context['run_record']['details']['activity'][0]['name'] == 'get_acquirer_profile'


def test_followup_run_context_uses_actual_citation_and_previous_buyer(buyer_run):
    from ma_engine.followup_context import saved_context
    buyer_run['followups'] = [{'question': 'What is Blackstone doing?', 'statements': [], 'status': 'verified'}]
    context, result = saved_context(buyer_run, 'Show me the source for your median deal size.')
    assert context['requested_buyer_reports'][0]['display_name'] == 'Blackstone'
    assert context['previous_turns_for_reference_only'][0]['question'] == 'What is Blackstone doing?'
    assert result['data']['saved_sources']
