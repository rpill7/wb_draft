"""Research submission, refresh recovery and follow-up conversation UI."""


from types import SimpleNamespace
from ma_engine.models import Target
from pydantic import ValidationError
from ma_engine.ui.reports import buyer_evidence_line
from ma_engine import config
from ma_engine.ui.reports import conviction_level
from ma_engine.research import engine
from ma_engine.ui.reports import executive_summary_html
from ma_engine.research import jobs
from ma_engine.ui.activity import live_label
from ma_engine.ui.reports import source_index
import streamlit as st
from ma_engine.ui.reports import statement_html
from ma_engine.ui.reports import status_text
from ma_engine.persistence import submissions
from ma_engine.ui.reports import target_sentence
from ma_engine.ui.activity_view import (
    event_names,
    render_activity,
    render_completion,
)
from ma_engine.ui.common import (
    ASSISTANT_AVATAR,
    buyer_names,
    cap,
    deals_analysed,
    display_question,
    err,
    info,
    key_error_text,
    md,
    ordered_reports,
    ranking_entry,
    safe,
    show_html,
    slug,
    target_dump,
    warn,
)
from ma_engine.ui.navigation import (
    _back_to_run,
    _open_full_report,
    _select_buyer,
    select_job,
    watch_job,
)
from ma_engine.ui.sessions import (
    activate_run,
    catalog_path,
)
from ma_engine.ui.target_form import (
    draw_history,
    render_request_card,
)


MARGIN_SHORT = {"strong": "Strong", "any": "Any"}


FIELD_NAMES = {
    "sector": "sector",
    "ev_mm": "enterprise value",
    "ownership": "ownership",
    "geography": "geography",
    "margin_profile": "EBITDA margin",
}


LABEL_ESCAPES = set("\\`*_[]<>#:")


BADGE_COLORS = {"High": "green", "Medium": "orange", "Low": "gray",
                "Verified": "green", "Not verified": "orange", "Failed": "red"}


EXAMPLE = (
    "Find the 10 most likely acquirers for a regional Healthcare Services company "
    "valued at $200M EV with strong EBITDA margins."
)


def _stored_request() -> str | None:
    request = st.session_state.get("user_request")
    if isinstance(request, str) and request.strip():
        return request.strip()
    return None


def assumed_text(target: Target, defaulted: list[str]) -> str:
    """The fields the request did not state, with the value used. Empty when none were assumed."""
    values = target.model_dump()
    shown = {"margin_profile": MARGIN_SHORT.get(values["margin_profile"])}
    parts = [f"{FIELD_NAMES.get(name, name)} {shown.get(name, values.get(name))}"
             for name in defaulted]
    return f"Not stated in the request, so assumed: {', '.join(parts)}." if parts else ""


def run_from_text(text: str) -> bool:
    watch_job(jobs.submit(text))
    return False


@st.fragment(run_every=1)
def render_job(token: str) -> None:
    try:
        item = jobs.snapshot(token)
    except ValueError:
        item = None
    if not item:
        st.warning("This request is unavailable or was deleted.")
        return
    render_request_card(item['question'])
    assumption = (assumed_text(Target.model_validate(item['target']), item['defaulted'])
                  if item.get('defaulted') else '')
    if item['status'] == 'completed' or (item['status'] == 'interrupted' and item.get('run_id')):
        path = catalog_path(item['run_id'])
        if path is None or path.is_symlink():
            st.warning('The saved research was deleted or is unavailable.')
            return
        activate_run(engine.load_run(str(path)))
        st.query_params.from_dict({"run": item['run_id']})
        st.session_state._route_seen = None
        st.rerun(scope="app")
    elif item['status'] == 'running':
        events = item.get('events', [])
        render_activity(events, None, f"job-{token}", event_names(events),
                        running=True, context_note=assumption)
        current_call = live_label(events, event_names(events))
        if current_call:
            cap(current_call)
    else:
        if assumption:
            cap(assumption)
        st.error("This request was interrupted. It has not been automatically resubmitted.")
        if item.get('error'):
            err(safe(item['error']))
        if st.button("Retry", key=f"retry-{token}"):
            run = engine.load_run(str(config.RUNS_DIR / item['run_id'])) if item.get('run_id') else None
            watch_job(jobs.submit(item['question'], run,
                                  target=Target.model_validate(item['target']) if item.get('target') else None,
                                  excluded=item.get('excluded', ())))


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
    watch_job(jobs.submit(_stored_request() or target_sentence(target), target=target,
                          excluded=tuple(st.session_state.excluded)))
    return False


