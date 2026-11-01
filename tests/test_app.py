"""Streamlit app tests with streamlit.testing.v1.AppTest. No real LLM.

The app runs in this process, so settings and the engine's clients are replaced with fakes.
A full mocked run uses the same scripted model as tests/test_agent.py, plus request parsing
and follow-up answers.
"""

import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from test_activity import deal_answer
from test_agent import FakeLLM, bad_draft, buyer_key, committee_policy, good_draft, research_round

from ma_engine import config
from ma_engine.research import engine
from ma_engine.clients import mcp_client
from ma_engine.clients.llm_client import LLMResult, _parse
from ma_engine.clients.mcp_client import MCPClient
from ma_engine.mcp_server.server import mcp as server
from ma_engine.research.agent import RequestError
from ma_engine.models import FollowupAnswer, RequestFields, Target
from ma_engine.ui.activity import (
    PREVIEW_LIMIT,
    as_markdown,
    activity_label,
    live_label,
    merge_steps,
    order_events,
    preview_json,
    step_title,
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


@pytest.fixture(autouse=True)
def wait_for_background_answers(monkeypatch):
    """Existing output assertions wait for completion, as the browser poller does.

    Dedicated test_jobs tests keep workers blocked to check refresh while running.
    """
    from ma_engine.research import jobs
    original = AppTest._run
    def run_and_poll(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        token = self.query_params.get('job')
        if isinstance(token, list):
            token = token[0]
        if token and token in jobs._jobs:
            jobs._jobs[token]['thread'].join(180)
            assert not jobs._jobs[token]['thread'].is_alive()
            result = original(self, timeout=kwargs.get("timeout"))
        return result
    monkeypatch.setattr(AppTest, '_run', run_and_poll)


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
                   use_cache=True, max_tokens=None) -> LLMResult:
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


def panel_shown(at: AppTest) -> bool:
    return any(tab.label == "Transaction Evidence" for tab in at.tabs)


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


def test_step_title_names_the_kind_status_and_reuse_without_claiming_verification():
    plain = step_title({"step": 1, "category": "app", "name": "run", "acquirer_key": None,
                        "round": None, "status": "completed", "duration_ms": 0}, {})
    assert plain == "Start run · App · Completed · 0 ms"

    research = step_title({
        "step": 5, "category": "llm", "name": "research", "acquirer_key": "blackstone",
        "round": 1, "status": "completed", "duration_ms": 12,
        "output_summary": {"cache_hit": True}}, {"blackstone": "Blackstone"})
    assert research == "Choose tools · LLM · Completed · Blackstone · round 1 · 12 ms · cached response"
    assert "Verified" not in research

    reused = step_title({
        "step": 6, "category": "mcp_tool", "name": "get_acquirer_deals", "acquirer_key": "kkr",
        "round": 2, "status": "completed", "duration_ms": 0,
        "output_summary": {"rows": 3, "repeated": True}}, {})
    assert reused.startswith("get_acquirer_deals · MCP · Completed · kkr · round 2")
    assert reused.endswith("reused result")

    failed = step_title({"step": 7, "category": "mcp_tool", "name": "get_acquirer_deals",
                         "status": "failed", "duration_ms": None}, {})
    assert failed == "get_acquirer_deals · MCP · :red[Failed]"

    running = {"step": 8, "category": "validation", "name": "check", "status": "running"}
    assert step_title(running, live=True) == "Check evidence · Validation · Running"
    assert step_title(running) == "Check evidence · Validation · :orange[Not finished]", (
        "a step left running when the activity ended is never shown as completed")


def test_preview_json_does_not_keep_a_hundred_thousand_character_output():
    blob_text = "x" * 100_000
    preview, full, truncated = preview_json({"payload": blob_text})
    assert truncated is True
    assert len(preview) == PREVIEW_LIMIT
    assert blob_text not in preview
    assert blob_text in full
    assert len(full) > 100_000


def test_running_and_completed_events_merge_and_rounds_stay_with_the_buyer():
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
        {"step": 4, "name": "get_acquirer_deals", "category": "mcp_tool", "status": "running",
         "acquirer_key": "blackstone", "round": 2, "output": None},
        {"step": 5, "name": "save", "category": "app", "status": "completed", "acquirer_key": None,
         "round": None, "output": huge},
    ]
    merged = merge_steps(events)
    assert [event["step"] for event in merged] == [1, 2, 3, 4, 5], "one row per step"
    assert merged[1]["status"] == "completed"
    assert live_label(events, {"blackstone": "Blackstone"}) == (
        "MCP · get_acquirer_deals · Blackstone")
    assert live_label(events[:1]) == ""
    assert live_label([{"step": 1, "category": "llm", "name": "committee",
                        "status": "running", "input": {"model": "provider/model"}}]) == "LLM · provider/model"
    ordered = order_events(events)
    buyers = [event.get("acquirer_key") for event in ordered]
    assert buyers[0] is None and buyers[-1] is None
    blackstone = [event["step"] for event in ordered if event.get("acquirer_key") == "blackstone"]
    assert blackstone == [2, 4]


def test_activity_label_is_status_and_recorded_wall_time_only():
    assert activity_label({"wall_time_s": 9.8}) == "Agent activity · Completed · 9.8s"
    assert activity_label({"wall_time_s": 4.0}) == "Agent activity · Completed · 4.0s"
    assert activity_label(None, failed=True) == "Agent activity · :red[Failed]"


# ---------- report html (no Streamlit script) ----------

def _html_of(*parts: str) -> str:
    return "\n".join(parts)


def test_stage_rows_sum_recorded_durations_and_leave_later_stages_pending():
    from ma_engine.ui.reports import stage_rows

    rows = {row["id"]: row for row in stage_rows([
        {"step": 1, "category": "calculation", "name": "ranking", "status": "completed",
         "duration_ms": 200},
        {"step": 2, "category": "mcp_tool", "name": "rank_acquirers", "status": "completed",
         "duration_ms": 68},
        {"step": 3, "category": "mcp_tool", "name": "get_acquirer_profile", "status": "running",
         "duration_ms": None},
    ])}
    assert rows["identify"]["status"] == "completed"
    assert rows["identify"]["duration"] == "268 ms"
    assert rows["identify"]["label"] == "Identify relevant sectors and buyers"
    assert rows["investigate"]["status"] == "running"
    assert rows["comps"]["status"] == "pending"
    assert rows["reports"]["status"] == "pending"
    assert rows["reports"]["label"] == "Generate and verify buyer reports"
    failed = stage_rows([
        {"step": 1, "category": "llm", "name": "write", "status": "completed", "duration_ms": 5},
        {"step": 2, "category": "app", "name": "buyer_failed", "acquirer_key": "gtcr",
         "status": "failed", "duration_ms": 0},
    ])
    assert failed[3]["status"] == "failed"


def _activity_script():
    import streamlit as st
    from ma_engine.ui.activity import render_timeline

    render_timeline(st.session_state.events, key_prefix=st.session_state.prefix,
                    names={"gtcr": "GTCR"}, scrub=lambda text: text.replace("sk-secret", "***"))


def _activity_app(events, prefix="run-x"):
    at = AppTest.from_function(_activity_script)
    at.session_state.events = events
    at.session_state.prefix = prefix
    return at.run()


