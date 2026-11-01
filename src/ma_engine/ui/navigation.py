"""URL routing and UI navigation callbacks. Never starts research."""


from pydantic import ValidationError
from ma_engine.research import engine
import streamlit as st
from ma_engine.ui.common import (
    err,
    ordered_reports,
    safe,
)
from ma_engine.ui.sessions import (
    _plain_run_id,
    activate_run,
    catalog_path,
    clear_active_session,
)


def queue_open(path_str: str) -> None:
    st.query_params.pop("job", None)
    st.session_state._route_seen = dict(st.query_params)
    st.session_state.pending_open = path_str
    st.session_state.screen = "research"
    st.session_state.composing = False


def open_saved(path_str: str) -> None:
    try:
        run = engine.load_run(path_str)
    except (OSError, ValueError, ValidationError) as exc:
        err(safe(f"Could not open that run: {exc}"))
        return
    activate_run(run)


def _go_screen(name: str) -> None:
    st.query_params.pop("job", None)
    st.session_state._route_seen = dict(st.query_params)
    st.session_state.screen = name


def _new_research() -> None:
    st.query_params.pop("job", None)
    st.session_state._route_seen = dict(st.query_params)
    st.session_state.screen = "research"
    st.session_state.composing = True
    st.session_state.selected_buyer = None
    st.session_state.panel_open = False


def _select_buyer(acquirer_key: str) -> None:
    st.session_state.selected_buyer = acquirer_key
    st.session_state.panel_open = True
    st.session_state.screen = "research"
    st.session_state.composing = False


def _open_full_report() -> None:
    st.session_state.selected_buyer = None
    st.session_state.panel_open = True


def _close_panel() -> None:
    st.session_state.selected_buyer = None
    st.session_state.panel_open = False


def _back_to_run() -> None:
    st.session_state.composing = False
    st.session_state.screen = "research"
    st.session_state.selected_buyer = None
    st.session_state.panel_open = False


ROUTE_TABS = {"report": "Report", "transactions": "Transaction Evidence",
              "valuation": "Valuation & Risk", "score": "Score Breakdown"}


ROUTE_KEYS = ("run", "buyer", "tab", "view", "job")


def restore_route() -> None:
    route = {key: st.query_params.get(key) for key in ROUTE_KEYS if st.query_params.get(key)}
    previous = st.session_state.get("_route_seen")
    if route == previous:
        return
    st.session_state._route_seen = route
    if route.get("job"):
        return
    if not route:
        if previous is not None:
            clear_active_session()
        return
    if route.get("view") in ("about", "history") and not route.get("run"):
        st.session_state.screen = route["view"]
        return
    try:
        run_id = _plain_run_id(route.get("run", ""))
        path = catalog_path(run_id)
        if path is None or path.is_symlink():
            raise ValueError("This research link is unavailable or the session was deleted.")
        current = st.session_state.get("current_run")
        if not current or current["run_id"] != run_id:
            activate_run(engine.load_run(str(path)))
        run = st.session_state.current_run
        buyer = route.get("buyer")
        if buyer and buyer not in {r.acquirer_key for r in ordered_reports(run)}:
            raise ValueError("The buyer in this link is not part of this saved research.")
        st.session_state.screen = "research"
        st.session_state.composing = False
        st.session_state.selected_buyer = buyer
        st.session_state.panel_open = bool(buyer or route.get("tab"))
        st.session_state.panel_tab = ROUTE_TABS.get(route.get("tab"), "Report")
    except (ValueError, OSError, ValidationError) as exc:
        clear_active_session()
        st.session_state.route_notice = safe(str(exc))


def publish_route() -> None:
    route = {}
    screen = st.session_state.get("screen")
    if screen in ("about", "history"):
        route = {"view": screen}
    elif not st.session_state.get("composing") and st.session_state.get("current_run"):
        route["run"] = st.session_state.current_run["run_id"]
        if st.session_state.get("panel_open"):
            if st.session_state.get("selected_buyer"):
                route["buyer"] = st.session_state.selected_buyer
            selected = st.session_state.get("panel_tab", "Report")
            route["tab"] = next((key for key, label in ROUTE_TABS.items() if label == selected),
                                "report")
    if route != st.session_state.get("_route_seen"):
        st.query_params.from_dict(route)
        st.session_state._route_seen = route


def select_job(token: str) -> None:
    st.query_params.from_dict({"job": token})
    st.session_state._route_seen = {"job": token}


def watch_job(token: str) -> None:
    select_job(token)
    st.rerun()
