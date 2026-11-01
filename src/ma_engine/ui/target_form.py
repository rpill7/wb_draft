"""Target editing and request-card presentation."""


from ma_engine.models import GEOGRAPHIES
from ma_engine.models import OWNERSHIPS
from ma_engine.data.prepare import SECTORS
from ma_engine.models import Target
from pydantic import ValidationError
from ma_engine.ui.activity import as_markdown
import streamlit as st
from ma_engine.ui.reports import target_sentence
from ma_engine.ui.activity_view import (
    event_names,
    render_activity,
)
from ma_engine.ui.common import (
    ASSISTANT_AVATAR,
    _index,
    cap,
    err,
    md,
)


MARGIN_LABELS = {"strong": "Strong (above the sector median)", "any": "Any"}


def _start_edit() -> None:
    st.session_state.editing_target = True


def _run_updated_target() -> None:
    """The user asked for a new run with the saved values. The old sentence no longer matches."""
    st.session_state.pending_target = st.session_state.edited_target
    st.session_state.pending_reset_excluded = True
    st.session_state.edited_target = None
    st.session_state.user_request = None


def _discard_updated_target() -> None:
    st.session_state.edited_target = None


def render_target_form(values: dict) -> None:
    """The target fields, prefilled. Save stores the values; it does not start a run."""
    with st.form("edit-target-form"):
        left, right = st.columns(2)
        sector = left.selectbox("Sector", SECTORS, index=_index(SECTORS, values["sector"]))
        ev_mm = right.number_input(
            as_markdown("Enterprise value ($M)"), min_value=1.0, max_value=100_000.0,
            value=float(values["ev_mm"]), step=10.0)
        geography = left.selectbox("Geography", GEOGRAPHIES,
                                   index=_index(GEOGRAPHIES, values["geography"]))
        ownership = right.selectbox("Ownership", OWNERSHIPS,
                                    index=_index(OWNERSHIPS, values["ownership"]))
        margin = st.radio(
            "EBITDA margin", list(MARGIN_LABELS),
            index=_index(list(MARGIN_LABELS), values["margin_profile"]),
            format_func=MARGIN_LABELS.get, horizontal=True)
        with st.container(horizontal=True, vertical_alignment="center", gap="small",
                          width="content", key="target-actions"):
            saved = st.form_submit_button("Save", type="primary", width="content")
            cancelled = st.form_submit_button("Cancel", type="tertiary", width="content")
    if cancelled:
        st.session_state.editing_target = False
        st.rerun()
    if not saved:
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
    st.session_state.edited_target = proposed
    st.session_state.editing_target = False
    st.rerun()


def render_updated_target() -> None:
    values = st.session_state.edited_target
    with st.container(border=True, key="updated-target"):
        md(f"**Updated target:** {target_sentence(Target(**values))}")
        cap("The results below are still for the previous target.")
        run, discard, _rest = st.columns([2.2, 1, 2], vertical_alignment="center")
        run.button("Run research with updated target", key="run-updated-target", type="primary",
                   icon=":material/play_arrow:", on_click=_run_updated_target)
        discard.button("Discard", key="discard-updated-target", type="tertiary",
                       on_click=_discard_updated_target)


def render_request_card(question: str, values: dict | None = None) -> None:
    """The user's request, exactly as typed. With `values`, an Edit target action sits in its
    top-right corner and opens the form directly below the card."""
    with st.chat_message("user"):
        if values is None:
            md(question)
        else:
            with st.container(horizontal=True, vertical_alignment="center", key="request-bar"):
                md(question)
                st.button("Edit", key="edit-target", type="tertiary", icon=":material/edit:",
                          help="Edit target", on_click=_start_edit,
                          disabled=st.session_state.editing_target, width="content")
    if values is None:
        return
    if st.session_state.editing_target:
        render_target_form(st.session_state.edited_target or values)
    elif st.session_state.edited_target:
        render_updated_target()


def draw_history() -> None:
    for index, message in enumerate(st.session_state.messages):
        avatar = ASSISTANT_AVATAR if message["role"] == "assistant" else None
        with st.chat_message(message["role"], avatar=avatar):
            if message.get("kind") == "error":
                if message.get("events"):
                    render_activity(message["events"], None, f"stopped-{index}",
                                    event_names(message["events"]), failed=True, stages=True)
                err(message["content"])
                continue
            md(message["content"])
