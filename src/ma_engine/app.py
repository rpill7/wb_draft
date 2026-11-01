"""Chat-first research screen. Display only.

The page calls `engine.parse_target`, `engine.run_engine` and `engine.run_followup`.
It does not rank, score, or call the model itself. Numbers on screen are the display
strings those functions already returned.
"""

import asyncio
import json
from pathlib import Path

import openai
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from ma_engine import config, engine, guardrails
from ma_engine.agent import RequestError
from ma_engine.clients.data_client import get_client
from ma_engine.clients.llm_client import LLMConfigError
from ma_engine.models import GEOGRAPHIES, OWNERSHIPS, BuyerReport, Target
from ma_engine.prepare import SECTORS
from ma_engine.ui_trace import (
    as_markdown,
    buyer_status_lines,
    clip,
    live_timeline_md,
    render_timeline,
)

EXAMPLE = (
    "Find the 10 most likely acquirers for a regional Healthcare Services company "
    "valued at $200M EV with strong EBITDA margins."
)
FOLLOWUP_EXAMPLES = (
    "Why was the top buyer ranked first?",
    "Show the supporting transactions for the top buyer",
)
PAGES = ("New Research", "Research Results", "About")
FIELD_NAMES = {
    "sector": "sector",
    "ev_mm": "enterprise value",
    "ownership": "ownership",
    "geography": "geography",
    "margin_profile": "EBITDA margin",
}
MARGIN_LABELS = {"strong": "Strong (above the sector median)", "any": "Any"}
REQUIRED_FILES = [
    config.RAW_CSV, config.TRANSACTIONS_CSV, config.ACQUIRER_PROFILES_JSON,
    config.VALUATION_COMPS_CSV, config.SECTOR_STATS_CSV, config.SECTOR_SIMILARITY_CSV,
    config.DATA_QUALITY_JSON,
]

# The sidebar caption is drawn before a run connects. The progress callback fills this
# placeholder in the same script run, once the server reports its tool count.
_MCP_SLOT: dict = {}


# ---------- small display helpers ----------

def missing_files() -> list[Path]:
    return [path for path in REQUIRED_FILES if not path.is_file()]


def safe(text: str) -> str:
    key = config.OPENROUTER_API_KEY
    if key and text:
        return text.replace(key, "***")
    return text


def key_error_text() -> str | None:
    try:
        config.require_api_key()
    except RuntimeError as exc:
        return f"{exc} Saved runs in the sidebar still open without a key."
    return None


def failure_text(exc: Exception, events: list, *, during_run: bool) -> str:
    """One clear sentence for each way a request or a run can stop."""
    if isinstance(exc, RequestError):
        return str(exc)
    if isinstance(exc, LLMConfigError):
        return f"Model setting problem: {exc}"
    if isinstance(exc, openai.AuthenticationError):
        return "OpenRouter rejected the API key. Check OPENROUTER_API_KEY in your .env file."
    if isinstance(exc, openai.PermissionDeniedError):
        return "OpenRouter refused access for this key or model. Check your OpenRouter account."
    if isinstance(exc, openai.RateLimitError):
        return "OpenRouter's rate limit was reached, even after retries. Wait a minute and try again."
    if isinstance(exc, ValueError):
        return str(exc)
    if during_run and not events:
        return (
            f"The MCP server failed to start ({type(exc).__name__}: {exc}). "
            "Run `make mcp` in a terminal to see its error; if it mentions missing files, "
            "run `make data`."
        )
    noun = "The run stopped" if during_run else "The request stopped"
    return f"{noun}: {type(exc).__name__}: {exc}"


def target_dump(target) -> dict:
    return target.model_dump() if isinstance(target, Target) else dict(target)


def md(text, **kwargs) -> None:
    st.markdown(as_markdown(text), **kwargs)


def cap(text) -> None:
    st.caption(as_markdown(text))


def write(text) -> None:
    st.write(as_markdown(text))


def err(text) -> None:
    st.error(as_markdown(text))


def warn(text) -> None:
    st.warning(as_markdown(text))


def info(text) -> None:
    st.info(as_markdown(text))


def ok(text) -> None:
    st.success(as_markdown(text))


