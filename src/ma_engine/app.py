"""Streamlit interface: display code only.

Runs `engine.run_engine` for the target in the sidebar and shows the ranked buyers, each
checked report, the original CSV rows behind its citations, its score breakdown and the trace
of how it was made. With no API key, a saved run from `outputs/runs/` or `outputs/sample/` can
still be browsed. Start with `make run`.
"""

import asyncio
import json
from pathlib import Path

import openai
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from ma_engine import config, engine, guardrails, ranking
from ma_engine.clients.llm_client import LLMConfigError
from ma_engine.models import GEOGRAPHIES, OWNERSHIPS, BuyerReport, Target
from ma_engine.prepare import SECTORS, load_raw

REQUIRED_FILES = [config.RAW_CSV, config.TRANSACTIONS_CSV, config.ACQUIRER_PROFILES_JSON,
                  config.VALUATION_COMPS_CSV, config.SECTOR_STATS_CSV,
                  config.SECTOR_SIMILARITY_CSV, config.DATA_QUALITY_JSON]
MARGIN_LABELS = {"strong": "Strong (above the sector median)", "any": "Any"}
TOOL_COLUMNS = ["round", "tool", "arguments", "status", "duration_ms", "repeated",
                "source_id_count", "error"]
LLM_COLUMNS = ["purpose", "prompt_tokens", "completion_tokens", "cost_usd", "cache_hit",
               "duration_ms", "tool_calls_requested", "parse_error"]


# ---------- loading ----------

def missing_files() -> list[Path]:
    return [path for path in REQUIRED_FILES if not path.is_file()]


@st.cache_data
def source_rows() -> pd.DataFrame:
    """The original CSV, every column as text, indexed by transaction_id."""
    return load_raw(config.RAW_CSV).set_index("transaction_id", drop=False)


@st.cache_data
def score_breakdown(target: dict, excluded_keys: tuple) -> dict:
    """Ranking entries by buyer key, from ranking.py (saved runs do not store them)."""
    data = ranking.rank_acquirers(Target(**target), limit=config.SHORTLIST_SIZE,
                                  excluded_keys=excluded_keys)["data"]
    return {b["acquirer_key"]: b for b in data["buyers"]}


def saved_run_dirs() -> dict[str, Path]:
    """Folders a saved run can be opened from, labelled for the user."""
    found = {}
    latest = guardrails.latest_run_dir(config.RUNS_DIR)
    if latest:
        found[f"Latest run ({latest.name})"] = latest
    sample = (config.SAMPLE_DIR if (config.SAMPLE_DIR / "reports.json").is_file()
              else guardrails.latest_run_dir(config.SAMPLE_DIR))
    if sample:
        found["Sample run (outputs/sample)"] = sample
    return found


def open_saved(run_dir: Path) -> dict:
    saved, reports = guardrails.load_reports(run_dir)
    trace_path = run_dir / "trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8")) if trace_path.is_file() else {}
    return {"run_id": saved["run_id"], "run_dir": str(run_dir), "target": saved["target"],
            "reports": reports, "trace": trace, "entries": None, "saved": True}


# ---------- running ----------

def safe(text: str) -> str:
    return text.replace(config.OPENROUTER_API_KEY, "***") if config.OPENROUTER_API_KEY else text


def error_message(exc: Exception, events: list[str]) -> str:
    """One clear sentence for each way a run can stop."""
    if isinstance(exc, LLMConfigError):
        return f"Model setting problem: {exc}"
    if isinstance(exc, openai.AuthenticationError):
        return "OpenRouter rejected the API key. Check OPENROUTER_API_KEY in your .env file."
    if isinstance(exc, openai.PermissionDeniedError):
        return "OpenRouter refused access for this key or model. Check your OpenRouter account."
    if isinstance(exc, openai.RateLimitError):
        return "OpenRouter's rate limit was reached, even after retries. Wait a minute and try again."
    if not events:
        return (f"The MCP server failed to start ({type(exc).__name__}: {exc}). Run `make mcp` "
                "in a terminal to see its error; if it mentions missing files, run `make data`.")
    return f"The run stopped: {type(exc).__name__}: {exc}"


