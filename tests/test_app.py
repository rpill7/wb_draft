"""Streamlit app tests with streamlit.testing.v1.AppTest. No real LLM.

The app runs in this process, so settings and the engine's clients are replaced with fakes.
A full mocked run uses the same scripted model as tests/test_agent.py, plus request parsing
and follow-up answers.
"""

import asyncio
import json

import pytest
from streamlit.testing.v1 import AppTest
from test_activity import deal_answer
from test_agent import FakeLLM, bad_draft, buyer_key, committee_policy, good_draft, research_round

from ma_engine import config, engine
from ma_engine.clients import mcp_client
from ma_engine.clients.llm_client import LLMResult, _parse
from ma_engine.clients.mcp_client import MCPClient
from ma_engine.mcp_server.server import mcp as server
from ma_engine.agent import RequestError
from ma_engine.models import FollowupAnswer, RequestFields
from ma_engine.ui_trace import (
    PREVIEW_LIMIT,
    as_markdown,
    buyer_status_lines,
    order_events,
    preview_json,
    row_label,
)

APP = str(config.ROOT / "src" / "ma_engine" / "app.py")
EXAMPLE = (
    "Find the 10 most likely acquirers for a regional Healthcare Services company "
    "valued at $200M EV with strong EBITDA margins."
)


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "SAMPLE_DIR", tmp_path / "sample")
    return tmp_path


def start(timeout: float = 60) -> AppTest:
    return AppTest.from_file(APP, default_timeout=timeout).run(timeout=timeout)


class AppLLM(FakeLLM):
    """FakeLLM plus the two structured calls the chat screen makes: parse and follow-up."""

    def __init__(self):
        super().__init__(committee=committee_policy({}))

    def _structured(self, payload, schema) -> LLMResult:
        self.totals["calls"] += 1
        content = json.dumps(payload)
        parsed, error = _parse(content, schema)
        return LLMResult(content=content, tool_calls=[], parsed=parsed, parse_error=error)

    async def chat(self, messages, tools=None, tool_choice=None, response_schema=None,
                   use_cache=True) -> LLMResult:
        if response_schema is RequestFields:
            return self._structured({
                "sector": "Healthcare Services", "ev_value": 200, "ev_unit": "million",
                "ownership": None, "geography": None, "margin_profile": "strong",
            }, response_schema)
        if response_schema is FollowupAnswer:
            try:
                payload = deal_answer(messages)
            except (IndexError, KeyError, StopIteration, TypeError):
                payload = {"statements": [{
                    "text": "The dataset does not show current appetite.",
                    "kind": "unknown", "source_ids": [],
                }]}
            return self._structured(payload, response_schema)
        if response_schema is None and buyer_key(messages) is None:
            calls = ([("get_acquirer_deals",
                       {"acquirer_key": "blackstone", "sectors": ["Healthcare Services"]})]
                     if research_round(messages) == 1 else [])
            self.totals["calls"] += 1
            return LLMResult(
                content=None if calls else "I have enough evidence.",
                tool_calls=[{"id": f"c{self.totals['calls']}_{i}", "name": name,
                             "arguments": json.dumps(args)}
                            for i, (name, args) in enumerate(calls)])
        return await super().chat(messages, tools=tools, tool_choice=tool_choice,
                                  response_schema=response_schema, use_cache=use_cache)


def fake_engine(monkeypatch, llm=None) -> None:
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(engine, "LLMClient", lambda: llm or AppLLM())
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))


def blob(at: AppTest) -> str:
    parts = [element.value for element in (
        *at.markdown, *at.text, *at.caption, *at.info, *at.success, *at.warning,
        *at.title, *at.header, *at.subheader,
    )]
    parts += [element.value for element in at.error]
    parts += [element.label for element in (*at.expander, *at.status, *at.button)]
    return "\n".join(parts)


def errors(at: AppTest) -> str:
    return " ".join(element.value for element in at.error)


# ---------- timeline helpers (no Streamlit script) ----------

def test_as_markdown_escapes_each_dollar_once():
    phrase = "$200.0M (between $100.0M and $400.0M)"
    escaped = as_markdown(phrase)
    assert escaped == r"\$200.0M (between \$100.0M and \$400.0M)"
    assert as_markdown(escaped) == escaped


