"""Buyer report tabs, downloads and previous/next navigation."""


from pathlib import Path
from ma_engine.ui.reports import buyer_block_html
from ma_engine.ui.reports import buyer_source_ids
from ma_engine.ui.reports import deal_rows_for
from ma_engine.ui.reports import executive_summary_html
from ma_engine.ui.reports import focus_header_html
from ma_engine.clients.data_client import get_client
from ma_engine.ui.reports import metric_items
from ma_engine.ui.reports import metrics_strip_html
import pandas as pd
from ma_engine.ui.reports import report_body_html
from ma_engine.ui.reports import section_html
from ma_engine.ui.reports import shortlist_table_html
from ma_engine.ui.reports import source_index
import streamlit as st
from ma_engine.ui.reports import verification_html
from ma_engine.ui.common import (
    cap,
    deals_analysed,
    info,
    ordered_reports,
    ranking_entry,
    show_html,
    slug,
    sources_of,
    write,
)
from ma_engine.ui.navigation import (
    _close_panel,
    _select_buyer,
)


PANEL_TABS = ("Report", "Transaction Evidence", "Valuation & Risk", "Score Breakdown")


def render_buyer_nav(run: dict, report, reports) -> None:
    index = reports.index(report)
    with st.container(key="buyer-nav"):
        previous, middle, nxt = st.columns(3, vertical_alignment="center")
        previous.button(
            "Previous", key=f"prev-buyer-{run['run_id']}", type="tertiary",
            icon=":material/chevron_left:", disabled=index <= 0, on_click=_select_buyer,
            args=(reports[index - 1].acquirer_key if index > 0 else report.acquirer_key,))
        middle.markdown(
            f'<p class="meta">Buyer {index + 1} of {len(reports)}</p>',
            unsafe_allow_html=True)
        nxt.button(
            "Next", key=f"next-buyer-{run['run_id']}", type="tertiary",
            icon=":material/chevron_right:", icon_position="right",
            disabled=index >= len(reports) - 1, on_click=_select_buyer,
            args=(reports[index + 1].acquirer_key if index < len(reports) - 1
                  else report.acquirer_key,))


def render_single_buyer(run: dict, report, reports) -> None:
    """Metric strip, then the six sections as one document."""
    sources = source_index(run)
    if report.selection_note:
        cap(report.selection_note)
    items = metric_items(ranking_entry(run, report.acquirer_key),
                         get_client().key_metrics(report.acquirer_key), sources)
    show_html(metrics_strip_html(items, sources))
    if report.draft is None:
        cap("This buyer has no report body. The reason is under the buyer's name.")
    else:
        show_html(report_body_html(report.draft, sources))
    render_buyer_nav(run, report, reports)


def render_all_buyers(run: dict) -> None:
    """The combined report: summary, shortlist, then every buyer's report."""
    reports = ordered_reports(run)
    sources = source_index(run)
    show_html(executive_summary_html(run, sources, deals_analysed()))
    show_html(f"<h2>Top {len(reports)} buyers</h2>{shortlist_table_html(reports)}")
    st.markdown("**Open one buyer's report**")
    for start in range(0, len(reports), 5):
        chunk = reports[start:start + 5]
        columns = st.columns(len(chunk))
        for column, report in zip(columns, chunk):
            column.button(
                f"{report.rank}. {report.display_name}",
                key=f"jump-{run['run_id']}-{slug(report.acquirer_key)}",
                on_click=_select_buyer, args=(report.acquirer_key,))
    for report in reports:
        show_html(buyer_block_html(report, sources))


def render_transaction_tab(run: dict, report) -> None:
    if report is None:
        info("Select a buyer in the list to see that buyer's cited transactions.")
        return
    if report.draft is None:
        info("This buyer has no report body, so there are no cited transactions.")
        return
    rows = deal_rows_for(sources_of(run)["entries"], buyer_source_ids(report))
    if not rows:
        cap("This report does not cite a transaction record.")
        return
    cap(f"{len(rows)} transactions cited in this report.")
    st.dataframe(pd.DataFrame(rows), hide_index=True,
                 key=f"deals-{run['run_id']}-{slug(report.acquirer_key)}")


def render_valuation_tab(run: dict, report) -> None:
    if report is None:
        info("Select a buyer in the list to see valuation and risk.")
        return
    if report.draft is None:
        cap("This buyer has no report body, so there is no valuation section.")
        return
    sources = source_index(run)
    show_html(f'<div class="report-doc"><h3>Valuation Context</h3>'
              f"{section_html(report.draft, 'valuation_context', sources)}"
              f"<h3>Risk Flags</h3>{section_html(report.draft, 'risks', sources)}</div>")


def render_score(entry: dict | None) -> None:
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


def render_score_tab(run: dict, report) -> None:
    if report is None:
        info("Select a buyer in the list to see the score breakdown.")
        return
    render_score(ranking_entry(run, report.acquirer_key))


def render_report_panel(run: dict) -> None:
    reports = ordered_reports(run)
    selected = st.session_state.get("selected_buyer")
    report = next((item for item in reports if item.acquirer_key == selected), None)
    with st.container(key="panel-head"):
        if report is None:
            show_html(f'<h2>Full report</h2><p class="meta">Combined report on all '
                      f'{len(reports)} buyers.</p>')
        else:
            show_html(focus_header_html(report))
        with st.container(key="panel-actions"):
            path = Path(run["run_dir"]) / "reports.md"
            if path.is_file():
                st.download_button(
                    "Download report", path.read_bytes(), file_name="reports.md",
                    mime="text/markdown", key=f"export-{run['run_id']}", type="tertiary",
                    icon=":material/download:", help="Download report")
            st.button("Close report", key="close-panel", type="tertiary", icon=":material/close:",
                      help="Close report", on_click=_close_panel)
    if report is not None:
        show_html(verification_html(report))
    # A session saved while Agent Activity was a tab would name a tab that is gone.
    chosen = st.session_state.get("panel_tab")
    if isinstance(chosen, str) and chosen not in PANEL_TABS:
        st.session_state["panel_tab"] = PANEL_TABS[0]
    tabs = st.tabs(list(PANEL_TABS), key="panel_tab", on_change="rerun", default="Report")
    with tabs[0]:
        if report is None:
            render_all_buyers(run)
        else:
            render_single_buyer(run, report, reports)
    with tabs[1]:
        render_transaction_tab(run, report)
    with tabs[2]:
        render_valuation_tab(run, report)
    with tabs[3]:
        render_score_tab(run, report)
