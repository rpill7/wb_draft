"""Streamlit entry point: page setup and top-level orchestration."""


from ma_engine.research import jobs
import streamlit as st
from ma_engine.ui.chat import (
    page_compose,
    render_chat,
    render_job,
)
from ma_engine.ui.common import (
    err,
    missing_files,
)
from ma_engine.ui.navigation import (
    open_saved,
    publish_route,
    restore_route,
    watch_job,
)
from ma_engine.ui.report_panel import (
    render_report_panel,
)
from ma_engine.ui.sessions import (
    init_state,
)
from ma_engine.ui.sidebar import (
    maybe_confirm_delete,
    page_about,
    page_history,
    render_sidebar,
)
from ma_engine.ui.styles import (
    PAGE_CSS,
    install_cite_popover,
)


def show_workspace(run: dict) -> None:
    """Chat alone, or chat and the buyer panel side by side. Each scrolls on its own."""
    if st.session_state.get("pending_target"):
        st.session_state.panel_open = False
        st.session_state.selected_buyer = None
    if not st.session_state.panel_open:
        render_chat(run)
        return
    chat, panel = st.columns([1, 1.25], gap="medium")
    with chat:
        render_chat(run)
    with panel, st.container(key="panel"):
        render_report_panel(run)


def main() -> None:
    st.set_page_config(
        page_title="M&A Research Assistant",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    install_cite_popover()
    init_state()
    missing = missing_files()
    if not missing:
        restore_route()
    if st.session_state.get("route_notice"):
        st.warning(st.session_state.pop("route_notice"))
    pending_open = st.session_state.pop("pending_open", None)
    if pending_open and not missing:
        open_saved(pending_open)
    render_sidebar()
    if missing:
        err(
            "Data files are missing: " + ", ".join(path.name for path in missing)
            + ". Run `make data` first (it needs the raw CSV in data/raw/)."
        )
        maybe_confirm_delete()
        st.stop()
    if st.query_params.get("job"):
        render_job(st.query_params["job"])
        maybe_confirm_delete()
        return
    current = st.session_state.get("current_run")
    active = jobs.active_for(current['run_id']) if current else None
    if active and st.session_state.screen == "research" and not st.session_state.composing:
        watch_job(active)
    pages = {"about": page_about, "history": page_history}
    if st.session_state.screen in pages:
        pages[st.session_state.screen]()
    elif st.session_state.composing or not st.session_state.get("current_run"):
        if page_compose():
            st.rerun()
    else:
        show_workspace(st.session_state.current_run)
    maybe_confirm_delete()
    publish_route()


if __name__ == "__main__":
    main()