def test_step_rows_show_parameters_output_issues_errors_and_redact_every_branch():
    events = [
        {"step": 1, "category": "mcp_tool", "name": "rank_acquirers", "status": "completed",
         "duration_ms": 68, "input": {"sector": "Healthcare Services", "token": "sk-secret"},
         "output_summary": {"rows": 15}, "output": {"data": {"note": "sk-secret"}}},
        {"step": 2, "category": "validation", "name": "check", "acquirer_key": "gtcr",
         "status": "completed", "duration_ms": 9, "output_summary": {"issue_count": 1},
         "output": ["risk_flags[0]: cites MA-2018-0338 (Rumored)"]},
        {"step": 3, "category": "llm", "name": "repair", "acquirer_key": "gtcr",
         "status": "failed", "duration_ms": 40, "error": "ReportError: sk-secret rejected",
         "output": "repaired draft", "attempts": [{"n": 1}, {"n": 2}],
         "tokens_in": 10, "cost_usd": 0.001},
        {"step": 4, "category": "mcp_tool", "name": "get_acquirer_deals", "acquirer_key": "gtcr",
         "status": "completed", "duration_ms": 1, "input": {"acquirer_key": "gtcr"},
         "output_summary": {"repeated": True}, "output": None},
        {"step": 5, "category": "mcp_tool", "name": "get_acquirer_profile", "status": "completed",
         "duration_ms": 1, "input": {}, "output": {"blob": "z" * (PREVIEW_LIMIT + 10) + "sk-secret"}},
        {"step": 6, "category": "app", "name": "run", "status": "completed", "duration_ms": 0,
         "input": {"sector": "Healthcare Services"}, "output": None, "decision": "Started the run"},
        {"step": 7, "category": "llm", "name": "write", "status": "completed", "duration_ms": 5,
         "decision": "Wrote the draft", "output": "the report"},
    ]
    at = _activity_app(events)
    assert not at.exception, at.exception
    codes = [code.value for code in at.code]
    texts = codes + [item.value for item in [*at.markdown, *at.caption, *at.error]]
    assert not any("sk-secret" in text for text in texts), "inputs, outputs and errors are redacted"
    assert any('"sector": "Healthcare Services"' in code for code in codes)
    assert any('"note": "***"' in code for code in codes), "small output shows the redacted copy"
    assert any("- risk_flags[0]: cites MA-2018-0338 (Rumored)" in item.value for item in at.markdown)
    assert any("ReportError: *** rejected" in item.value for item in at.error)
    assert any(item.value == "Attempts: 2" for item in at.caption)
    assert any(item.value == "No output recorded." for item in at.caption)
    assert any(item.value.startswith("Reused result:") for item in at.caption)
    assert any(item.value.startswith("Preview truncated: the first 20,000 of") for item in at.caption)
    assert max(len(code) for code in codes) <= PREVIEW_LIMIT
    assert len(at.download_button) == 1, "only the large output gets a download"
    labels = [item.label for item in at.expander]
    assert labels.count("Raw tool response") == 2, "only calls that recorded an output"
    assert "Issues found" in labels and "Visible text preview" in labels
    assert labels[0].startswith("rank_acquirers · MCP · Completed")
    assert not any(label.startswith("Start run") or label.startswith("Write report") for label in labels)
    assert not any(label == "Recorded output" for label in labels)
    assert sum(item.value == "No output recorded." for item in at.caption) == 1
    for group in at.button_group:
        group.set_value("All steps")
    at = at.run()
    assert not at.exception, at.exception
    labels = [item.label for item in at.expander]
    assert any(label.startswith("Start run · App · Completed") for label in labels)
    assert any(label.startswith("Write report · LLM · Completed") for label in labels)
    assert not any(label == "Recorded output" for label in labels), "a start event has no output box"
    assert sum(item.value == "No output recorded." for item in at.caption) == 1


def test_two_responses_with_the_same_step_numbers_keep_separate_rows():
    first = [{"step": 1, "category": "llm", "name": "followup", "status": "completed",
              "duration_ms": 5, "decision": "First answer"}]
    second = [{"step": 1, "category": "llm", "name": "followup", "status": "completed",
               "duration_ms": 7, "decision": "Second answer"}]

    def script():
        import streamlit as st
        from ma_engine.ui.activity import render_timeline

        for prefix, events in zip(st.session_state.prefixes, st.session_state.histories):
            render_timeline(events, key_prefix=prefix)

    at = AppTest.from_function(script)
    at.session_state.histories = [first, second]
    at.session_state.prefixes = ["fu-run-0", "fu-run-1"]
    at.run()
    assert not at.exception, at.exception
    assert not any(item.value.startswith("**Result:**") for item in at.markdown), (
        "a finished LLM step stays behind All steps")
    for group in at.button_group:
        group.set_value("All steps")
    at = at.run()
    assert not at.exception, at.exception
    results = [item.value for item in at.markdown if item.value.startswith("**Result:**")]
    assert results == ["**Result:** First answer", "**Result:** Second answer"]
    clash = AppTest.from_function(script)
    clash.session_state.histories = [first, second]
    clash.session_state.prefixes = ["fu-run", "fu-run"]
    clash.run()
    assert clash.exception, "step number alone is not an identity; the prefix must differ"


def test_real_run_html_lists_every_buyer_and_every_citation_opens_its_source():
    """The measurement run. Skipped only when that folder is not on disk."""
    from ma_engine.ui.reports import (
        buyer_block_html, esc, executive_summary_html, metric_items, metrics_strip_html,
        source_index,
    )
    from ma_engine.clients.data_client import get_client

    run_dir = config.ROOT / "outputs" / "runs" / "20261031-222706"
    if not (run_dir / "reports.json").is_file():
        pytest.skip("measurement run is not in the workspace")
    run = engine.load_run(run_dir)
    sources = source_index(run)
    reports = sorted(run["reports"], key=lambda report: report.rank)
    html = _html_of(
        executive_summary_html(run, sources, 500),
        *(buyer_block_html(report, sources) for report in reports),
    )
    assert {int(rank) for rank in re.findall(r'id="buyer-(\d+)"', html)} == set(range(1, 11))
    assert_citations(html, run)
    written = next(report for report in reports if report.draft is not None)
    block = buyer_block_html(written, sources)
    for title in ("Acquirer Overview", "Strategic Fit Thesis", "Precedent Activity",
                  "Valuation Context", "Risk Flags", "Conviction"):
        assert f"<h3>{title}</h3>" in block
    failed = next(report for report in reports if report.status == "failed")
    assert failed.draft is None
    assert f'id="buyer-{failed.rank}"' in html
    failed_html = buyer_block_html(failed, sources)
    assert "Failed" in failed_html and "Verification details" in failed_html
    assert "see Diagnostics" not in html
    assert failed.error and esc(failed.error) in failed_html
    deal = next(entry for entry in run["sources"]["entries"] if entry["kind"] == "deal")
    view = _html_of(cite_view(html, deal["id"]))
    for field in ("transaction_id", "acquirer", "target_company", "sector", "outcome"):
        assert str(deal["record"][field]) in view, field
    stat = next(entry for entry in run["sources"]["entries"] if entry["kind"] == "statistic"
                and len(entry["record"]["source_ids"]) > 5)
    from ma_engine.ui.reports import cite_html
    view = cite_html(stat, sources)
    assert stat["record"]["display"] in view and f"n = {stat['record']['n']}" in view
    assert f"Show all {len(stat['record']['source_ids'])} transactions" in view
    assert all(source_id in view for source_id in stat["record"]["source_ids"])
    assert stat["id"] in view.split("Technical details", 1)[1], "the stat id sits under details"
    metrics = get_client().key_metrics(reports[0].acquirer_key)
    entry = next(buyer for buyer in run["ranking"]["buyers"]
                 if buyer["acquirer_key"] == reports[0].acquirer_key)
    strip = metrics_strip_html(metric_items(entry, metrics, sources), sources)
    assert entry["relevant_closed_count"]["display"] in strip
    assert "median EV/EBITDA" in strip and "largest" not in strip