def execute(target: Target, use_cache: bool, excluded_keys: list[str]) -> dict | None:
    """Run the engine with live progress. Returns the run, or None after showing the error."""
    try:
        config.require_api_key()
    except RuntimeError as exc:
        st.error(f"{exc} Until then you can open a saved run below.")
        return None
    events = []
    with st.status("Running the engine…", expanded=True) as status:
        def progress(event: str) -> None:
            events.append(event)
            status.write(event)
        try:
            run = asyncio.run(engine.run_engine(target, use_cache=use_cache,
                                                excluded_keys=excluded_keys, progress=progress))
        except Exception as exc:
            status.update(label="Run stopped", state="error")
            st.error(safe(error_message(exc, events)))
            return None
        status.update(label=f"Run {run['run_id']} finished", state="complete", expanded=False)
    return {"run_id": run["run_id"], "run_dir": run["run_dir"], "target": target.model_dump(),
            "reports": run["reports"], "trace": run["trace"], "saved": False,
            "entries": {b["acquirer_key"]: b for b in run["ranking"]["buyers"]}}


def exclude(key: str) -> None:
    """'Not relevant' button: re-run the same target without this buyer (cache keeps the rest)."""
    st.session_state.excluded.append(key)
    st.session_state.request = {"target": st.session_state.current_run["target"],
                                "use_cache": True}


# ---------- sidebar ----------

def sidebar_form() -> dict | None:
    """The target form. Returns a run request when Run or Regenerate is pressed."""
    d = config.DEFAULT_TARGET
    with st.sidebar.form("target"):
        st.subheader("Target company")
        sector = st.selectbox("Sector", SECTORS, index=SECTORS.index(d["sector"]))
        ev_mm = st.number_input("Enterprise value ($M)", value=float(d["ev_mm"]), step=10.0)
        geography = st.selectbox("Geography", GEOGRAPHIES, index=GEOGRAPHIES.index(d["geography"]))
        ownership = st.selectbox("Ownership", OWNERSHIPS, index=OWNERSHIPS.index(d["ownership"]))
        margin = st.radio("EBITDA margin profile", list(MARGIN_LABELS),
                          index=list(MARGIN_LABELS).index(d["margin_profile"]),
                          format_func=MARGIN_LABELS.get)
        run = st.form_submit_button("Run", type="primary", key="run")
        regenerate = st.form_submit_button("Regenerate", key="regenerate",
                                           help="Run again without the response cache.")
    st.sidebar.caption("Regenerate asks the model again instead of reusing saved answers.")
    if not (run or regenerate):
        return None
    values = {"sector": sector, "ev_mm": ev_mm, "geography": geography,
              "ownership": ownership, "margin_profile": margin}
    try:
        Target(**values)
    except ValidationError as exc:
        st.sidebar.error("; ".join(f"{e['loc'][0]}: {e['msg'].removeprefix('Value error, ')}"
                                   for e in exc.errors()))
        return None
    return {"target": values, "use_cache": not regenerate}


# ---------- run display ----------

def cited_ids(report: BuyerReport) -> list[str]:
    draft = report.draft
    items = [s for section in guardrails.SECTIONS for s in getattr(draft, section)]
    ids = [i for item in [*items, *draft.risk_flags, draft.conviction] for i in item.source_ids]
    return list(dict.fromkeys(ids))