def target_phrase(target) -> str:
    data = target_dump(target)
    return (
        f"{data['sector']}, enterprise value ${data['ev_mm']:g}M, {data['geography']}, "
        f"{data['ownership']}, {data['margin_profile']} EBITDA margins"
    )


def understood_text(target: Target, defaulted: list[str]) -> str:
    line = f"I understood: {target_phrase(target)}."
    if defaulted:
        names = ", ".join(FIELD_NAMES.get(name, name) for name in defaulted)
        return f"{line} Defaulted fields: {names}."
    return f"{line} No fields were defaulted."


def _index(options: list, value) -> int:
    try:
        return list(options).index(value)
    except ValueError:
        return 0


def cited_ids(report: BuyerReport) -> list[str]:
    draft = report.draft
    found = [item.source_ids for section in guardrails.SECTIONS
             for item in getattr(draft, section)]
    found.append(draft.conviction.source_ids)
    found.extend(flag.source_ids for flag in draft.risk_flags)
    ordered = []
    for group in found:
        for tid in group:
            if tid not in ordered:
                ordered.append(tid)
    return ordered


def buyer_names(run: dict) -> dict[str, str]:
    return {report.acquirer_key: report.display_name for report in run.get("reports") or []}


def ranking_entry(run: dict, acquirer_key: str) -> dict | None:
    ranking = run.get("ranking") or {}
    for buyer in ranking.get("buyers") or []:
        if buyer.get("acquirer_key") == acquirer_key:
            return buyer
    return None


def note_tools(count: int) -> None:
    st.session_state.mcp_tool_count = count
    box = _MCP_SLOT.get("box")
    if box is not None:
        box.markdown(as_markdown(f"MCP server: {count} tools"))


def display_question(run: dict) -> str:
    """The sentence the user typed, or a description of the target when that was not stored."""
    request = (run.get("trace") or {}).get("request")
    if isinstance(request, str) and request.strip():
        return request.strip()
    return target_phrase(run["target"])


def note_mcp_event(event: dict) -> None:
    if event.get("name") != "mcp_connected" or event.get("status") != "completed":
        return
    summary = event.get("output_summary")
    count = summary.get("tools") if isinstance(summary, dict) else None
    if isinstance(count, int):
        note_tools(count)


def note_mcp_trace(trace: dict) -> None:
    for event in trace.get("events") or []:
        if event.get("name") == "mcp_connected" and event.get("status") == "completed":
            note_mcp_event(event)
            return
    tools = (trace.get("mcp") or {}).get("tools")
    if isinstance(tools, list) and tools:
        note_tools(len(tools))


# ---------- saved runs and session ----------

def init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("excluded", [])
    st.session_state.setdefault("session_runs", [])
    st.session_state.setdefault("followups", [])
    st.session_state.setdefault("mcp_tool_count", None)


def remember_run(run: dict) -> None:
    path = str(run["run_dir"])
    if path not in st.session_state.session_runs:
        st.session_state.session_runs.append(path)


def read_followups(run_dir) -> list:
    path = Path(run_dir) / "followups.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def activate_run(run: dict) -> None:
    st.session_state.current_run = run
    st.session_state.followups = read_followups(run["run_dir"])
    st.session_state.excluded = list((run.get("trace") or {}).get("excluded_keys") or [])
    st.session_state.question_run_id = run["run_id"]
    st.session_state.run_question = display_question(run)
    st.session_state.user_request = (run.get("trace") or {}).get("request")
    remember_run(run)
    note_mcp_trace(run.get("trace") or {})


def run_catalog() -> list[tuple[str, Path]]:
    """This session's runs, every saved run under outputs/runs, and the sample run."""
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(label: str, path: Path) -> None:
        path = Path(path)
        if path in seen or not (path / "reports.json").is_file():
            return
        seen.add(path)
        found.append((label, path))

    latest = guardrails.latest_run_dir(config.RUNS_DIR)
    for raw in st.session_state.session_runs:
        path = Path(raw)
        label = f"Latest run ({path.name})" if path == latest else f"Run {path.name}"
        add(label, path)
    if config.RUNS_DIR.is_dir():
        for path in sorted(p for p in config.RUNS_DIR.iterdir() if (p / "reports.json").is_file()):
            label = f"Latest run ({path.name})" if path == latest else f"Run {path.name}"
            add(label, path)
    sample = (config.SAMPLE_DIR if (config.SAMPLE_DIR / "reports.json").is_file()
              else guardrails.latest_run_dir(config.SAMPLE_DIR))
    if sample:
        add("Sample run (outputs/sample)", sample)
    return found


