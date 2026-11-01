"""Sidebar, History/About pages and deletion confirmation."""


from pathlib import Path
from ma_engine.ui.activity import as_markdown
from ma_engine import config
from ma_engine.research import jobs
import streamlit as st
from ma_engine.persistence import submissions
from ma_engine.ui.common import (
    cap,
    err,
    md,
    safe,
)
from ma_engine.ui.navigation import (
    _go_screen,
    _new_research,
    queue_open,
    select_job,
)
from ma_engine.ui.sessions import (
    can_delete_run,
    delete_research_session,
    forget_deleted_run,
    run_catalog,
    run_label,
    session_identity,
    sidebar_run_text,
)


DELETE_QUESTION = (
    "Delete this research session and its saved reports, follow-ups and activity?"
)


def _cancel_delete() -> None:
    st.session_state.pending_delete = None


@st.dialog("Delete", on_dismiss=_cancel_delete)
def confirm_delete_dialog(run_id: str) -> None:
    submission_id = run_id.removeprefix("submission:") if run_id.startswith("submission:") else None
    if submission_id:
        item = next((item for item in submissions.unfinished() if item["id"] == submission_id), None)
        title, when = (item["question"], "Unfinished request") if item else ("Saved request", "")
    else:
        title, when = session_identity(run_id)
    md(f"**{title}**")
    st.caption(when)
    md("Delete this unfinished request?" if submission_id else DELETE_QUESTION)
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        cancelled = st.button("Cancel", key="delete-cancel", type="tertiary", width="content")
        confirmed = st.button("Delete", key="delete-confirm", type="primary", width="content")
    if cancelled:
        st.session_state.pending_delete = None
        st.rerun()
    if not confirmed:
        return
    if (submission_id and (jobs.snapshot(submission_id) or {}).get('status') == 'running') or (
            not submission_id and jobs.active_for(run_id)):
        err("This request is still running. Wait until it finishes before deleting it.")
        return
    try:
        if submission_id:
            submissions.delete(submission_id)
        else:
            delete_research_session(run_id)
    except ValueError as exc:
        err(safe(str(exc)))
        return
    if submission_id:
        st.session_state.pending_delete = None
    else:
        forget_deleted_run(run_id)
    st.rerun()


def maybe_confirm_delete() -> None:
    run_id = st.session_state.get("pending_delete")
    if isinstance(run_id, str) and run_id:
        confirm_delete_dialog(run_id)


def _ask_delete(run_id: str) -> None:
    st.session_state.pending_delete = run_id


def research_actions(path: Path, key: str) -> None:
    """A red delete icon at the right of the summary. It opens the confirmation and does not
    open the session."""
    if not can_delete_run(path):
        return
    st.button("Delete", key=key, type="tertiary", icon=":material/delete:",
              help="Delete", width="content", on_click=_ask_delete, args=(path.name,))


def render_sidebar() -> None:
    """New research, the recent runs, and About. Run status and exports live on the page."""
    with st.sidebar.container(key="sidebar-head"):
        st.markdown(
            '<div class="product"><div class="product-title">M&amp;A Research</div>'
            '<div class="product-sub">Assistant</div></div>',
            unsafe_allow_html=True)
        st.button("New Research", key="nav-new", type="primary", icon=":material/add:",
                  width="stretch", on_click=_new_research)
    run = st.session_state.get("current_run")
    recent = sorted((path for _label, path in run_catalog()), key=lambda path: path.name,
                    reverse=True)
    active = str(run["run_dir"]) if run else None
    with st.sidebar.container(key="sidebar-recent"):
        st.markdown('<div class="side-label">Recent research</div>', unsafe_allow_html=True)
        with st.container(key="sidebar-list"):
            unfinished = [item for item in submissions.unfinished() if not item.get("run_id")]
            if not recent and not unfinished:
                st.caption("No saved runs yet.")
            for item in unfinished:
                with st.container(key=f"recent-row-submission-{item['id']}"):
                    with st.container(key=f"recent-off-submission-{item['id']}"):
                        st.button(f"**{as_markdown(item['question'])}**\nUnfinished request",
                                  key=f"submission-sidebar-{item['id']}", type="tertiary",
                                  width="stretch", help=item["question"], wrap=True,
                                  on_click=select_job, args=(item["id"],))
                    st.button("Delete", key=f"menu-submission-{item['id']}", type="tertiary",
                              icon=":material/delete:", help="Delete", width="content",
                              on_click=_ask_delete, args=(f"submission:{item['id']}",))
            for path in recent:
                label, hover = sidebar_run_text(path)
                state = "recent-on" if str(path) == active else "recent-off"
                if can_delete_run(path):
                    with st.container(key=f"recent-row-{path.name}"):
                        with st.container(key=f"{state}-{path.name}"):
                            st.button(label, key=f"recent-{path.name}", type="tertiary",
                                      width="stretch", help=hover, wrap=True,
                                      on_click=queue_open, args=(str(path),))
                        research_actions(path, f"menu-{path.name}")
                else:
                    with st.container(key=f"{state}-{path.name}"):
                        st.button(label, key=f"recent-{path.name}", type="tertiary",
                                  width="stretch", help=hover, wrap=True,
                                  on_click=queue_open, args=(str(path),))
    with st.sidebar.container(key="sidebar-foot"):
        st.button("History", key="nav-history", type="tertiary",
                  icon=":material/history:", width="stretch",
                  on_click=_go_screen, args=("history",))
        st.button("About", key="nav-about", type="tertiary", icon=":material/info:",
                  width="stretch", on_click=_go_screen, args=("about",))


def page_about() -> None:
    st.header("About")
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


def page_history() -> None:
    st.header("Research History")
    pending = submissions.unfinished()
    if pending:
        st.subheader("Unfinished requests")
        for item in pending:
            with st.container(border=True):
                name, action = st.columns([4, 1], vertical_alignment="center")
                with name:
                    md(item["question"])
                with action:
                    st.button("Delete", key=f"history-menu-submission-{item['id']}",
                              type="tertiary", icon=":material/delete:", help="Delete",
                              width="content", on_click=_ask_delete,
                              args=(f"submission:{item['id']}",))
                st.button("View request", key=f"watch-{item['id']}",
                          on_click=select_job, args=(item['id'],))
                state = jobs.snapshot(item['id']) or item
                cap("Processing continues in the background. View the request to see live activity."
                    if state['status'] == 'running' else
                    "Processing was interrupted. View the request to retry; it will not restart automatically.")
                if item.get("run_id"):
                    st.button("Open research", key=f"submission-{item['id']}",
                              on_click=queue_open,
                              args=(str(config.RUNS_DIR / item["run_id"]),))
                else:
                    cap("View the request to check its status or retry.")
    catalog = run_catalog()
    if not catalog:
        cap("No saved runs yet.")
        return
    for _label, path in sorted(catalog, key=lambda item: item[1].name, reverse=True):
        with st.container(border=True):
            name, action = st.columns([4, 1], vertical_alignment="center")
            name.markdown(as_markdown(f"**{run_label(path)}**"))
            with action.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.button("Open", key=f"open-{path.name}", on_click=queue_open, args=(str(path),))
                research_actions(path, f"history-menu-{path.name}")
