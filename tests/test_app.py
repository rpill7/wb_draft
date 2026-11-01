"""Streamlit app tests with streamlit.testing.v1.AppTest. No real LLM; the app runs in this
process, so settings and the engine's clients are replaced with monkeypatch."""

import asyncio

import pytest
from streamlit.testing.v1 import AppTest
from test_agent import FakeLLM, bad_draft, buyer_key, committee_policy, good_draft

from ma_engine import config, engine
from ma_engine.clients import mcp_client
from ma_engine.clients.mcp_client import MCPClient
from ma_engine.mcp_server.server import mcp as server

APP = str(config.ROOT / "src" / "ma_engine" / "app.py")


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "SAMPLE_DIR", tmp_path / "sample")
    return tmp_path


def start() -> AppTest:
    return AppTest.from_file(APP, default_timeout=60).run()


def fake_engine(monkeypatch, llm) -> None:
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(engine, "LLMClient", lambda: llm)
    monkeypatch.setattr(engine, "MCPClient", lambda log_file=None: MCPClient(transport=server))


def errors(at) -> str:
    return " ".join(e.value for e in at.error)


def test_app_loads_with_config_defaults(folders):
    at = start()
    assert not at.exception
    assert at.title[0].value == "M&A Acquirer Identification"
    sector, geography, ownership = (s.value for s in at.sidebar.selectbox)
    d = config.DEFAULT_TARGET
    assert (sector, geography, ownership) == (d["sector"], d["geography"], d["ownership"])
    assert at.sidebar.number_input[0].value == d["ev_mm"]
    assert at.sidebar.radio[0].value == d["margin_profile"]
    assert "No run in this session yet" in at.info[0].value
    assert any("No saved runs found" in c.value for c in at.caption)


def test_run_without_key_shows_the_missing_key_message(folders, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    at = start()
    at.button(key="run").click().run()
    assert not at.exception
    assert "OPENROUTER_API_KEY is not set. Copy .env.example to .env" in errors(at)
    assert not config.RUNS_DIR.exists()


def test_invalid_target_shows_the_model_message(folders):
    at = start()
    at.sidebar.number_input[0].set_value(0.0)
    at.button(key="run").click().run()
    assert "ev_mm: Input should be greater than or equal to 1" in at.sidebar.error[0].value


def test_missing_processed_data_says_run_make_data(folders, monkeypatch):
    monkeypatch.setattr(config, "TRANSACTIONS_CSV", folders / "transactions.csv")
    at = start()
    assert not at.exception
    assert "transactions.csv" in errors(at) and "make data" in errors(at)


def test_mcp_server_failure_has_its_own_message(folders, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(engine, "LLMClient", lambda: FakeLLM())
    monkeypatch.setattr(mcp_client, "SERVER_ARGS", ["-c", "raise SystemExit('no data')"])
    at = start()
    at.button(key="run").click().run()
    assert not at.exception
    assert "The MCP server failed to start" in errors(at)
    assert "test-key" not in errors(at)


def test_live_run_then_not_relevant_reruns_without_that_buyer(folders, monkeypatch):
    fake_engine(monkeypatch, FakeLLM(committee=committee_policy({})))
    at = start()
    at.button(key="run").click().run()
    assert not at.exception, at.exception
    labels = [e.label for e in at.expander if e.label[0].isdigit()]
    assert len(labels) == config.REPORT_COUNT and labels[0].startswith("1. Blackstone")
    assert at.status[0].label.endswith("finished")

    at.button(key="exclude-blackstone").click().run()
    assert not at.exception
    assert at.session_state["excluded"] == ["blackstone"]
    labels = [e.label for e in at.expander if e.label[0].isdigit()]
    assert len(labels) == config.REPORT_COUNT and not any("Blackstone" in x for x in labels)
    assert any("Marked not relevant" in i.value for i in at.info)


def test_saved_run_can_be_browsed_without_a_key(folders, monkeypatch):
    def write(messages):
        return bad_draft(messages) if buyer_key(messages) == "nordic capital" \
            else good_draft(messages)

    fake_engine(monkeypatch, FakeLLM(write=write, committee=committee_policy({})))
    run = asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")

    at = start()
    [open_button] = [b for b in at.button if b.label.startswith("Open Latest run")]
    open_button.click().run()
    assert not at.exception, at.exception
    assert at.subheader[0].value == f"Run {run['run_id']} (saved run)"
    labels = [e.label for e in at.expander if e.label[0].isdigit()]
    assert len(labels) == config.REPORT_COUNT
    assert any("Nordic Capital" in x and "NOT VERIFIED" in x for x in labels)
    assert any("**Not verified.**" in e.value for e in at.error)
    assert any(m.value.startswith("## 1. Blackstone") for m in at.markdown)
    assert [b.label for b in at.download_button] == ["Download reports.md",
                                                     "Download reports.json"]
    first = run["reports"][0]
    cited = first.draft.precedent_activity[0].source_ids[0]
    assert any(cited in df.value["transaction_id"].tolist() for df in at.dataframe
               if "transaction_id" in df.value)
    assert any(df.value.columns.tolist()[:2] == ["Signal", "Value"] for df in at.dataframe)