def queue_open(path_str: str) -> None:
    st.session_state.pending_open = path_str
    st.session_state.nav = "Research Results"


def open_saved(path_str: str) -> None:
    try:
        run = engine.load_run(path_str)
    except (OSError, ValueError, ValidationError) as exc:
        err(safe(f"Could not open that run: {exc}"))
        return
    activate_run(run)


def queue_exclude(key: str, values: dict) -> None:
    excluded = list(st.session_state.excluded)
    if key not in excluded:
        excluded.append(key)
    st.session_state.excluded = excluded
    st.session_state.pending_target = values
    st.session_state.pending_reset_excluded = False


def queue_followup(question: str) -> None:
    st.session_state.pending_followup = question


# ---------- sidebar ----------

def render_sidebar() -> str:
    st.sidebar.title("Acquirer research")
    page = st.sidebar.radio("Pages", PAGES, key="nav")
    st.sidebar.caption("Saved runs")
    catalog = run_catalog()
    if not catalog:
        st.sidebar.caption("No saved runs yet.")
    for label, path in catalog:
        st.sidebar.button(label, key=f"open-{path.name}", on_click=queue_open, args=(str(path),))
    box = st.sidebar.empty()
    _MCP_SLOT["box"] = box
    count = st.session_state.get("mcp_tool_count")
    if count:
        box.markdown(as_markdown(f"MCP server: {count} tools"))
    return page


# ---------- adjust form ----------

def render_adjust_form(values: dict, index: int) -> None:
    """Fields prefilled from the parsed target. Submitting queues a new run."""
    with st.form(f"adjust-{index}"):
        sector = st.selectbox("Sector", SECTORS, index=_index(SECTORS, values["sector"]),
                              key=f"sector-{index}")
        ev_mm = st.number_input(
            as_markdown("Enterprise value ($M)"), min_value=1.0, max_value=100_000.0,
            value=float(values["ev_mm"]), step=10.0, key=f"ev-{index}")
        geography = st.selectbox("Geography", GEOGRAPHIES,
                                 index=_index(GEOGRAPHIES, values["geography"]),
                                 key=f"geo-{index}")
        ownership = st.selectbox("Ownership", OWNERSHIPS,
                                 index=_index(OWNERSHIPS, values["ownership"]),
                                 key=f"own-{index}")
        margin = st.radio(
            "EBITDA margin", list(MARGIN_LABELS),
            index=_index(list(MARGIN_LABELS), values["margin_profile"]),
            format_func=MARGIN_LABELS.get, key=f"margin-{index}")
        submitted = st.form_submit_button("Run with these values")
    if not submitted:
        return
    proposed = {
        "sector": sector, "ev_mm": ev_mm, "geography": geography,
        "ownership": ownership, "margin_profile": margin,
    }
    try:
        Target(**proposed)
    except ValidationError as exc:
        err("; ".join(
            f"{item['loc'][0]}: {item['msg'].removeprefix('Value error, ')}" for item in exc.errors()))
        return
    st.session_state.pending_target = proposed
    st.session_state.pending_reset_excluded = True
    st.rerun()


def draw_history() -> None:
    for index, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            if message.get("kind") == "error":
                err(message["content"])
                continue
            md(message["content"])
            if message.get("target"):
                with st.expander("Adjust target", expanded=False):
                    render_adjust_form(message["target"], index)


# ---------- running the engine (display of progress only) ----------

def _parse(text: str) -> tuple[Target, list[str]]:
    with st.status("Reading the request…", expanded=False) as status:
        def progress(event: dict) -> None:
            # The message is one sentence. The full model output stays out of the live view.
            status.write(as_markdown(str(event.get("message") or event.get("name") or "")))

        target, defaulted = asyncio.run(engine.parse_target(text, progress=progress))
        status.update(label="Request read", state="complete", expanded=False)
    return target, defaulted


