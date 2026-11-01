"""End-to-end runs: scripted fake LLM, the real MCP server over stdio, the real processed data.

Already covered in test_agent.py and not repeated here: committee one-step rule, cache hits,
request format, same-type substitution rule, rate-limit and wrong-model stops, and single-buyer
repair. These tests check what a whole run leaves behind on disk, which the app reads.
"""

import asyncio
import json

import pytest
from mcp.shared.exceptions import MCPError
from test_agent import FakeLLM, bad_draft, buyer_key, committee_policy, evidence_policy, \
    good_draft, research_round

from ma_engine import config, engine, guardrails, prepare
from ma_engine.clients import data_client, mcp_client


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


def stdio_run(monkeypatch, llm, **kwargs):
    """A full run with the fake LLM; the engine starts the real stdio MCP server itself."""
    monkeypatch.setattr(engine, "LLMClient", lambda: llm)
    events = []
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET, progress=events.append, **kwargs))
    return run, events


def saved_trace(run) -> dict:
    return json.loads((config.RUNS_DIR / run["run_id"] / "trace.json").read_text())


def test_full_run_saves_ten_reports_trace_and_server_log(runs_dir, monkeypatch):
    run, events = stdio_run(monkeypatch, FakeLLM(committee=committee_policy({})))
    reports = run["reports"]
    assert [r.rank for r in reports] == list(range(1, config.REPORT_COUNT + 1))
    assert all(r.status == "verified" for r in reports), [r.issues for r in reports]

    run_dir = runs_dir / run["run_id"]
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "mcp_server.log", "ranking.json", "reports.json", "reports.md", "sources.json",
        "trace.json"]
    assert not list(runs_dir.glob(".mcp_server-*"))
    saved, loaded = guardrails.load_reports(run_dir)
    assert loaded == reports and saved["target"] == config.DEFAULT_TARGET
    markdown = (run_dir / "reports.md").read_text()
    assert all(f"## {r.rank}. {r.display_name}" in markdown for r in reports)

    trace = saved_trace(run)
    assert trace["mcp"]["sessions_opened"] == 1 and len(trace["mcp"]["tools"]) == 7
    assert trace["wall_time_s"] > 0 and trace["llm_totals"]["calls"] > 0
    assert [b["acquirer_key"] for b in trace["buyers"]] == [r.acquirer_key for r in reports]
    for buyer in trace["buyers"]:
        assert buyer["tool_calls"] and buyer["llm_calls"] and buyer["final_status"] == "verified"
        assert all(isinstance(c["arguments"], dict) and c["duration_ms"] >= 0
                   for c in buyer["tool_calls"])
    assert trace["quality"]["summary"]["full_report_count"]
    assert trace["quality"]["summary"]["all_reports_verified"]
    messages = [e["message"] for e in events]
    assert any(m.startswith("Ranked ") for m in messages) and any("Committee review" in m for m in messages)
    assert messages[-1].startswith("Saved run")


def test_missing_processed_data_and_raw_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(data_client, "TRANSACTIONS_CSV", tmp_path / "transactions.csv")
    with pytest.raises(FileNotFoundError, match="Run `make data` first"):
        data_client.DataClient()
    with pytest.raises(FileNotFoundError, match="Raw CSV not found"):
        prepare.load_raw(tmp_path / "missing.csv")


def test_server_without_processed_data_stops_before_anything_starts(runs_dir, tmp_path,
                                                                   monkeypatch):
    """The real server module, started over stdio with its transactions file pointed away."""
    missing = str(tmp_path / "transactions.csv")
    monkeypatch.setattr(mcp_client, "SERVER_ARGS", ["-c", (
        "from pathlib import Path; import runpy; from ma_engine.clients import data_client; "
        f"data_client.TRANSACTIONS_CSV = Path({missing!r}); "
        "runpy.run_module('ma_engine.mcp_server.server', run_name='__main__')")])
    with pytest.raises(MCPError):
        stdio_run(monkeypatch, FakeLLM())
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []


def test_missing_key_stops_before_the_server_starts(runs_dir, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(engine, "MCPClient", lambda **kwargs: pytest.fail("server started"))
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not set. Copy .env.example"):
        asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    assert not runs_dir.exists()


def test_tool_failures_go_back_to_the_model_and_the_run_completes(runs_dir, monkeypatch):
    def policy(messages):
        broken = [("get_sector_overview", {"sector": "Hospitals"}),
                  ("get_acquirer_deals", {"acquirer_key": buyer_key(messages), "limit": 500})]
        return broken + evidence_policy(messages) if research_round(messages) == 1 \
            else evidence_policy(messages)

    run, _ = stdio_run(monkeypatch, FakeLLM(research=policy, committee=committee_policy({})))
    assert len(run["reports"]) == config.REPORT_COUNT
    assert all(r.status == "verified" for r in run["reports"])
    for buyer in saved_trace(run)["buyers"]:
        # Calls in one round run in parallel, so the trace lists them in the order they finished.
        errors = {c["tool"]: c["error"] for c in buyer["tool_calls"] if c["status"] == "error"}
        assert sorted(errors) == ["get_acquirer_deals", "get_sector_overview"]
        assert "Unknown sector 'Hospitals'" in errors["get_sector_overview"]
        assert "limit" in errors["get_acquirer_deals"]


def test_one_buyer_failure_stays_in_place(runs_dir, monkeypatch):
    run, events = stdio_run(monkeypatch, FakeLLM(committee=committee_policy({}),
                                                 fail_keys={"gtcr"}))
    reports = {r.acquirer_key: r for r in run["reports"]}
    assert len(reports) == config.REPORT_COUNT and reports["gtcr"].status == "failed"
    assert reports["gtcr"].draft is None and "provider error" in reports["gtcr"].error
    trace = saved_trace(run)
    assert trace["substitutions"] == []
    assert trace["errors"][0]["partial_trace"]["tool_calls"]
    markdown = (runs_dir / run["run_id"] / "reports.md").read_text()
    assert "GTCR" in markdown and "Failed" in markdown
    assert "Replaced GTCR" not in markdown
    assert any(e["name"] == "buyer_failed" and e["acquirer_key"] == "gtcr" for e in events)


def test_malformed_model_output(runs_dir, monkeypatch, capsys):
    """A draft that breaks the rules twice ends 'Not verified' and is still shown and saved.
    Output that is not JSON at all after repair is a buyer error, so that buyer stays failed."""
    def write(messages):
        key = buyer_key(messages)
        return {"nordic capital": bad_draft(messages), "gtcr": "{not json"}.get(
            key, good_draft(messages))

    run, _ = stdio_run(monkeypatch, FakeLLM(write=write, committee=committee_policy({})))
    reports = {r.acquirer_key: r for r in run["reports"]}
    assert len(reports) == config.REPORT_COUNT and reports["gtcr"].status == "failed"
    assert reports["gtcr"].draft is None
    nordic = reports["nordic capital"]
    assert nordic.status == "not_verified"
    assert any("does not appear in the tool results" in i for i in nordic.issues)
    trace = saved_trace(run)
    assert "ReportError" in trace["errors"][0]["error"]
    assert trace["quality"]["not_verified"] == ["nordic capital"]
    markdown = (runs_dir / run["run_id"] / "reports.md").read_text()
    assert "> Not verified." in markdown
    with pytest.raises(SystemExit):
        guardrails.main()
    assert "1 report(s) not verified" in capsys.readouterr().out