def test_verification_wording_comes_from_the_saved_checks(monkeypatch):
    from types import SimpleNamespace

    from ma_engine.ui.reports import explain_issue, verification_html

    sample = ('valuation_context[0]: "n=4" is not the sample size of the cited statistic '
              "(stored n=10).")
    number = 'precedent_activity[0]: number "$185M" does not appear in the tool results.'
    unknown = "A brand new check that the page does not recognise."
    assert explain_issue(sample) == "A reported sample size does not match its cited evidence."
    assert "n=10" not in explain_issue(sample)
    assert explain_issue(unknown) == unknown

    one = verification_html(SimpleNamespace(status="not_verified", issues=[sample], error=None))
    assert "A reported sample size does not match its cited evidence." in one
    assert "n=4" in one and "n=10" in one and "Verification details" in one

    many = verification_html(SimpleNamespace(
        status="not_verified", issues=[sample, number, unknown], error=None))
    assert "A reported sample size does not match its cited evidence." in many
    assert "A number in the report does not appear in the tool results." in many
    assert unknown in many
    assert many.count("A reported sample size does not match its cited evidence.") == 1

    same_kind = verification_html(SimpleNamespace(
        status="not_verified",
        issues=[sample, sample.replace("n=4", "n=3")],
        error=None))
    assert same_kind.count("A reported sample size does not match its cited evidence.") == 1
    assert "n=3" in same_kind and "n=4" in same_kind

    assert verification_html(SimpleNamespace(status="verified", issues=[], error=None)) == ""
    failed = verification_html(SimpleNamespace(
        status="failed", issues=[], error="provider error", draft=None))
    assert "provider error" in failed and "Verification details" in failed
    blank = verification_html(SimpleNamespace(status="failed", issues=[], error=None, draft=None))
    assert "Research failed before a report was written." in blank

    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-secret-value")
    hidden = verification_html(SimpleNamespace(
        status="failed", issues=[], error="boom sk-secret-value", draft=None))
    assert "sk-secret-value" not in hidden and "***" in hidden


def cite_view(html: str, source_id: str) -> str:
    """The source card stored for one cited id."""
    start = html.index(f'data-source="{source_id}"')
    return html[start:html.index("</details>", html.index("cite-pop", start))]


# ---------- app ----------

def test_sidebar_size_text_drops_only_a_whole_million_decimal():
    from ma_engine.ui.sessions import sidebar_size_text

    assert sidebar_size_text("$200.0M") == "$200M"
    assert sidebar_size_text("$1,234.0M") == "$1,234M"
    assert sidebar_size_text("$185.6M") == "$185.6M"
    assert sidebar_size_text("") == ""


def test_sidebar_run_text_keeps_two_lines_and_the_full_time(tmp_path):
    from ma_engine.ui.sessions import sidebar_run_text

    folder = tmp_path / "20261101-014500"
    folder.mkdir()
    (folder / "reports.json").write_text(json.dumps({
        "target": {"sector": "Very Long Healthcare Services Name For The Sidebar", "ev_mm": 200.0}
    }), encoding="utf-8")
    label, hover = sidebar_run_text(folder)
    title = "Very Long Healthcare Services Name For The Sidebar buyer research"
    assert label.startswith(f"**{title}**")
    assert "$200M" in label and "$200.0M" not in label
    assert ":small[" not in label
    assert "\n" in label
    assert title in hover
    assert "$200M" in hover and "01\\:45" in hover
    assert sidebar_run_text(folder) == (label, hover)


def test_sidebar_run_text_uses_the_saved_title(tmp_path):
    from ma_engine.ui.sessions import sidebar_run_text

    folder = tmp_path / "20261101-014500"
    folder.mkdir()
    (folder / "reports.json").write_text(json.dumps({
        "target": {"sector": "Healthcare Services", "ev_mm": 200.0},
        "research_title": "Regional healthcare buyers",
    }), encoding="utf-8")
    label, hover = sidebar_run_text(folder)
    assert label.startswith("**Regional healthcare buyers**")
    assert "Healthcare Services" not in label
    assert "$200M" in label and "\n" in label
    assert "Regional healthcare buyers" in hover and "01\\:45" in hover


def test_app_loads_with_the_research_sidebar(folders):
    at = start()
    assert not at.exception
    text = blob(at)
    assert 'product-title">M&amp;A Research' in text
    assert 'product-sub">Assistant' in text
    assert "M&amp;A Research<br>Assistant" not in text
    assert not panel_shown(at), "the research panel is hidden until a buyer is clicked"
    assert at.button(key="nav-new").label == "New Research"
    assert at.button(key="nav-about").label == "About"
    assert "regional Healthcare Services company" in text
    assert r"\$200M" in text
    assert at.button(key="use_example").label == "Use this example"
    assert at.chat_input(key="research_chat") is not None
    assert at.button(key="nav-history").label == "History"
    assert not [button for button in at.button if button.key in ("nav-saved", "view-details")]
    at.button(key="nav-history").click().run()
    assert "No saved runs yet" in blob(at)