def _stored_request() -> str | None:
    request = st.session_state.get("user_request")
    if isinstance(request, str) and request.strip():
        return request.strip()
    return None


def _research_status(target: Target, request: str | None) -> dict | None:
    """Run the engine. Buyer lines and the timeline update in place. Full outputs stay hidden
    until the run has finished, and even then a very large output is only previewed."""
    events: list[dict] = []
    question = request or target_phrase(target)
    status = st.status("Researching acquirers…", expanded=True)
    mcp_slot = status.empty()
    lines_slot = status.container(height=220).empty()
    with st.expander("Agent Activity", expanded=False):
        live = st.empty()

    def progress(event: dict) -> None:
        events.append(event)
        note_mcp_event(event)
        mcp_line, body = buyer_status_lines(events)
        mcp_slot.markdown(as_markdown(mcp_line or "MCP server: connecting…"))
        lines_slot.markdown(as_markdown(body or "_Waiting for the first step._"))
        live.markdown(as_markdown(live_timeline_md(events, question)))

    try:
        run = asyncio.run(engine.run_engine(
            target, use_cache=True, excluded_keys=tuple(st.session_state.excluded),
            progress=progress, request=request))
    except Exception as exc:
        status.update(label="Run stopped", state="error")
        err(safe(failure_text(exc, events, during_run=True)))
        return None
    status.update(label=f"Run {run['run_id']} finished", state="complete", expanded=False)
    activate_run(run)
    with live.container():
        render_timeline(
            (run.get("trace") or {}).get("events") or [],
            question=display_question(run),
            totals=(run.get("trace") or {}).get("totals") or {},
            key_prefix=f"run-{run['run_id']}",
            names=buyer_names(run),
            scrub=safe,
        )
    return run


def run_from_text(text: str) -> bool:
    """Show the user bubble, the parsed target, then the live run. True if a timeline was drawn."""
    st.session_state.excluded = []
    st.session_state.messages.append({"role": "user", "content": text})
    with st.chat_message("user"):
        md(text)
    with st.chat_message("assistant"):
        blocked = key_error_text()
        if blocked:
            err(blocked)
            st.session_state.messages.append(
                {"role": "assistant", "kind": "error", "content": blocked})
            return False
        try:
            target, defaulted = _parse(text)
        except Exception as exc:
            message = safe(failure_text(exc, [], during_run=False))
            err(message)
            st.session_state.messages.append(
                {"role": "assistant", "kind": "error", "content": message})
            return False
        understood = understood_text(target, defaulted)
        md(understood)
        record = {
            "role": "assistant", "kind": "parse", "content": understood,
            "target": target.model_dump(), "defaulted": list(defaulted),
        }
        st.session_state.messages.append(record)
        with st.expander("Adjust target", expanded=False):
            render_adjust_form(record["target"], len(st.session_state.messages) - 1)
        st.session_state.user_request = text
        run = _research_status(target, text)
        return run is not None


def run_from_target(values: dict) -> bool:
    blocked = key_error_text()
    if blocked:
        err(blocked)
        return False
    try:
        target = Target.model_validate(values)
    except ValidationError as exc:
        err(safe(str(exc)))
        return False
    note = f"Running with these values: {target_phrase(target)}."
    st.session_state.messages.append({"role": "assistant", "kind": "note", "content": note})
    with st.chat_message("assistant"):
        md(note)
        run = _research_status(target, _stored_request())
        return run is not None


def take_pending_target() -> bool:
    values = st.session_state.pop("pending_target", None)
    reset = st.session_state.pop("pending_reset_excluded", False)
    st.session_state.pop("pending_question", None)
    if not values:
        return False
    if reset:
        st.session_state.excluded = []
    return run_from_target(values)


# ---------- results ----------

def results_table(run: dict) -> pd.DataFrame:
    rows = []
    for report in run["reports"]:
        rows.append({
            "Rank": report.rank,
            "Buyer": report.display_name,
            "Type": report.acquirer_type,
            "Conviction": report.draft.conviction.level,
            "Status": "Verified" if report.status == "verified" else "Not verified",
            "Reason": clip(report.draft.conviction.reason, 140),
        })
    return pd.DataFrame(rows)