def show_summary(run: dict) -> None:
    reports, trace = run["reports"], run["trace"]
    origin = "saved run" if run["saved"] else "this session"
    st.subheader(f"Run {run['run_id']} ({origin})")
    st.caption("Target: " + ", ".join(f"{k} = {v}" for k, v in run["target"].items())
               + f" · Folder: {run['run_dir']}")
    if len(reports) < config.REPORT_COUNT:
        st.warning(f"Only {len(reports)} of {config.REPORT_COUNT} reports were produced. "
                   "See the failed buyers below and the run details.")
    not_verified = [r.display_name for r in reports if r.status != "verified"]
    if not_verified:
        st.warning(f"{len(not_verified)} report(s) did not pass the guardrail checks: "
                   + ", ".join(not_verified))
    for error in trace.get("errors", []):
        st.warning(f"Buyer {error['acquirer_key']} failed: {safe(error['error'])}")
    for sub in trace.get("substitutions", []):
        st.info(f"{sub['replacement']} replaced {sub['failed']} (shortlist rank "
                f"{sub['replacement_shortlist_rank']}).")
    if trace.get("excluded_keys"):
        st.info("Marked not relevant and excluded: " + ", ".join(trace["excluded_keys"]))
    table = pd.DataFrame([{
        "Rank": r.rank, "Buyer": r.display_name, "Type": r.acquirer_type, "Score": r.score,
        "Score rank": r.score_rank, "Starting conviction": r.starting_level,
        "Conviction": r.draft.conviction.level,
        "Status": "Verified" if r.status == "verified" else "Not verified",
        "Note": r.selection_note or ""} for r in reports])
    st.dataframe(table, hide_index=True)
    folder = Path(run["run_dir"])
    columns = st.columns(2)
    for column, name, mime in zip(columns, ["reports.md", "reports.json"],
                                  ["text/markdown", "application/json"]):
        if (folder / name).is_file():
            column.download_button(f"Download {name}", (folder / name).read_bytes(),
                                   file_name=name, mime=mime, on_click="ignore")


def show_sources(report: BuyerReport) -> None:
    ids = cited_ids(report)
    st.write("Cited transaction IDs: " + (", ".join(ids) or "none"))
    rows = source_rows()
    found = [i for i in ids if i in rows.index]
    if found:
        st.dataframe(rows.loc[found], hide_index=True)
    if len(found) < len(ids):
        st.warning("Not in the CSV: " + ", ".join(i for i in ids if i not in rows.index))


def show_score(entry: dict | None, saved: bool) -> None:
    if entry is None:
        st.info("This buyer is not in the current ranking for this target, so its score "
                "breakdown cannot be shown.")
        return
    if saved:
        st.caption("Saved runs do not store the breakdown; this is ranking.py's output for the "
                   "same target and exclusions on the current processed data.")
    st.write(f"Score **{entry['score']['display']}** · score rank {entry['score_rank']} · "
             f"starting conviction **{entry['starting_conviction']['level']}**")
    st.caption(entry["starting_conviction"]["reason"])
    st.dataframe(pd.DataFrame([{
        "Signal": s["name"], "Value": s["value_display"], "Weight": s["weight_display"],
        "Contribution": s["contribution_display"], "Explanation": s["explanation"]}
        for s in entry["signals"]]), hide_index=True)


def show_buyer_trace(buyer: dict | None) -> None:
    if not buyer:
        st.info("No trace was saved for this buyer.")
        return
    st.write(f"Tool rounds: {buyer.get('rounds')} · Stopped because: {buyer.get('stop_reason')} · "
             f"Repair attempts: {buyer.get('repair_attempts')} · "
             f"Time: {buyer.get('duration_ms')} ms")
    calls = [{**c, "arguments": json.dumps(c.get("arguments"), ensure_ascii=False)}
             for c in buyer.get("tool_calls", [])]
    st.markdown("**Tool calls, round by round** (calls in one round ran in parallel and are "
                "listed as they finished)")
    st.dataframe(pd.DataFrame(calls, columns=TOOL_COLUMNS), hide_index=True)
    st.markdown("**Model calls**")
    st.dataframe(pd.DataFrame(buyer.get("llm_calls", []), columns=LLM_COLUMNS), hide_index=True)
    if buyer.get("first_check_issues"):
        st.markdown("**Guardrail issues on the first draft**")
        st.write(buyer["first_check_issues"])
    if buyer.get("length_warnings"):
        st.caption("Length warnings: " + "; ".join(buyer["length_warnings"]))


def show_buyer(report: BuyerReport, run: dict, entry: dict | None, buyer: dict | None) -> None:
    label = (f"{report.rank}. {report.display_name} — {report.draft.conviction.level} — "
             + ("Verified" if report.status == "verified" else "NOT VERIFIED"))
    with st.expander(label):
        if report.status != "verified":
            st.error("**Not verified.** This report did not pass the guardrail checks:\n\n"
                     + "\n".join(f"- {issue}" for issue in report.issues))
        tabs = st.tabs(["Report", "Source rows", "Score breakdown", "How this was decided"])
        with tabs[0]:
            st.markdown(guardrails.render_markdown(report))
        with tabs[1]:
            show_sources(report)
        with tabs[2]:
            show_score(entry, run["saved"])
        with tabs[3]:
            show_buyer_trace(buyer)
        st.button("Not relevant: re-run without this buyer", key=f"exclude-{report.acquirer_key}",
                  on_click=exclude, args=(report.acquirer_key,))