def take_pending_target() -> bool:
    values = st.session_state.pop("pending_target", None)
    reset = st.session_state.pop("pending_reset_excluded", False)
    st.session_state.pop("pending_question", None)
    if not values:
        return False
    if reset:
        st.session_state.excluded = []
    return run_from_target(values)


def _label_text(text) -> str:
    """Text for a button label, with Markdown control characters escaped."""
    return "".join(f"\\{ch}" if ch in LABEL_ESCAPES else ch for ch in str(text))


def buyer_card_label(report, entry: dict | None = None) -> str:
    """Rank and name, then what kind of buyer this is, how strong the case is, and
    whether the written report passed the checks."""
    level = conviction_level(report)
    status = status_text(report.status)
    return (f"**{report.rank}. {_label_text(report.display_name)}**  \n"
            f"Buyer type: {_label_text(report.acquirer_type)} · "
            f":{BADGE_COLORS.get(level, 'gray')}-badge[{level} conviction] "
            f":{BADGE_COLORS.get(status, 'orange')}-badge[{status} report]"
            + (f"  \n{_label_text(buyer_evidence_line(entry))}" if entry else ""))


def render_top10(run: dict) -> None:
    """All buyers as compact cards. Each card is one button that opens that buyer's report."""
    reports = ordered_reports(run)
    title = "Top 10 Potential Acquirers" if len(reports) == 10 else "Potential Acquirers"
    selected = st.session_state.get("selected_buyer")
    with st.container(horizontal=True, vertical_alignment="center", key="shortlist-head"):
        st.markdown(f'<div class="card-title">{title}</div>', unsafe_allow_html=True)
        st.button("Open full report", key="open-full-report", type="tertiary",
                  icon=":material/article:", on_click=_open_full_report,
                  help="All buyers in one combined report", width="content")
    with st.container(key="shortlist-area"), st.container(key="shortlist"):
        for report in reports:
            part = slug(report.acquirer_key)
            card_key = f"card-sel-{part}" if report.acquirer_key == selected else f"card-{part}"
            with st.container(key=card_key):
                st.button(buyer_card_label(report, ranking_entry(run, report.acquirer_key)), key=f"top-{run['run_id']}-{part}",
                          on_click=_select_buyer, args=(report.acquirer_key,), width="stretch")
    alternatives = (run.get("ranking") or {}).get("strategic_alternatives") or []
    if alternatives:
        with st.expander("Strategic alternatives outside the top 10"):
            cap("Scoring-only alternatives; full reports were not generated for these buyers.")
            for item in alternatives:
                md(f"**{item['display_name']}** · score rank {item['score_rank']} · "
                   f"{buyer_evidence_line(item)}")
    excluded = st.session_state.get("excluded") or []
    if excluded:
        names = buyer_names(run)
        cap("Marked not relevant and excluded: " + ", ".join(names.get(key, key) for key in excluded))


def render_answer(run: dict) -> None:
    """The executive summary, built from the run's checked reports."""
    show_html(executive_summary_html(run, source_index(run), deals_analysed()))


def render_middle(run: dict, pending: str | None = None) -> None:
    render_request_card(display_question(run), target_dump(run["target"]))
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        render_completion(run)
        render_answer(run)
        if len(run.get("reports") or []) < config.REPORT_COUNT:
            warn(f"Only {len(run['reports'])} of {config.REPORT_COUNT} reports were produced.")
        render_top10(run)
    show_followups(run, pending)


def _followup_dict(item) -> dict:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return item


def followup_body(data: dict, index: int, run: dict, activity: bool = True) -> None:
    """The follow-up's activity row (unless the live one is already on screen), then the answer."""
    if activity:
        render_activity(data.get("events") or [], data.get("totals"),
                        f"fu-{run['run_id']}-{index}", buyer_names(run), response_totals=True)
    status = data.get("status")
    label = "Verified" if status == "verified" else "Not verified"
    md(f"**Status:** {label}")
    issues = data.get("issues") or []
    if status != "verified":
        cap(f"{len(issues)} check(s) did not pass — see the expander below.")
    statements = data.get("statements") or []
    if statements:
        sources = source_index(run)
        parts = []
        for statement in statements:
            if isinstance(statement, dict):
                statement = SimpleNamespace(text=statement.get("text") or "",
                                            kind=statement.get("kind"),
                                            source_ids=statement.get("source_ids") or [])
            parts.append(statement_html(statement, sources))
        show_html(f'<div class="para">{" ".join(parts)}</div>')
    elif issues:
        md("The model did not return a usable answer. Please retry the question. "
           "Details are available below.")
    elif data.get("markdown"):
        md(data["markdown"])
    if data.get("source_catalog"):
        with st.expander("Report sources", expanded=True):
            st.dataframe(data["source_catalog"], hide_index=True, width="stretch")
    if issues:
        with st.expander("Follow-up diagnostics"):
            for issue in issues:
                md(f"- {issue}")