def chosen_report(run: dict, table: pd.DataFrame) -> BuyerReport:
    """The dataframe selection when there is one, otherwise the selectbox."""
    reports = run["reports"]
    labels = [f"{report.rank}. {report.display_name}" for report in reports]
    state = st.dataframe(
        table, hide_index=True, on_select="rerun", selection_mode="single-row",
        key=f"rank-{run['run_id']}",
    )
    picked = None
    selection = getattr(state, "selection", None)
    rows = list(getattr(selection, "rows", []) or [])
    if rows and rows[0] < len(reports):
        picked = rows[0]
    choice = st.selectbox("Buyer details", labels, key=f"buyer-{run['run_id']}")
    if picked is None:
        picked = labels.index(choice) if choice in labels else 0
    return reports[picked]


def show_transaction_evidence(report: BuyerReport) -> None:
    ids = cited_ids(report)
    write("Cited transaction IDs: " + (", ".join(ids) or "none"))
    if not ids:
        return
    frame = get_client().transactions
    known = set(frame["transaction_id"])
    order = {tid: position for position, tid in enumerate(ids)}
    found = frame[frame["transaction_id"].isin(ids)].copy()
    if not found.empty:
        found["_cited_order"] = found["transaction_id"].map(order)
        found = found.sort_values("_cited_order").drop(columns="_cited_order")
        st.dataframe(found, hide_index=True)
    missing = [tid for tid in ids if tid not in known]
    if missing:
        warn("Not in the transaction table: " + ", ".join(missing))


def show_valuation_and_risks(report: BuyerReport) -> None:
    draft = report.draft
    md("**Valuation context**")
    if not draft.valuation_context:
        cap("This report has no valuation statements.")
    for statement in draft.valuation_context:
        tags = " ".join(f"[{tid}]" for tid in statement.source_ids)
        md(f"- {statement.text}" + (f" {tags}" if tags else ""))
    md("**Risk flags**")
    for flag in draft.risk_flags:
        tags = " ".join(f"[{tid}]" for tid in flag.source_ids)
        md(f"- **{flag.risk}:** {flag.explanation}" + (f" {tags}" if tags else ""))


def show_score(entry: dict | None) -> None:
    if entry is None:
        info("This saved run has no ranking.json entry for this buyer, so the score "
             "breakdown is not available.")
        return
    score = (entry.get("score") or {}).get("display")
    starting = entry.get("starting_conviction") or {}
    write(
        f"Score **{score}** · score rank {entry.get('score_rank')} · "
        f"starting conviction **{starting.get('level')}**"
    )
    if starting.get("reason"):
        cap(starting["reason"])
    st.dataframe(pd.DataFrame([
        {
            "Signal": signal.get("name"),
            "Value": signal.get("value_display") or "—",
            "Weight": signal.get("weight_display") or "—",
            "Contribution": signal.get("contribution_display") or "—",
            "Explanation": signal.get("explanation") or "",
        }
        for signal in entry.get("signals") or []
    ]), hide_index=True)


def show_buyer(report: BuyerReport, run: dict) -> None:
    if report.status != "verified":
        err(
            "**Not verified.** This report did not pass the guardrail checks:\n\n"
            + "\n".join(f"- {issue}" for issue in report.issues)
        )
    if report.selection_note:
        cap(report.selection_note)
    tabs = st.tabs([
        "Report", "Transaction evidence", "Valuation context & Risk flags",
        "Score breakdown", "Agent activity",
    ])
    with tabs[0]:
        md(guardrails.render_markdown(report))
    with tabs[1]:
        show_transaction_evidence(report)
    with tabs[2]:
        show_valuation_and_risks(report)
    with tabs[3]:
        show_score(ranking_entry(run, report.acquirer_key))
    with tabs[4]:
        render_timeline(
            (run.get("trace") or {}).get("events") or [],
            question=display_question(run),
            totals=(run.get("trace") or {}).get("totals") or {},
            key_prefix=f"buyer-{run['run_id']}-{report.acquirer_key}",
            names=buyer_names(run),
            buyer=report.acquirer_key,
            scrub=safe,
        )
    values = target_dump(run["target"])
    st.button(
        "Not relevant",
        key=f"exclude-{report.acquirer_key}",
        on_click=queue_exclude,
        args=(report.acquirer_key, values),
        help="Run the same target again without this buyer.",
    )