def show_run_totals(trace: dict) -> None:
    st.subheader("How this was decided: run totals")
    if not trace:
        st.info("This run has no trace.json.")
        return
    totals = trace.get("llm_totals", {})
    committee = trace.get("committee", {})
    metrics = st.columns(5)
    metrics[0].metric("Wall time", f"{trace.get('wall_time_s')} s")
    metrics[1].metric("Model calls", f"{totals.get('calls')} ({totals.get('cache_hits')} cached)")
    metrics[2].metric("Tokens in / out",
                      f"{totals.get('prompt_tokens')} / {totals.get('completion_tokens')}")
    metrics[3].metric("Cost (USD)", f"${totals.get('cost_usd', 0):.4f}")
    metrics[4].metric("Cache used", "Yes" if trace.get("use_cache") else "No (Regenerate)")
    st.caption(f"Model {trace.get('model')} · ranking {trace.get('ranking_ms')} ms · research "
               f"{trace.get('research_ms')} ms · committee {committee.get('duration_ms')} ms · "
               f"MCP sessions opened {trace.get('mcp', {}).get('sessions_opened')}")
    with st.expander("Committee review"):
        if committee.get("error"):
            st.warning(committee["error"])
        st.write({k: committee.get(k) for k in ("applied", "rejected", "similar_pairs")})
    with st.expander("Quality checks (same as `make check`)"):
        st.write(trace.get("quality", {}).get("summary", {}))


def show_run(run: dict) -> None:
    show_summary(run)
    entries = run["entries"]
    if entries is None:
        excluded = tuple(run["trace"].get("excluded_keys", []))
        try:
            entries = score_breakdown(run["target"], excluded)
        except ValueError as exc:
            st.warning(f"Score breakdowns are unavailable for this saved run: {exc}")
            entries = {}
    buyers = {b["acquirer_key"]: b for b in run["trace"].get("buyers", [])}
    st.subheader("Buyers")
    for report in run["reports"]:
        show_buyer(report, run, entries.get(report.acquirer_key), buyers.get(report.acquirer_key))
    show_run_totals(run["trace"])


def offer_saved_runs() -> None:
    st.info("No run in this session yet. Set the target in the sidebar and press Run.")
    saved = saved_run_dirs()
    if not saved:
        st.caption("No saved runs found in outputs/runs or outputs/sample.")
        return
    st.write("Or open a saved run (no API key needed):")
    for label, run_dir in saved.items():
        if st.button(f"Open {label}", key=f"open-{run_dir}"):
            run = open_saved(run_dir)
            st.session_state.current_run = run
            st.session_state.excluded = list(run["trace"].get("excluded_keys", []))
            st.rerun()


# ---------- page ----------

def main() -> None:
    st.set_page_config(page_title="M&A Acquirer Identification", layout="wide")
    st.title("M&A Acquirer Identification")
    st.caption("Ranks the 10 most likely acquirers from 500 historical deals and writes a "
               "source-cited report for each. Every number comes from Python, not the AI.")
    missing = missing_files()
    if missing:
        st.error("Data files are missing: " + ", ".join(p.name for p in missing)
                 + ". Run `make data` first (it needs the raw CSV in data/raw/).")
        st.stop()
    st.session_state.setdefault("excluded", [])
    request = sidebar_form() or st.session_state.pop("request", None)
    if request:
        last = st.session_state.get("current_run")
        if last is None or last["target"] != request["target"]:
            st.session_state.excluded = []
        run = execute(Target(**request["target"]), request["use_cache"],
                      list(st.session_state.excluded))
        if run:
            st.session_state.current_run = run
    if "current_run" in st.session_state:
        show_run(st.session_state.current_run)
    else:
        offer_saved_runs()


main()