def render_followup(item, index: int, run: dict) -> None:
    data = _followup_dict(item)
    with st.chat_message("user"):
        md(data.get("question") or "")
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        followup_body(data, index, run)


def render_stopped_followup(item: dict, number: int, run: dict) -> None:
    """A follow-up that raised an error: its question, partial activity and the error."""
    with st.chat_message("user"):
        md(item["question"])
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        render_activity(item["events"], None, f"fu-stopped-{run['run_id']}-{number}",
                        buyer_names(run), failed=True)
        err(item["error"])


def run_followup_ui(question: str, run: dict) -> None:
    try:
        watch_job(jobs.submit(question, run))
    except jobs.BusyRunError as exc:
        st.warning(str(exc))


def show_followups(run: dict, pending: str | None) -> None:
    blocked = key_error_text()
    if blocked:
        info(blocked)
    stopped = [item for item in st.session_state.stopped_followups
               if item["run_id"] == run["run_id"]]
    for index, item in enumerate(st.session_state.followups):
        for number, failure in enumerate(stopped):
            if failure["position"] == index:
                render_stopped_followup(failure, number, run)
        render_followup(item, index, run)
    for number, failure in enumerate(stopped):
        if failure["position"] >= len(st.session_state.followups):
            render_stopped_followup(failure, number, run)
    for number, item in enumerate(submissions.unfinished()):
        if item.get("run_id") == run["run_id"] and not any(
                failure["question"] == item["question"] for failure in stopped):
            state = jobs.snapshot(item['id']) or item
            if state['status'] == 'interrupted':
                render_stopped_followup({"question": item['question'],
                    "events": state.get('events', []),
                    "error": state.get('error') or "Processing was interrupted. You can retry this question."},
                    number, run)
                if st.button("Retry", key=f"retry-{item['id']}"):
                    run_followup_ui(item['question'], run)
            else:
                cap("A follow-up is running. Open its activity to reconnect.")
                st.button("View request", key=f"watch-{item['id']}",
                          on_click=select_job, args=(item['id'],))
    if pending and not blocked:
        run_followup_ui(pending, run)


def followup_input(run: dict) -> str | None:
    """The one chat box, under the chat column's scroll area."""
    reports = ordered_reports(run)
    hint = (f'Ask a follow-up question (e.g., "Why is {reports[0].display_name} ranked first?")'
            if reports else "Ask a follow-up question about this run")
    typed = st.chat_input(hint, key="followup_chat", disabled=key_error_text() is not None)
    return typed.strip() if typed and typed.strip() else None


def chat_frame():
    """The chat column: a scroll area, with the one chat box placed under it by the caller.

    Closed panel: the column is centred at a reading width. Open panel: it fills its column.
    """
    frame = st.container(key="chat-beside" if st.session_state.panel_open else "chat-area")
    with frame:
        scroll = st.container(key="chat-scroll")
    return frame, scroll


def page_compose() -> bool:
    """The request box. Returns True when a run has just finished."""
    frame, scroll = chat_frame()
    with frame:
        prompt = st.chat_input("Describe the company you want to sell", key="research_chat")
    with scroll:
        if st.session_state.get("current_run"):
            st.button("Back to the current run", key="back-to-run", type="tertiary",
                      icon=":material/arrow_back:", on_click=_back_to_run)
        deals = deals_analysed()
        source = f"{deals} historical deals" if isinstance(deals, int) else "the historical deals"
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            md(f"**Hello.** Describe the company you want to sell. I rank the most likely "
               f"buyers from {source} and write a source-cited report for each.")
            cap("Try this request")
            md(f"> {EXAMPLE}")
            use_example = st.button("Use this example", key="use_example",
                                    icon=":material/arrow_upward:")
        draw_history()
        if use_example or (prompt and prompt.strip()):
            st.session_state.pop("pending_target", None)
            st.session_state.pop("pending_reset_excluded", None)
            return run_from_text(EXAMPLE if use_example else prompt.strip())
        return take_pending_target()


def render_chat(run: dict) -> None:
    frame, scroll = chat_frame()
    with frame:
        pending = followup_input(run)
    with scroll:
        if take_pending_target():
            st.rerun()
        render_middle(run, pending)