def show_downloads(run: dict) -> None:
    folder = Path(run["run_dir"])
    columns = st.columns(3)
    files = (
        ("reports.md", "text/markdown"),
        ("reports.json", "application/json"),
        ("trace.json", "application/json"),
    )
    for column, (name, mime) in zip(columns, files):
        path = folder / name
        if path.is_file():
            column.download_button(
                f"Download {name}", path.read_bytes(), file_name=name, mime=mime,
                key=f"dl-{run['run_id']}-{name}",
            )


def show_run_notes(run: dict) -> None:
    trace = run.get("trace") or {}
    reports = run["reports"]
    st.subheader(f"Run {run['run_id']}")
    cap(f"Target: {target_phrase(run['target'])} · Folder: {run['run_dir']}")
    if len(reports) < config.REPORT_COUNT:
        warn(f"Only {len(reports)} of {config.REPORT_COUNT} reports were produced.")
    failed = [report.display_name for report in reports if report.status != "verified"]
    if failed:
        warn(f"{len(failed)} report(s) did not pass the guardrail checks: " + ", ".join(failed))
    for error in trace.get("errors") or []:
        warn(f"Buyer {error.get('acquirer_key')} failed: {safe(str(error.get('error')))}")
    for sub in trace.get("substitutions") or []:
        info(
            f"{sub.get('replacement')} replaced {sub.get('failed')} "
            f"(shortlist rank {sub.get('replacement_shortlist_rank')})."
        )
    if trace.get("excluded_keys"):
        info("Marked not relevant and excluded: " + ", ".join(trace["excluded_keys"]))


def show_results(run: dict, *, full_timeline: bool) -> None:
    show_run_notes(run)
    if not run["reports"]:
        warn("This run has no reports.")
        return
    table = results_table(run)
    report = chosen_report(run, table)
    show_buyer(report, run)
    show_downloads(run)
    if full_timeline:
        st.subheader("Agent Activity")
        with st.expander("Agent Activity", expanded=False):
            render_timeline(
                (run.get("trace") or {}).get("events") or [],
                question=display_question(run),
                totals=(run.get("trace") or {}).get("totals") or {},
                key_prefix=f"activity-{run['run_id']}",
                names=buyer_names(run),
                scrub=safe,
            )
    show_followups(run)


# ---------- follow-up chat ----------

def _followup_dict(item) -> dict:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return item


def followup_body(data: dict, index: int, run_id: str) -> None:
    status = data.get("status")
    if status == "verified":
        ok("Verified")
    else:
        issues = data.get("issues") or []
        detail = "\n".join(f"- {issue}" for issue in issues)
        err("Not verified" + (f"\n\n{detail}" if detail else ""))
    if data.get("markdown"):
        md(data["markdown"])
    with st.expander("Follow-up activity", expanded=False):
        render_timeline(
            data.get("events") or [],
            question=data.get("question"),
            totals=data.get("totals") or {},
            key_prefix=f"fu-{run_id}-{index}",
            scrub=safe,
        )


def render_followup(item, index: int, run_id: str) -> None:
    data = _followup_dict(item)
    with st.chat_message("user"):
        md(data.get("question") or "")
    with st.chat_message("assistant"):
        followup_body(data, index, run_id)


def run_followup_ui(question: str, run: dict) -> None:
    events: list[dict] = []
    with st.chat_message("user"):
        md(question)
    with st.chat_message("assistant"):
        status = st.status("Answering the follow-up…", expanded=True)
        mcp_slot = status.empty()
        lines_slot = status.container(height=180).empty()

        def progress(event: dict) -> None:
            events.append(event)
            note_mcp_event(event)
            mcp_line, body = buyer_status_lines(events)
            mcp_slot.markdown(as_markdown(mcp_line or "MCP server: connecting…"))
            lines_slot.markdown(as_markdown(body or "_Waiting for the first step._"))

        try:
            asyncio.run(engine.run_followup(question, run, use_cache=True, progress=progress))
        except Exception as exc:
            status.update(label="Follow-up stopped", state="error")
            err(safe(failure_text(exc, events, during_run=True)))
            return
        status.update(label="Follow-up finished", state="complete", expanded=False)
        st.session_state.followups = read_followups(run["run_dir"])
        if not st.session_state.followups:
            err("The follow-up finished but it was not saved.")
            return
        followup_body(st.session_state.followups[-1], len(st.session_state.followups) - 1,
                      run["run_id"])


