"""Activity panels rendered from recorded engine events."""


from ma_engine.ui.activity import activity_label
from ma_engine.ui.activity import as_markdown
from ma_engine.ui.activity import render_stages
from ma_engine.ui.activity import render_timeline
from ma_engine.ui.activity import render_totals
import streamlit as st
from ma_engine.ui.common import (
    buyer_names,
    cap,
    md,
    safe,
)


def event_names(events) -> dict[str, str]:
    """Buyer names from the step messages ("Blackstone: writing the report"), for the live view."""
    return {event["acquirer_key"]: event["message"].split(":", 1)[0] for event in events
            if event.get("acquirer_key") and ":" in str(event.get("message") or "")}


def render_activity(events: list[dict], totals: dict | None, key_prefix: str,
                    names: dict[str, str], *, failed: bool = False,
                    stages: bool = False, run_totals: bool = False, response_totals: bool = False,
                    notes: list[str] | None = None, reports=None, running: bool = False,
                    context_note: str = "") -> None:
    """One collapsed 'Agent activity' row for one response, drawn from its saved events only.

    `key_prefix` names the run and the response, because step numbers restart per follow-up.
    `run_totals` shows the original research totals. `response_totals` shows only
    the saved usage for this follow-up; neither includes other responses.
    """
    label = "Agent activity · Running" if running else activity_label(totals, failed=failed)
    icon = ":material/hourglass_top:" if running else (":material/error:" if failed else ":material/check_circle:")
    # `on_change="rerun"` stores open or closed. `expanded=False` is only the first value,
    # so finishing collapses the row once and reopening it survives later redraws.
    activity = st.expander(as_markdown(label), icon=icon, expanded=False, on_change="rerun",
                           key=f"{key_prefix}-activity")
    if not activity.open:
        return
    with activity:
        if context_note:
            cap(context_note)
        if run_totals:
            with st.expander("Run-wide totals"):
                cap("These totals cover the whole research run. They stay the same when a "
                    "buyer is selected.")
                render_totals(totals)
        elif response_totals:
            with st.expander("Response totals", key=f"{key_prefix}-totals"):
                cap("These totals cover this follow-up only, excluding the original research "
                    "and other responses.")
                render_totals(totals)
        if notes:
            with st.expander("Buyer substitutions"):
                for note in notes:
                    md(f"- {note}")
        if stages:
            render_stages(events, key_prefix, names, safe, reports)
        else:
            render_timeline(events, key_prefix=key_prefix, names=names, scrub=safe)


def substitution_notes(run: dict) -> list[str]:
    """Shortlist replacements saved on the run. Empty when nobody was swapped in."""
    notes = []
    for sub in (run.get("trace") or {}).get("substitutions") or []:
        notes.append(f"{sub.get('replacement')} replaced {sub.get('failed')} "
                     f"(shortlist rank {sub.get('replacement_shortlist_rank')}).")
    return notes


def render_completion(run: dict) -> None:
    """The finished research as one collapsed activity row, above the answer."""
    trace = run.get("trace") or {}
    render_activity(trace.get("events") or [], trace.get("totals"), f"run-{run['run_id']}",
                    buyer_names(run), stages=True, run_totals=True, notes=substitution_notes(run),
                    reports=run.get("reports"))