def test_row_label_skips_empty_fields_and_badges_the_category():
    plain = row_label({
        "step": 1, "ts": "2026-10-31T01:14:29.000Z", "category": "app", "name": "run",
        "acquirer_key": None, "round": None, "status": "completed", "duration_ms": 0,
        "decision": "Started the run",
    }, {})
    assert plain.startswith("**#1** · 01:14:29 · :primary-badge[App] · run · completed · 0 ms")
    assert "round" not in plain
    assert plain.endswith("— Started the run")

    research = row_label({
        "step": 5, "ts": "2026-10-31T01:14:30.000Z", "category": "llm", "name": "research",
        "acquirer_key": "blackstone", "round": 1, "status": "completed", "duration_ms": 0,
        "decision": "Requested 2 tools: get_acquirer_profile",
    }, {"blackstone": "Blackstone"})
    assert ":blue-badge[LLM]" in research
    assert "Blackstone" in research and "R1" in research
    assert research.endswith("— Requested 2 tools: get_acquirer_profile")
    assert ":red-badge[failed]" not in research

    failed = row_label({
        "step": 6, "category": "mcp_tool", "name": "get_acquirer_deals",
        "acquirer_key": "kkr", "round": 2, "status": "failed", "duration_ms": None,
        "decision": None, "ts": None,
    }, {})
    assert ":green-badge[MCP tool]" in failed
    assert ":red-badge[failed]" in failed
    assert "R2" in failed
    assert " ms" not in failed
    assert "—" not in failed

    running = row_label({
        "step": 7, "category": "validation", "name": "check", "status": "running",
        "acquirer_key": None, "round": None, "duration_ms": None, "ts": None,
    }, {})
    assert ":orange-badge[Validation]" in running
    assert ":shimmer[running]" in running


def test_preview_json_does_not_keep_a_hundred_thousand_character_output():
    blob_text = "x" * 100_000
    preview, full, truncated = preview_json({"payload": blob_text})
    assert truncated is True
    assert len(preview) == PREVIEW_LIMIT
    assert blob_text not in preview
    assert blob_text in full
    assert len(full) > 100_000


def test_live_lines_skip_the_full_output_and_rounds_stay_with_the_buyer():
    huge = "y" * 100_000
    events = [
        {"step": 1, "name": "run", "category": "app", "status": "completed", "acquirer_key": None,
         "round": None, "output": huge, "output_summary": {"tools": 7}, "message": "Started"},
        {"step": 2, "name": "get_acquirer_profile", "category": "mcp_tool", "status": "running",
         "acquirer_key": "blackstone", "round": 1, "output": huge},
        {"step": 2, "name": "get_acquirer_profile", "category": "mcp_tool", "status": "completed",
         "acquirer_key": "blackstone", "round": 1, "output": huge,
         "output_summary": {"rows": 4}, "decision": "Called the profile"},
        {"step": 3, "name": "get_acquirer_deals", "category": "mcp_tool", "status": "completed",
         "acquirer_key": "kkr", "round": 1, "output": huge},
        {"step": 4, "name": "get_acquirer_deals", "category": "mcp_tool", "status": "completed",
         "acquirer_key": "blackstone", "round": 2, "output": huge},
        {"step": 5, "name": "save", "category": "app", "status": "completed", "acquirer_key": None,
         "round": None, "output": huge},
    ]
    mcp, body = buyer_status_lines(events)
    assert huge not in body and huge not in mcp
    assert "blackstone" in body and "R2" in body
    assert "round —" not in body and huge not in body
    ordered = order_events(events)
    buyers = [event.get("acquirer_key") for event in ordered]
    assert buyers[0] is None and buyers[-1] is None
    blackstone = [event["step"] for event in ordered if event.get("acquirer_key") == "blackstone"]
    assert blackstone == [2, 4]
    assert ordered[buyers.index("blackstone")]["status"] == "completed"


# ---------- app ----------

def test_app_loads_with_three_nav_sections(folders):
    at = start()
    assert not at.exception
    assert at.title[0].value == "M&A Acquirer Identification"
    radio = at.radio(key="nav")
    assert list(radio.options) == ["New Research", "Research Results", "About"]
    assert radio.value == "New Research"
    assert "regional Healthcare Services company" in blob(at)
    assert r"\$200M" in blob(at)
    assert at.button(key="use_example").label == "Use this example"
    assert at.chat_input(key="research_chat") is not None
    assert "No saved runs yet" in blob(at)