def show_followups(run: dict) -> None:
    st.subheader("Follow-up questions")
    blocked = key_error_text()
    if blocked:
        info(blocked)
    pending = st.session_state.pop("pending_followup", None)
    for index, label in enumerate(FOLLOWUP_EXAMPLES):
        st.button(label, key=f"followup-ex-{index}", on_click=queue_followup, args=(label,),
                  disabled=blocked is not None)
    with st.container():
        typed = st.chat_input(
            "Ask a follow-up about this run", key="followup_chat", disabled=blocked is not None)
    if typed and typed.strip():
        pending = typed.strip()
    saved = list(st.session_state.followups)
    for index, item in enumerate(saved):
        render_followup(item, index, run["run_id"])
    if pending and not blocked:
        run_followup_ui(pending, run)


# ---------- pages ----------

def page_new() -> None:
    st.title("M&A Acquirer Identification")
    cap(
        "Describe the company you want to sell. The engine ranks the 10 most likely "
        "acquirers from 500 historical deals and writes a source-cited report for each."
    )
    md("**Example request**")
    md(EXAMPLE)
    use_example = st.button("Use this example", key="use_example")
    prompt = st.chat_input("Describe the company you want to sell", key="research_chat")
    draw_history()
    ran = False
    if use_example or (prompt and prompt.strip()):
        st.session_state.pop("pending_target", None)
        st.session_state.pop("pending_reset_excluded", None)
        ran = run_from_text(EXAMPLE if use_example else prompt.strip())
    else:
        ran = take_pending_target()
    run = st.session_state.get("current_run")
    if run:
        show_results(run, full_timeline=not ran)
    elif not st.session_state.messages:
        info("Send a request to start. Saved runs open from the sidebar without an API key.")


def page_results() -> None:
    st.title("Research Results")
    ran = take_pending_target()
    run = st.session_state.get("current_run")
    if not run:
        info("No run is open yet. Start one from New Research, or open a saved run in the sidebar.")
        return
    show_results(run, full_timeline=not ran)


def page_about() -> None:
    st.title("About")
    md(
        "This tool reads a file of historical deals and names the buyers most likely to "
        "acquire a company you describe. It then writes a one-page report for each of the "
        "top 10, with every fact tied back to a deal in the file."
    )
    st.subheader("Rules")
    md(
        "- The AI never calculates. Every number is worked out in Python and handed to the "
        "model as a ready-to-quote string.\n"
        "- Every fact cites transaction IDs from the dataset.\n"
        "- A report or answer that fails the checks is shown as **Not verified**, with the "
        "issues listed. Nothing is hidden or quietly passed.\n"
        "- The model may only talk about what is in this dataset. If the data cannot answer, "
        "the report says unknown."
    )
    st.subheader("Data")
    md(
        "The source file is `data/raw/ma_transactions_500.csv` (500 deals). "
        "`make data` writes the cleaned tables under `data/processed/`. "
        "The screen only looks those tables up. It does not recompute them."
    )
    st.subheader("Model")
    model = config.LLM_MODEL or "not set (add LLM_MODEL to .env)"
    md(f"Model: `{model}`")
    cap("The API key stays in `.env` and is never shown here.")
    st.subheader("Read more")
    md("[README](README.md)")
    md("[Architecture](docs/architecture.md)")
    cap("Those files are `README.md` and `docs/architecture.md` in the project folder.")


def main() -> None:
    st.set_page_config(
        page_title="M&A Acquirer Identification",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_state()
    page = render_sidebar()
    missing = missing_files()
    if missing:
        err(
            "Data files are missing: " + ", ".join(path.name for path in missing)
            + ". Run `make data` first (it needs the raw CSV in data/raw/)."
        )
        st.stop()
    pending_open = st.session_state.pop("pending_open", None)
    if pending_open:
        open_saved(pending_open)
    if page == "New Research":
        page_new()
    elif page == "Research Results":
        page_results()
    else:
        page_about()


main()