def test_about_names_the_model_and_hides_the_key(folders, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-secret-should-not-appear")
    monkeypatch.setattr(config, "LLM_MODEL", "fake/model")
    at = start()
    at.button(key="nav-about").click().run()
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


def test_a_parsed_title_is_handed_to_the_run_and_not_to_the_target(folders, monkeypatch):
    captured = {}

    async def parse(text, use_cache=True, progress=None):
        return Target.default(), ["ownership"], "Regional healthcare buyers"

    async def run(target, **kwargs):
        captured["target"] = target.model_dump()
        captured["kwargs"] = kwargs
        raise RuntimeError("stop after the handoff")

    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(engine, "parse_target", parse)
    monkeypatch.setattr(engine, "run_engine", run)
    at = start()
    at.button(key="use_example").click().run()
    assert captured["kwargs"]["research_title"] == "Regional healthcare buyers"
    assert "title" not in captured["target"]
    assert "research_title" not in captured["target"]
    assert "stop after the handoff" in errors(at)


def test_missing_key_explains_how_to_add_one(folders, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    at = start()
    at.button(key="use_example").click().run()
    assert not at.exception
    assert "OPENROUTER_API_KEY is not set. Copy .env.example to .env" in errors(at)
    assert not list(config.RUNS_DIR.glob("*/reports.json"))
    from ma_engine.persistence import submissions
    assert "Find the 10 most likely acquirers" in submissions.unfinished()[0]["question"]


def test_missing_processed_data_says_run_make_data(folders, monkeypatch):
    monkeypatch.setattr(config, "TRANSACTIONS_CSV", folders / "transactions.csv")
    at = start()
    assert not at.exception
    assert "transactions.csv" in errors(at) and "make data" in errors(at)
    assert at.button(key="nav-new").label == "New Research"
    assert at.button(key="nav-about").label == "About"


def page_html(at: AppTest) -> str:
    return "\n".join(element.value for element in at.markdown)


def assert_citations(html: str, run: dict) -> None:
    """Every [n] names a source in the run's registry, and its card shows that number."""
    registry = {entry["id"]: entry for entry in run["sources"]["entries"]}
    cited = re.findall(r'data-source="([^"]+)"', html)
    assert cited, "expected clickable citations"
    assert set(cited) <= set(registry), set(cited) - set(registry)
    for source_id in set(cited):
        number = registry[source_id]["number"]
        assert f">[{number}]</summary>" in html
    assert 'href="#src-' not in html, "no jump links to a sources list"


def test_example_request_shows_the_target_then_results_and_a_followup(folders, monkeypatch):
    counts = {"parse": 0}

    class Counting(AppLLM):
        async def chat(self, messages, tools=None, tool_choice=None, response_schema=None,
                       use_cache=True, max_tokens=None):
            if response_schema is RequestFields:
                counts["parse"] += 1
            return await super().chat(
                messages, tools, tool_choice, response_schema, use_cache, max_tokens)

    fake_engine(monkeypatch, Counting())
    at = start(timeout=180)
    at.button(key="use_example").click().run(timeout=180)
    assert not at.exception, at.exception
    text = blob(at)
    assert "I understood:" not in text
    assert "Healthcare Services" in text
    assert "Not stated in the request, so assumed:" not in text
    assert at.button(key="edit-target").label == "Edit"
    assert next(e for e in at.expander if e.label.startswith("Agent activity")).proto.expanded is False
    at.session_state[f"run-{at.session_state.current_run['run_id']}-activity"] = True
    at.run()
    text = blob(at)
    assert "Identify relevant sectors and buyers" in text
    assert "Generate and verify buyer reports" in text
    assert "Research Progress" not in text
    order = [item.value for item in at.markdown]
    activity = [item for item in at.expander if item.label.startswith("Agent activity")]
    assert len(activity) == 1, "one activity row for the research answer"
    assert re.fullmatch(r"Agent activity · Completed · [\d.]+s", activity[0].label), activity[0].label
    assert activity[0].proto.expanded is True
    assert not any(item.label in ("View activity", "Follow-up activity", "Recorded steps")
                   for item in at.expander)
    assert not at.status, "the live status is gone once the research has finished"
    assert any(item.label.startswith("rank_acquirers · MCP · Completed") for item in at.expander)
    assert any('"sector": "Healthcare Services"' in code.value for code in at.code)
    stage_at = next(i for i, value in enumerate(order) if "**Result:**" in value)
    answer_at = next(i for i, value in enumerate(order) if "<h3>Executive Summary</h3>" in value)
    top10_at = next(i for i, value in enumerate(order) if "Top 10 Potential Acquirers" in value)
    assert stage_at < answer_at < top10_at, "completion, then executive summary, then shortlist"
    run = at.session_state.current_run
    assert counts["parse"] == 1
    assert run["research_title"] == "Healthcare Services buyer research"
    assert "research_title" not in run["target"].model_dump()
    reports_file = Path(run["run_dir"]) / "reports.json"
    assert json.loads(reports_file.read_text())["research_title"] == run["research_title"]
    rank_inputs = [event["input"] for event in run["trace"]["events"]
                   if event.get("name") == "rank_acquirers" and event.get("input")]
    assert rank_inputs and all("research_title" not in item for item in rank_inputs)
    executive = order[answer_at]
    assert "ranking is not a probability of an offer" in executive
    verified = sum(report.status == "verified" for report in run["reports"])
    assert "automated checks passed" in executive
    assert sum("<h3>Executive Summary</h3>" in value for value in order) == 1
    assert "full shortlist is below" not in text and "Source appendix" not in text
    assert any(item.label == "Run-wide totals" for item in at.expander)
    summaries = [report.draft.summary.text for report in run["reports"] if report.draft]
    shown = [text_ for text_ in summaries if any(text_ in value for value in order)]
    assert len(shown) <= 1, "no per-buyer paragraphs before the shortlist"
    assert not any('id="buyer-' in value for value in order), "no full reports in the chat"
    from ma_engine.ui.reports import conviction_level, status_text
    cards = [button for button in at.button if button.key.startswith(f"top-{run['run_id']}-")]
    assert len(cards) == len(run["reports"]), "one card button per buyer"
    for card, report in zip(cards, sorted(run["reports"], key=lambda item: item.rank)):
        first, second, evidence = card.label.split("  \n")
        assert "direct-sector deal" in evidence
        assert first.startswith(f"**{report.rank}. ") and report.acquirer_type in second
        assert conviction_level(report) in second and status_text(report.status) in second
    assert not panel_shown(at), "the report panel opens only after a click"
    at.button(key="open-full-report").click().run()
    assert not at.exception, at.exception
    text = blob(at)
    html = page_html(at)
    assert {int(rank) for rank in re.findall(r'id="buyer-(\d+)"', html)} == set(range(1, 11))
    assert_citations(html, at.session_state.current_run)
    assert "Source appendix" not in html and 'class="shortlist"' in html
    assert "Blackstone" in text
    tab_labels = {tab.label for tab in at.tabs}
    assert tab_labels == {"Report", "Transaction Evidence", "Valuation & Risk", "Score Breakdown"}
    money_md = r"\$200.0M (between \$100.0M and \$400.0M)"
    money_html = "&#36;200.0M (between &#36;100.0M and &#36;400.0M)"
    raw_money = "$200.0M (between $100.0M and $400.0M)"
    assert any(money_md in item.value or money_html in item.value for item in at.markdown)
    assert not any(raw_money in item.value for item in at.markdown)
    labels = [item.label for item in at.expander]
    assert any(" · MCP · Completed" in label for label in labels)
    assert any(label.startswith("Retrieve valuation comparisons") for label in labels)
    assert "Technical event log" in labels
    assert "Research stages" not in labels
    assert any(label.startswith("Choose tools · LLM · Completed · Blackstone · round 1")
               for label in labels)
    assert not any(item.value.startswith("**Question:**") for item in at.markdown)
    top = next(button for button in at.button
               if button.key and button.key.startswith("top-") and button.label.split("  \n")[0].endswith("Blackstone**"))
    at = top.click().run()
    assert not at.exception, at.exception
    panel_html = page_html(at)
    assert not re.search(r'id="buyer-\d+"', panel_html), "one buyer only, not the full report"
    assert "Source appendix" not in panel_html
    assert panel_html.count("<h3>Executive Summary</h3>") == 1, "summary stays in the chat only"
    assert not any(tab.label == "1. Overview" for tab in at.tabs), "one tab row only"
    report_html = page_html(at)
    for title in ("Acquirer Overview", "Strategic Fit Thesis", "Precedent Activity",
                  "Valuation Context", "Risk Flags", "Conviction"):
        assert f"<h3>{title}</h3>" in report_html, title
    assert 'class="metric-strip"' in report_html
    assert any(list(frame.value.columns[:2]) == ["Signal", "Value"] for frame in at.dataframe)
    assert not any(list(frame.value.columns[:2]) == ["Number", "Source"] for frame in at.dataframe)
    assert "All buyers in one report" not in blob(at)
    assert "Not relevant" not in blob(at)
    run_id = at.session_state.current_run["run_id"]
    previous = at.button(key=f"prev-buyer-{run_id}")
    nxt = at.button(key=f"next-buyer-{run_id}")
    assert previous.label == "Previous" and previous.disabled
    assert nxt.label == "Next" and not nxt.disabled
    assert "Point at or tap a bracketed number" not in blob(at)

    at.chat_input(key="followup_chat").set_value("Why was the top buyer ranked first?").run(timeout=180)
    assert not at.exception, at.exception
    text = blob(at)
    assert "**Status:** Verified" in text or "**Status:** Not verified" in text
    assert "Verified" in text
    activity = [item.label for item in [*at.expander, *at.status]
                if item.label.startswith("Agent activity")]
    assert len(activity) == 2, activity
    assert all(label.startswith("Agent activity · Completed") for label in activity), activity
    at.run()
    assert not at.exception, at.exception
    saved = [item.label for item in at.expander if item.label.startswith("Agent activity")]
    assert len(saved) == 2 and not at.status, "after a rerun both rows are drawn from saved events"
    assert not any(item.label == "Follow-up activity" for item in at.expander)
    at.session_state[f"run-{at.session_state.current_run['run_id']}-activity"] = True
    at.run()
    assert sum(item.label == "Run-wide totals" for item in at.expander) == 1
    assert counts["parse"] == 1
    assert json.loads(reports_file.read_text())["research_title"] == "Healthcare Services buyer research"
    assert at.session_state.current_run["research_title"] == "Healthcare Services buyer research"


def test_edit_target_saves_without_running_until_asked(folders, monkeypatch):
    fake_engine(monkeypatch)
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    started = []
    seen = []

    async def record(target, **kwargs):
        started.append(target)
        seen.append(kwargs)
        return run

    monkeypatch.setattr(engine, "run_engine", record)
    at = start(timeout=60)
    at.button(key="nav-history").click().run()
    at.button(key=f"open-{run['run_id']}").click().run()
    assert not at.exception, at.exception
    assert not at.number_input, "the form is closed until Edit target is clicked"

    at.button(key="edit-target").click().run()
    assert at.number_input[0].value == 200.0, "the form opens with the current values"
    next(button for button in at.button if button.label == "Cancel").click().run()
    assert not at.number_input and "Updated target" not in blob(at)

    at.button(key="edit-target").click().run()
    at.number_input[0].set_value(500.0)
    at = next(button for button in at.button if button.label == "Save").click().run()
    assert not at.exception, at.exception
    text = blob(at)
    assert "Updated target:" in text and "$500.0M" in text.replace("&#36;", "$").replace("\\$", "$")
    assert any(item.label.startswith("Agent activity · Completed") for item in at.expander), (
        "old results stay")
    assert started == [], "Save does not start a run"

    at.button(key="discard-updated-target").click().run()
    assert "Updated target:" not in blob(at)
    at.button(key="edit-target").click().run()
    at.number_input[0].set_value(500.0)
    at = next(button for button in at.button if button.label == "Save").click().run()
    at.button(key="run-updated-target").click().run(timeout=60)
    assert not at.exception, at.exception
    assert len(started) == 1 and started[0].ev_mm == 500.0
    assert seen[0].get("research_title") is None


def test_mcp_server_failure_has_its_own_message(folders, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(engine, "LLMClient", lambda: AppLLM())
    monkeypatch.setattr(mcp_client, "SERVER_ARGS", ["-c", "raise SystemExit('no data')"])
    at = start(timeout=90)
    at.button(key="use_example").click().run(timeout=90)
    assert not at.exception, at.exception
    assert "The MCP server failed to start" in errors(at)
    assert "test-key" not in errors(at)
    assert "Not stated in the request, so assumed:" in blob(at)


def test_saved_run_renders_without_a_key_and_switching_buyers_calls_no_engine(
        folders, monkeypatch):
    def write(messages):
        if buyer_key(messages) == "nordic capital":
            return bad_draft(messages)
        return good_draft(messages)

    def explode(*_args, **_kwargs):
        raise AssertionError("engine should not be called while switching buyers")

    fake_engine(monkeypatch, FakeLLM(write=write, committee=committee_policy({}),
                                      fail_keys={"gtcr"}))
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    failed = next(report for report in run["reports"] if report.status == "failed")
    assert failed.draft is None
    assert failed.acquirer_key == "gtcr"

    at = start(timeout=60)
    at.button(key="nav-history").click().run()
    assert "Healthcare Services · \\$200.0M" in blob(at)
    assert run["run_id"] not in blob(at), "runs are listed by readable names"
    at.button(key=f"open-{run['run_id']}").click().run()
    assert not at.exception, at.exception
    assert not panel_shown(at), "the report panel opens only after a click"
    answer = page_html(at)
    assert_citations(answer, at.session_state.current_run)
    entries = json.loads((Path(run["run_dir"]) / "sources.json").read_text())["entries"]
    first = re.search(r'data-source="([^"]+)"', answer).group(1)
    entry = next(item for item in entries if item["id"] == first)
    assert f"Source [{entry['number']}]" in cite_view(answer, first)
    assert not panel_shown(at), "a citation opens in place, not in the panel"
    at.button(key="open-full-report").click().run()
    assert not at.exception, at.exception
    html = page_html(at)
    assert {int(rank) for rank in re.findall(r'id="buyer-(\d+)"', html)} == set(range(1, 11))
    assert "Source appendix" not in html
    assert_citations(html, at.session_state.current_run)
    exported = (Path(run["run_dir"]) / "reports.md").read_text()
    assert "## Sources" in exported
    cited_id = re.search(r'data-source="([^"]+)"', html).group(1)
    cited_number = next(item["number"] for item in entries if item["id"] == cited_id)
    assert f">[{cited_number}]</summary>" in html and f"[{cited_number}]" in exported
    failed_block = next(item.value for item in at.markdown
                        if f'id="buyer-{failed.rank}"' in item.value)
    assert failed.display_name in failed_block
    assert "Failed" in failed_block
    assert "Verification details" in failed_block
    assert "see Diagnostics" not in failed_block
    assert failed.error in failed_block
    assert not any(item.label == "Diagnostics" for item in at.expander)
    assert any(failed.error[:80] in item.value for item in at.markdown)
    nordic = next(report for report in run["reports"] if report.display_name == "Nordic Capital")
    assert nordic.status == "not_verified"
    assert "> Not verified." in exported
    assert failed.error[:40] in exported
    assert "Not verified" in html
    at.session_state[f"run-{run['run_id']}-activity"] = True
    at.run()
    assert any(nordic.issues[0].split("$")[0] in item.value for item in at.markdown)
    assert "Download report" in [button.label for button in at.download_button]
    assert "Download reports.json" not in [button.label for button in at.download_button]

    at.button(key=f"top-{run['run_id']}-nordic-capital").click().run()
    assert not at.exception, at.exception
    assert "Not verified" in blob(at)
    assert "Verification details" in blob(at)
    assert "A number in the report does not appear in the tool results." in blob(at)
    assert not any("see Diagnostics" in item.value for item in at.warning)
    close = at.button(key="close-panel")
    assert close.label == "Close report"
    assert close.help == "Close report"
    assert any(list(frame.value.columns[:2]) == ["Signal", "Value"] for frame in at.dataframe)
    cited = nordic.draft.precedent_activity[0].source_ids[0]
    assert any(
        cited in frame.value["ID"].tolist()
        for frame in at.dataframe if "ID" in getattr(frame.value, "columns", [])
    )
    assert "of 10" in blob(at)

    monkeypatch.setattr(engine, "load_run", explode)
    monkeypatch.setattr(engine, "run_engine", explode)
    monkeypatch.setattr(engine, "run_followup", explode)
    monkeypatch.setattr(engine, "parse_target", explode)
    monkeypatch.setattr(MCPClient, "call_tool", explode)
    assert {tab.label for tab in at.tabs} == {
        "Report", "Transaction Evidence", "Valuation & Risk", "Score Breakdown"}

    def open_tab(label):
        nonlocal at
        at.session_state["panel_tab"] = label
        at = at.run()
        assert not at.exception, at.exception

    open_tab("Transaction Evidence")
    assert at.session_state["panel_tab"] == "Transaction Evidence"
    assert any("transactions cited" in item.value for item in at.caption)
    open_tab("Valuation & Risk")
    assert at.session_state["panel_tab"] == "Valuation & Risk"
    valuation = page_html(at)
    assert "Valuation Context" in valuation and "Risk Flags" in valuation
    open_tab("Score Breakdown")
    assert at.session_state["panel_tab"] == "Score Breakdown"
    assert any(list(frame.value.columns[:2]) == ["Signal", "Value"] for frame in at.dataframe)
    at.session_state["panel_tab"] = "Agent Activity"
    at = at.run()
    assert not at.exception, at.exception
    assert at.session_state["panel_tab"] == "Report"
    assert "<h3>Acquirer Overview</h3>" in page_html(at)
    at.session_state[f"run-{run['run_id']}-activity"] = True
    at.run()
    steps_for = at.selectbox(key=f"run-{run['run_id']}-buyer")
    at = steps_for.set_value(failed.acquirer_key).run()
    assert not at.exception, at.exception
    assert any(item.label.startswith("Agent activity · Completed") for item in at.expander)
    assert any(item.label == "Run-wide totals" for item in at.expander)
    assert any("whole research run" in item.value for item in at.caption)
    assert any(item.label == "LLM calls" for item in at.metric)
    assert any(failed.display_name in item.label for item in at.expander
               if " · " in item.label and not item.label.startswith("Agent activity"))
    at.button(key=f"next-buyer-{run['run_id']}").click().run()
    assert not at.exception
    at.button(key=f"prev-buyer-{run['run_id']}").click().run()
    assert not at.exception
    at.button(key="open-full-report").click().run()
    assert not at.exception
    assert {int(rank) for rank in re.findall(r'id="buyer-(\d+)"', page_html(at))} == set(range(1, 11))
    at.button(key=f"jump-{run['run_id']}-{failed.acquirer_key}").click().run()
    assert not at.exception
    assert failed.display_name in blob(at)
    assert "Failed" in blob(at)
    at.session_state.current_run["trace"]["substitutions"] = [{
        "replacement": "Buyer A", "failed": "Buyer B", "replacement_shortlist_rank": 3,
    }]
    at.run()
    assert not at.exception, at.exception
    assert any(item.label == "Buyer substitutions" for item in at.expander)
    assert "Buyer A replaced Buyer B (shortlist rank 3)." in blob(at)
    at.button(key="close-panel").click().run()
    assert not at.exception
    assert not panel_shown(at)

    at.button(key="open-full-report").click().run()
    assert not at.exception
    downloads = [button.label for button in at.download_button]
    assert "Download report" in downloads
    assert not {"Download reports.json", "Download sources.json", "Download trace.json"} & set(downloads)


def _tool_events(question: str, status: str) -> list[dict]:
    opened = {"step": 1, "category": "mcp_tool", "name": "get_acquirer_deals",
              "acquirer_key": None, "round": 1, "status": "running",
              "input": {"acquirer_key": "blackstone", "note": question}}
    if status == "running":
        return [opened]
    return [opened, {**opened, "status": "completed", "duration_ms": 3,
                     "output": {"data": {"question": question}, "source_ids": [], "warnings": []},
                     "output_summary": {"rows": 0, "repeated": False}}]


def test_each_followup_keeps_its_own_activity_and_a_failed_one_keeps_its_partial_steps(
        folders, monkeypatch):
    fake_engine(monkeypatch)
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    calls = []

    async def fake_followup(question, run, use_cache=True, progress=None, submission_id=None):
        calls.append(question)
        if question == "boom":
            for event in _tool_events(question, "running"):
                progress(event)
            raise RuntimeError("tool server went away")
        events = _tool_events(question, "completed")
        for event in events:
            progress(event)
        path = Path(run["run_dir"]) / "followups.json"
        items = json.loads(path.read_text()) if path.is_file() else []
        items.append({"question": question, "statements": [], "status": "verified", "issues": [],
                      "markdown": f"Answer to {question}", "events": events,
                      "totals": {"wall_time_s": 1.5, "llm_calls": 2, "mcp_tool_calls": 0,
                                 "tokens_in": 100, "tokens_out": 20, "cost_usd": 0.007157,
                                 "cache_hits": 0}})
        path.write_text(json.dumps(items))

    monkeypatch.setattr(engine, "run_followup", fake_followup)
    at = start(timeout=60)
    at.button(key="nav-history").click().run()
    at.button(key=f"open-{run['run_id']}").click().run()
    for question in ("first?", "second?", "boom"):
        at.chat_input(key="followup_chat").set_value(question).run(timeout=60)
        assert not at.exception, at.exception
    assert calls == ["first?", "second?", "boom"]
    at.run()
    assert not at.exception, at.exception
    assert calls == ["first?", "second?", "boom"], "redrawing activity calls nothing"
    rows = [item.label for item in at.expander if item.label.startswith("Agent activity")]
    assert rows[1:] == ["Agent activity · Completed · 1.5s", "Agent activity · Completed · 1.5s",
                        "Agent activity · :red[Failed]"], rows
    assert not any(item.label in ("Run-wide totals", "Response totals") for item in at.expander), "closed activity must not build hidden timelines"
    for key in (f"run-{run['run_id']}-activity", f"fu-{run['run_id']}-0-activity",
                f"fu-{run['run_id']}-1-activity", f"fu-stopped-{run['run_id']}-0-activity"):
        at.session_state[key] = True
    at.run()
    assert calls == ["first?", "second?", "boom"], "opening saved activity never calls the engine"
    assert sum(item.label == "Run-wide totals" for item in at.expander) == 1
    assert sum(item.label == "Response totals" for item in at.expander) == 2
    assert sum(item.label == "Cost" and "0.007157" in item.value for item in at.metric) == 2
    assert sum("this follow-up only" in item.value for item in at.caption) == 2
    notes = [code.value for code in at.code if '"note"' in code.value]
    assert [next(q for q in ("first?", "second?", "boom") if q in note) for note in notes] == [
        "first?", "second?", "boom"], "each follow-up shows its own step 1, once"
    steps = [item.label for item in at.expander
             if item.label.startswith("get_acquirer_deals · MCP · ") and "round 1" in item.label
             and item.label.count(" · ") <= 4]
    assert steps == ["get_acquirer_deals · MCP · Completed · round 1 · 3 ms"] * 2 + [
        "get_acquirer_deals · MCP · :orange[Not finished] · round 1"], steps
    assert any("tool server went away" in item.value for item in at.error)


# ---------- deleting a saved session ----------

DELETE_QUESTION = "Delete this research session and its saved reports, follow-ups and activity?"


def plant_run(root: Path, run_id: str, title: str) -> Path:
    """A finished run folder small enough to open. It is not one of the real research sessions."""
    folder = root / run_id
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "research_title": title,
        "target": dict(config.DEFAULT_TARGET),
        "reports": [{
            "acquirer_key": "blackstone",
            "display_name": "Blackstone",
            "acquirer_type": "Financial Sponsor",
            "rank": 1,
            "score_rank": 1,
            "score": "0.10",
            "selection_note": None,
            "starting_level": "Low",
            "draft": None,
            "status": "failed",
            "issues": ["stopped"],
            "error": "stopped",
        }],
    }
    (folder / "reports.json").write_text(json.dumps(payload), encoding="utf-8")
    (folder / "reports.md").write_text("saved report\n", encoding="utf-8")
    (folder / "followups.json").write_text("[]", encoding="utf-8")
    (folder / "trace.json").write_text(
        json.dumps({"request": f"Request for {title}", "events": []}), encoding="utf-8")
    (folder / "sources.json").write_text(
        json.dumps({"numbers": {}, "entries": []}), encoding="utf-8")
    (folder / "ranking.json").write_text(json.dumps({"buyers": []}), encoding="utf-8")
    return folder


def block_research(monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise AssertionError("research must not start")

    monkeypatch.setattr(engine, "run_engine", explode)
    monkeypatch.setattr(engine, "run_followup", explode)
    monkeypatch.setattr(engine, "parse_target", explode)
    monkeypatch.setattr(engine, "MCPClient", explode)


def test_menu_opens_confirmation_without_opening_the_session_or_starting_research(
        folders, monkeypatch):
    block_research(monkeypatch)
    folder = plant_run(config.RUNS_DIR, "20990101-120000", "Disposable buyer research")
    at = start()
    assert at.session_state.get("current_run") is None
    at.button(key="menu-20990101-120000").click().run()
    assert not at.exception, at.exception
    text = blob(at)
    assert "Disposable buyer research" in text
    assert "Jan 01, 12:00" in text
    assert DELETE_QUESTION in text
    assert at.button(key="delete-cancel").label == "Cancel"
    assert at.button(key="delete-confirm").label == "Delete"
    assert at.session_state.get("current_run") is None
    assert at.session_state.pending_delete == "20990101-120000"
    assert folder.is_dir() and (folder / "reports.md").read_text() == "saved report\n"


def test_cancel_leaves_the_session_and_inactive_delete_keeps_the_open_one(
        folders, monkeypatch):
    block_research(monkeypatch)
    active = plant_run(config.RUNS_DIR, "20990101-120000", "Open buyer research")
    other = plant_run(config.RUNS_DIR, "20990101-130000", "Other buyer research")
    at = start()
    at.button(key="nav-history").click().run()
    assert at.button(key="history-menu-20990101-130000").label == "Delete"
    at.button(key="open-20990101-120000").click().run()
    assert not at.exception, at.exception
    assert at.session_state.current_run["run_id"] == "20990101-120000"
    assert "Request for Open buyer research" in blob(at)

    at.button(key="nav-history").click().run()
    at.button(key="history-menu-20990101-130000").click().run()
    assert not at.exception, at.exception
    assert DELETE_QUESTION in blob(at)
    assert "Other buyer research" in blob(at)
    at.button(key="delete-cancel").click().run()
    assert not at.exception, at.exception
    assert at.session_state.get("pending_delete") is None
    assert other.is_dir() and (other / "reports.md").is_file()
    assert at.session_state.current_run["run_id"] == "20990101-120000"
    assert "Other buyer research" in blob(at)

    at.button(key="history-menu-20990101-130000").click().run()
    at.button(key="delete-confirm").click().run()
    assert not at.exception, at.exception
    assert not other.exists()
    assert active.is_dir()
    assert at.session_state.current_run["run_id"] == "20990101-120000"
    assert at.session_state.screen == "history"
    assert "Other buyer research" not in blob(at)
    assert "Open buyer research" in blob(at)
    at.run()
    assert not other.exists()
    assert "Other buyer research" not in blob(at)


def test_deleting_the_active_session_returns_to_new_research(folders, monkeypatch):
    block_research(monkeypatch)
    folder = plant_run(config.RUNS_DIR, "20990101-120000", "Open buyer research")
    at = start()
    at.button(key="recent-20990101-120000").click().run()
    assert not at.exception, at.exception
    at.session_state.followups = [{"question": "Why this buyer?"}]
    at.session_state.stopped_followups = [{
        "run_id": "20990101-120000", "position": 0, "question": "Why?",
        "events": [], "error": "stopped"}]
    at.session_state.selected_buyer = "blackstone"
    at.session_state.messages = [{"role": "user", "content": "old request"}]
    at.session_state.user_request = "old request"
    at.session_state.pending_question = "still waiting"
    at.button(key="menu-20990101-120000").click().run()
    assert not at.exception, at.exception
    at.button(key="delete-confirm").click().run()
    assert not at.exception, at.exception
    assert not folder.exists()
    assert at.session_state.get("current_run") is None
    assert at.session_state.followups == []
    assert at.session_state.stopped_followups == []
    assert at.session_state.selected_buyer is None
    assert at.session_state.panel_open is False
    assert at.session_state.composing is True
    assert at.session_state.screen == "research"
    assert at.session_state.messages == []
    assert at.session_state.get("user_request") is None
    assert at.session_state.get("pending_question") is None
    assert at.session_state.get("pending_delete") is None
    text = blob(at)
    assert "Try this request" in text
    assert "Use this example" in text
    assert "old request" not in text
    assert "Open buyer research" not in text
    at.button(key="nav-history").click().run()
    assert "No saved runs yet" in blob(at)
    assert not folder.exists()


def test_delete_refuses_sample_outside_paths_and_a_run_still_being_written(folders, monkeypatch):
    block_research(monkeypatch)
    sample = plant_run(config.SAMPLE_DIR, "20990101-110000", "Sample assessment")
    outside = folders / "not-runs" / "20990101-140000"
    plant_run(outside.parent, outside.name, "Outside the runs folder")
    writing = config.RUNS_DIR / "20990101-150000"
    writing.mkdir(parents=True)
    (writing / "mcp_server.log").write_text("still writing\n", encoding="utf-8")
    linked = folders / "linked-target"
    plant_run(linked.parent, linked.name, "Linked target")
    link = config.RUNS_DIR / "20990101-160000"
    link.symlink_to(linked, target_is_directory=True)
    kept = plant_run(config.RUNS_DIR, "20990101-120000", "Kept buyer research")

    at = start()
    at.button(key="nav-history").click().run()
    button_keys = [item.key for item in at.button]
    assert "history-menu-20990101-120000" in button_keys
    assert "history-menu-20990101-110000" not in button_keys
    assert "history-menu-20990101-160000" not in button_keys
    assert "menu-20990101-110000" not in button_keys
    assert "Delete research" not in blob(at)

    def refuse(run_id: str, message: str) -> None:
        at.session_state.pending_delete = run_id
        at.run()
        assert not at.exception, at.exception
        at.button(key="delete-confirm").click().run()
        assert not at.exception, at.exception
        assert message in errors(at)
        assert at.session_state.get("pending_delete") == run_id

    refuse("20990101-110000", "Only a saved research session can be deleted.")
    assert (sample / "reports.json").is_file()
    refuse(str(outside), "That is not a saved research session.")
    assert (outside / "reports.json").is_file()
    refuse("../20990101-110000", "That is not a saved research session.")
    assert (sample / "reports.json").is_file()
    refuse("20990101-150000", "still being written")
    assert (writing / "mcp_server.log").is_file()
    refuse("20990101-160000", "Only a saved research session can be deleted.")
    assert link.is_symlink() and (linked / "reports.json").is_file()
    assert kept.is_dir()
    assert at.session_state.get("current_run") is None


def test_delete_failure_is_reported_and_does_not_claim_success(folders, monkeypatch):
    block_research(monkeypatch)
    folder = plant_run(config.RUNS_DIR, "20990101-120000", "Kept buyer research")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "rmtree", boom)
    at = start()
    at.button(key="nav-history").click().run()
    at.button(key="open-20990101-120000").click().run()
    assert at.session_state.current_run["run_id"] == "20990101-120000"
    at.button(key="menu-20990101-120000").click().run()
    at.button(key="delete-confirm").click().run()
    assert not at.exception, at.exception
    assert "Could not delete that research session." in errors(at)
    assert "Deleted" not in errors(at)
    assert folder.is_dir() and (folder / "reports.md").is_file()
    assert at.session_state.current_run["run_id"] == "20990101-120000"
    assert at.session_state.pending_delete == "20990101-120000"
    assert "Request for Kept buyer research" in blob(at)


def test_an_already_missing_run_closes_without_an_error(folders, monkeypatch):
    block_research(monkeypatch)
    folder = plant_run(config.RUNS_DIR, "20990101-120000", "Gone buyer research")
    at = start()
    at.button(key="recent-20990101-120000").click().run()
    assert not at.exception, at.exception
    shutil.rmtree(folder)
    at.session_state.pending_delete = "20990101-120000"
    at.session_state.followups = [{"question": "Why?"}]
    at.session_state.selected_buyer = "blackstone"
    at.run()
    assert not at.exception, at.exception
    at.button(key="delete-confirm").click().run()
    assert not at.exception, at.exception
    assert not at.error
    assert at.session_state.get("current_run") is None
    assert at.session_state.followups == []
    assert at.session_state.selected_buyer is None
    assert at.session_state.composing is True
    assert "Try this request" in blob(at)
    at.session_state.pending_delete = "20980101-000000"
    at.run()
    at.button(key="delete-confirm").click().run()
    assert not at.exception, at.exception
    assert not at.error
    assert "Try this request" in blob(at)


def test_saved_report_url_restores_without_research(folders, monkeypatch):
    block_research(monkeypatch)
    plant_run(config.RUNS_DIR, "20261101-010000", "Saved research")
    at = AppTest.from_file(APP)
    at.query_params.update({"run": "20261101-010000", "buyer": "blackstone", "tab": "score"})
    at.run()
    assert not at.exception, at.exception
    assert at.session_state.current_run["run_id"] == "20261101-010000"
    assert at.session_state.selected_buyer == "blackstone"
    assert at.session_state.panel_tab == "Score Breakdown"
    assert panel_shown(at)
    at.button(key="close-panel").click().run()
    assert not at.exception, at.exception
    assert at.query_params == {"run": "20261101-010000"}


def test_invalid_saved_report_url_never_starts_research(folders, monkeypatch):
    block_research(monkeypatch)
    at = AppTest.from_file(APP)
    at.query_params["run"] = "../outside"
    at.run()
    assert not at.exception, at.exception
    assert at.session_state.current_run is None
    assert at.warning


def test_saved_followup_displays_full_source_catalog_without_new_research(folders, monkeypatch):
    fake_engine(monkeypatch)
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    catalog = [{"Number": 1, "Source ID": "MA-2021-0195", "Kind": "deal"},
               {"Number": 2, "Source ID": "ST-PROFILE-blackstone-closed_count", "Kind": "statistic"}]
    (Path(run["run_dir"]) / "followups.json").write_text(json.dumps([
        {"question": "List all report sources", "statements": [], "markdown": "Sources below.",
         "status": "verified", "issues": [], "events": [], "totals": {}, "source_catalog": catalog}]))
    at = start()
    at.button(key="nav-history").click().run()
    at.button(key=f"open-{run['run_id']}").click().run()
    assert not at.exception
    assert any(e.label == "Report sources" for e in at.expander)
    table = next(frame.value for frame in at.dataframe if list(frame.value.columns) == ["Number", "Source ID", "Kind"])
    assert table.to_dict("records") == catalog


def test_invalid_followup_shows_one_status_and_diagnostics(folders, monkeypatch):
    fake_engine(monkeypatch)
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    issue = 'The model returned invalid JSON.'
    (Path(run['run_dir']) / 'followups.json').write_text(json.dumps([
        {'question': 'Compare buyers', 'statements': [],
         'markdown': '**Status:** Not verified\n> **Not verified.** Duplicate diagnostic',
         'status': 'not_verified', 'issues': [issue], 'events': [], 'totals': {}}]))
    at = start()
    at.button(key='nav-history').click().run()
    at.button(key=f"open-{run['run_id']}").click().run()
    assert not at.exception
    text = blob(at)
    assert text.count('**Status:** Not verified') == 1
    assert 'Duplicate diagnostic' not in text
    assert issue in text


def test_history_restores_unfinished_question_in_fresh_browser_session(folders, monkeypatch):
    from ma_engine.persistence import submissions
    submissions.begin('My question submitted before a browser refresh')
    def unexpected(*args, **kwargs):
        raise AssertionError('Opening history must not restart research')
    monkeypatch.setattr(engine, 'run_engine', unexpected)
    at = start()
    at.button(key='nav-history').click().run()
    assert not at.exception
    assert 'My question submitted before a browser refresh' in blob(at)
    assert 'Unfinished requests' in blob(at)
    assert any(str(button.key).startswith('submission-sidebar-') for button in at.button)


def test_new_question_saved_before_parsing_and_restored_after_failure(folders, monkeypatch):
    from ma_engine.persistence import submissions
    async def interrupted(text, progress=None):
        assert submissions.unfinished()[0]['question'] == text
        raise RuntimeError('Simulated interruption')
    monkeypatch.setattr(engine, 'parse_target', interrupted)
    at = start()
    at.chat_input(key='research_chat').set_value('Find healthcare buyers for my company').run()
    assert not at.exception
    assert submissions.unfinished()[0]['status'] == 'interrupted'
    fresh = start()
    fresh.button(key='nav-history').click().run()
    assert not fresh.exception
    assert 'Find healthcare buyers for my company' in blob(fresh)


@pytest.mark.parametrize('location', ['sidebar', 'history'])
def test_delete_unfinished_request_with_confirmation(folders, monkeypatch, location):
    from ma_engine.persistence import submissions
    block_research(monkeypatch)
    token = submissions.begin('Unfinished question to delete')
    at = start()
    if location == 'history':
        at.button(key='nav-history').click().run()
    key = f"{'history-' if location == 'history' else ''}menu-submission-{token}"
    at.button(key=key).click().run()
    at.button(key='delete-cancel').click().run()
    assert len(submissions.unfinished()) == 1
    at.button(key=key).click().run()
    at.button(key='delete-confirm').click().run()
    assert not at.exception
    assert not submissions.unfinished()
    assert 'Unfinished question to delete' not in blob(at)
    fresh = start()
    assert 'Unfinished question to delete' not in blob(fresh)