def test_about_names_the_model_and_hides_the_key(folders, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-secret-should-not-appear")
    monkeypatch.setattr(config, "LLM_MODEL", "fake/model")
    at = start()
    at.radio(key="nav").set_value("About").run()
    text = blob(at)
    assert not at.exception
    assert "fake/model" in text
    assert "sk-secret-should-not-appear" not in text
    assert "README.md" in text and "docs/architecture.md" in text
    assert "never calculates" in text


def test_a_request_the_parser_cannot_read_shows_its_message(folders, monkeypatch):
    async def reject(text, use_cache=True, progress=None):
        raise RequestError("Could not read a target from the request: not a company.")

    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(engine, "parse_target", reject)
    at = start()
    at.button(key="use_example").click().run()
    assert not at.exception
    assert "Could not read a target from the request: not a company." in errors(at)
    assert "test-key" not in errors(at)


def test_missing_key_explains_how_to_add_one(folders, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    at = start()
    at.button(key="use_example").click().run()
    assert not at.exception
    assert "OPENROUTER_API_KEY is not set. Copy .env.example to .env" in errors(at)
    assert not config.RUNS_DIR.exists()


def test_missing_processed_data_says_run_make_data(folders, monkeypatch):
    monkeypatch.setattr(config, "TRANSACTIONS_CSV", folders / "transactions.csv")
    at = start()
    assert not at.exception
    assert "transactions.csv" in errors(at) and "make data" in errors(at)
    assert list(at.radio(key="nav").options) == ["New Research", "Research Results", "About"]


def test_example_request_shows_the_target_then_results_and_a_followup(folders, monkeypatch):
    fake_engine(monkeypatch)
    at = start(timeout=180)
    at.button(key="use_example").click().run(timeout=180)
    assert not at.exception, at.exception
    text = blob(at)
    assert "I understood:" in text
    assert "Healthcare Services" in text
    assert "Defaulted fields: ownership" in text
    assert "MCP server: 7 tools" in text
    table = next(frame.value for frame in at.dataframe if "Buyer" in frame.value.columns)
    assert "Blackstone" in table["Buyer"].tolist()
    assert list(table.columns)[:4] == ["Rank", "Buyer", "Type", "Conviction"]
    labels = [item.label for item in at.expander]
    assert any(label == "Agent Activity" for label in labels)
    assert any(item.label.endswith("finished") for item in at.status)
    assert any(item.value.startswith("## 1. Blackstone") for item in at.markdown)
    money = r"\$200.0M (between \$100.0M and \$400.0M)"
    assert any(money in item.value for item in at.markdown)
    assert not any(
        "$200.0M (between $100.0M and $400.0M)" in item.value.replace(r"\$", "")
        and money not in item.value
        for item in at.markdown
    )
    labels = [item.label for item in at.expander]
    assert any(":blue-badge[LLM]" in label and "R1" in label and "Blackstone" in label
               for label in labels)
    assert any(":green-badge[MCP tool]" in label for label in labels)
    assert not any("round —" in label for label in labels)
    assert any(item.value.startswith("**Question:**") and "regional Healthcare Services" in item.value
               for item in at.markdown)
    assert any(list(frame.value.columns[:2]) == ["Signal", "Value"] for frame in at.dataframe)
    assert at.button(key="exclude-blackstone").label == "Not relevant"

    at.button(key="followup-ex-0").click().run(timeout=180)
    assert not at.exception, at.exception
    text = blob(at)
    assert "**Status:** Verified" in text or "**Status:** Not verified" in text
    assert "Verified" in text
    assert any(item.label == "Follow-up activity" for item in at.expander)


def test_mcp_server_failure_has_its_own_message(folders, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(engine, "LLMClient", lambda: AppLLM())
    monkeypatch.setattr(mcp_client, "SERVER_ARGS", ["-c", "raise SystemExit('no data')"])
    at = start(timeout=90)
    at.button(key="use_example").click().run(timeout=90)
    assert not at.exception, at.exception
    assert "The MCP server failed to start" in errors(at)
    assert "test-key" not in errors(at)
    assert "I understood:" in blob(at)


def test_saved_run_renders_without_a_key(folders, monkeypatch):
    def write(messages):
        if buyer_key(messages) == "nordic capital":
            return bad_draft(messages)
        return good_draft(messages)

    fake_engine(monkeypatch, FakeLLM(write=write, committee=committee_policy({})))
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")

    at = start(timeout=60)
    opened = next(button for button in at.button if button.label.startswith("Latest run"))
    opened.click().run()
    assert not at.exception, at.exception
    text = blob(at)
    assert "Research Results" in text
    assert f"Run {run['run_id']}" in text
    assert f"Saved run {run['run_id']}" not in text
    assert any(item.value.startswith("**Question:**") and "Healthcare Services" in item.value
               for item in at.markdown)
    table = next(frame.value for frame in at.dataframe if "Buyer" in frame.value.columns)
    assert "Nordic Capital" in table["Buyer"].tolist()
    assert "Not verified" in table["Status"].tolist()
    downloads = [button.label for button in at.download_button]
    assert "Download reports.md" in downloads
    assert "Download reports.json" in downloads
    assert "Download trace.json" in downloads

    nordic_report = next(report for report in run["reports"]
                         if report.display_name == "Nordic Capital")
    picker = at.selectbox(key=f"buyer-{run['run_id']}")
    nordic = next(option for option in picker.options if "Nordic Capital" in option)
    picker.set_value(nordic).run()
    assert not at.exception, at.exception
    assert any("**Not verified.**" in element.value for element in at.error)
    assert any(list(frame.value.columns[:2]) == ["Signal", "Value"] for frame in at.dataframe)
    cited = nordic_report.draft.precedent_activity[0].source_ids[0]
    assert any(
        cited in frame.value["transaction_id"].tolist()
        for frame in at.dataframe if "transaction_id" in getattr(frame.value, "columns", [])
    )
