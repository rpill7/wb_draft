"""Research screen. Display only.

The page calls `engine.parse_target`, `engine.run_engine` and `engine.run_followup`.
Switching tabs or buyers only re-reads the run already in session. It does not
rank, score, or call the model.
"""

import asyncio
import html
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import openai
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from ma_engine import config
from ma_engine.research import engine
from ma_engine.validation import guardrails
from ma_engine.persistence import submissions
from ma_engine.research.agent import RequestError, display_research_title
from ma_engine.clients.data_client import get_client
from ma_engine.clients.llm_client import LLMConfigError
from ma_engine.models import GEOGRAPHIES, OWNERSHIPS, Target
from ma_engine.data.prepare import SECTORS
from ma_engine.ui.reports import (
    buyer_block_html,
    buyer_evidence_line,
    buyer_source_ids,
    conviction_level,
    deal_rows_for,
    executive_summary_html,
    focus_header_html,
    metric_items,
    metrics_strip_html,
    money_text,
    report_body_html,
    section_html,
    shortlist_table_html,
    source_index,
    statement_html,
    status_text,
    target_sentence,
    verification_html,
)
from ma_engine.ui.activity import (
    LiveSteps,
    activity_label,
    as_markdown,
    live_label,
    render_timeline,
    render_stages,
    render_totals,
)

EXAMPLE = (
    "Find the 10 most likely acquirers for a regional Healthcare Services company "
    "valued at $200M EV with strong EBITDA margins."
)
ASSISTANT_AVATAR = ":material/auto_awesome:"
DELETE_QUESTION = (
    "Delete this research session and its saved reports, follow-ups and activity?"
)
BADGE_COLORS = {"High": "green", "Medium": "orange", "Low": "gray",
                "Verified": "green", "Not verified": "orange", "Failed": "red"}
LABEL_ESCAPES = set("\\`*_[]<>#:")
PANEL_TABS = ("Report", "Transaction Evidence", "Valuation & Risk", "Score Breakdown")
SUMMARY_BUYERS = 3
FIELD_NAMES = {
    "sector": "sector",
    "ev_mm": "enterprise value",
    "ownership": "ownership",
    "geography": "geography",
    "margin_profile": "EBITDA margin",
}
MARGIN_LABELS = {"strong": "Strong (above the sector median)", "any": "Any"}
MARGIN_SHORT = {"strong": "Strong", "any": "Any"}
REQUIRED_FILES = [
    config.RAW_CSV, config.TRANSACTIONS_CSV, config.ACQUIRER_PROFILES_JSON,
    config.VALUATION_COMPS_CSV, config.SECTOR_STATS_CSV, config.SECTOR_SIMILARITY_CSV,
    config.DATA_QUALITY_JSON,
]
PAGE_CSS = """
<style>
/* Only our own st.container keys (.st-key-*) and Streamlit's data-testid names are used.
   The sidebar width is left to Streamlit, so collapsing it frees the space. */
[data-testid="stMainBlockContainer"] { padding: 3rem 1.5rem 0.5rem; max-width: none; }
.product { margin: 0; padding: 2px 40px 0 0; }
.product-title { font-size: 1.15rem; font-weight: 700; line-height: 1.2; color: #FFFFFF; }
.product-sub { margin-top: 2px; font-size: 0.78rem; font-weight: 500; line-height: 1.2;
               color: #8FA2BA; }
.pill { display: inline-block; padding: 1px 10px; border-radius: 999px; font-size: 0.78rem;
        font-weight: 650; line-height: 1.6; margin: 0 4px 0 0; white-space: nowrap; }
.pill-high, .pill-verified { background: #E5F6EC; color: #0F7A3A; }
.pill-medium, .pill-not-verified { background: #FFF4D6; color: #8A5A00; }
.pill-low { background: #EEF1F4; color: #5C6770; }
.pill-failed { background: #FDECEC; color: #B42318; }
.meta { color: #5C6770; margin: 0.15rem 0 0.4rem; }
.warn { color: #8A5A00; background: #FFF8E8; border-radius: 8px; padding: 8px 10px; }
.para { margin: 0 0 0.75rem; line-height: 1.6; }
.lead { font-weight: 500; }
.interp { text-decoration: underline dotted #94A3B8; text-underline-offset: 3px; }
.cite-raw { color: #1D4E89; font-weight: 600; }
/* sidebar: header and footer stay put; only the recent list scrolls */
[data-testid="stSidebar"][data-collapsed="false"] [data-testid="stSidebarContent"] {
    position: relative; display: flex; flex-direction: column; height: 100%; min-height: 0;
    scrollbar-gutter: auto; padding: 0 !important; }
[data-testid="stSidebar"][data-collapsed="false"] [data-testid="stSidebarHeader"] {
    position: absolute; top: 6px; right: 6px; width: auto; height: auto; min-height: 0;
    z-index: 3; background: transparent; }
[data-testid="stSidebar"][data-collapsed="false"] [data-testid="stLogoSpacer"] { display: none; }
[data-testid="stSidebar"][data-collapsed="false"] [data-testid="stSidebarUserContent"] {
    flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; overflow: hidden;
    padding: 8px 18px 12px !important; }
[data-testid="stSidebar"][data-collapsed="false"] [data-testid="stSidebarUserContent"] > div,
[data-testid="stSidebar"][data-collapsed="false"] [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
    flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
[data-testid="stSidebar"] .stButton > button { width: 100%; border-radius: 8px; }
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] { justify-content: flex-start;
    padding: 8px 12px; margin: 0; }
[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"] { justify-content: flex-start;
    padding: 6px 12px; color: #DCE4EE; font-weight: 500; }
[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"]:hover {
    background: rgba(255, 255, 255, 0.08); color: #FFFFFF; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.25rem; }
[data-testid="stLayoutWrapper"]:has(> .st-key-sidebar-head),
[data-testid="stLayoutWrapper"]:has(> .st-key-sidebar-foot) { flex: none; }
[data-testid="stLayoutWrapper"]:has(> .st-key-sidebar-recent) {
    flex: 1 1 auto; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
[data-testid="stSidebar"] .st-key-sidebar-head { flex: none; gap: 14px; }
.st-key-sidebar-head [data-testid="stMarkdownContainer"],
.st-key-sidebar-recent [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
[data-testid="stSidebar"] .st-key-sidebar-recent { flex: 1 1 auto; min-height: 0; display: flex;
    flex-direction: column; overflow: hidden; gap: 4px; }
.st-key-sidebar-recent > [data-testid="stLayoutWrapper"]:has(> .st-key-sidebar-list) {
    flex: 1 1 auto; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
[data-testid="stSidebar"] .st-key-sidebar-list { flex: 1 1 auto; min-height: 0; gap: 2px;
    overflow-x: hidden; overflow-y: auto; }
[data-testid="stSidebar"] .st-key-sidebar-foot { flex: none; gap: 2px; margin-top: 8px;
    padding-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.14); }
/* Footer rows fill the sidebar. The icon and label start at the same inset as a history entry.
   The inner wrapper is what Streamlit centers, so that wrapper is left-aligned too. */
[data-testid="stSidebar"] .st-key-sidebar-foot [data-testid="stElementContainer"] { width: 100%; }
[data-testid="stSidebar"] .st-key-sidebar-foot button { width: 100% !important;
    justify-content: flex-start !important; text-align: left; padding: 7px 10px !important; }
[data-testid="stSidebar"] .st-key-sidebar-foot button > div,
[data-testid="stSidebar"] .st-key-sidebar-foot button > div > span {
    display: flex !important; justify-content: flex-start !important; align-items: center !important;
    width: 100% !important; min-width: 0; text-align: left; gap: 8px; }
[data-testid="stSidebar"] .st-key-sidebar-foot button p { margin: 0; text-align: left; }
[data-testid="stSidebar"] .st-key-sidebar-foot button:hover {
    background: rgba(255, 255, 255, 0.08); color: #FFFFFF; }
[data-testid="stSidebar"] .st-key-sidebar-foot button:focus-visible {
    outline: 2px solid #F2C14E; outline-offset: 2px; }
.side-label { color: #8FA2BA; font-size: 0.72rem; font-weight: 650; letter-spacing: 0.04em;
              text-transform: uppercase; margin: 12px 0 4px 2px; }
.st-key-sidebar-list button { height: auto; min-height: 0; white-space: normal;
    align-items: flex-start; text-align: left; padding: 7px 10px !important; }
.st-key-sidebar-list button > div,
.st-key-sidebar-list button > div > span { display: flex; justify-content: flex-start !important;
    align-items: stretch !important; width: 100%; min-width: 0; flex: 1 1 auto; overflow: hidden;
    text-align: left; }
.st-key-sidebar-list button [data-testid="stMarkdownContainer"] { display: block; min-width: 0;
    width: 100%; overflow: hidden; text-align: left; }
.st-key-sidebar-list button p { margin: 0; min-width: 0; overflow: hidden; text-align: left;
    font-size: 0.72rem; font-weight: 400; line-height: 1.35; color: #8FA2BA;
    white-space: normal; overflow-wrap: normal; word-break: keep-all; }
.st-key-sidebar-list button p br { display: none; }
.st-key-sidebar-list button p strong { display: block; max-width: 100%; font-size: 0.84rem;
    font-weight: 500; line-height: 1.3; color: #E8EEF6; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.st-key-sidebar-list button p span { display: block; margin-top: 1px; font-size: 0.72rem !important;
    font-weight: 400 !important; line-height: 1.35; color: #8FA2BA !important;
    white-space: normal; overflow-wrap: normal; word-break: keep-all; }
[class*="st-key-recent-on-"] button { background: rgba(255, 255, 255, 0.12) !important;
    color: #FFFFFF !important; }
[class*="st-key-recent-off-"] button:hover,
[class*="st-key-recent-on-"] button:hover { background: rgba(255, 255, 255, 0.16) !important;
    color: #FFFFFF !important; }
.st-key-sidebar-list button:focus-visible { outline: 2px solid #F2C14E; outline-offset: 2px; }
/* One history row: the summary fills the row. A red delete icon sits at its right edge. */
.st-key-sidebar-list [class*="st-key-recent-row-"] { flex-direction: row !important;
    align-items: center; gap: 2px; width: 100%; }
.st-key-sidebar-list [class*="st-key-recent-row-"] > div:first-child { flex: 1 1 auto; min-width: 0; }
.st-key-sidebar-list [class*="st-key-recent-row-"] > div:last-child { flex: 0 0 auto; width: auto !important; }
.st-key-sidebar-list [class*="st-key-menu-"] { width: auto !important; flex: none; }
.st-key-sidebar-list [class*="st-key-menu-"] button { width: 28px !important; min-width: 28px !important;
    height: 28px !important; min-height: 28px !important; padding: 0 !important;
    justify-content: center !important; align-items: center !important; color: #FF8A80 !important;
    background: transparent !important; }
.st-key-sidebar-list [class*="st-key-menu-"] button:hover { color: #FFD2CC !important;
    background: rgba(255, 138, 128, 0.16) !important; }
.st-key-sidebar-list [class*="st-key-menu-"] button > div,
.st-key-sidebar-list [class*="st-key-menu-"] button > div > span {
    justify-content: center !important; align-items: center !important; width: auto !important;
    flex: none !important; }
.st-key-sidebar-list [class*="st-key-menu-"] button [data-testid="stMarkdownContainer"] {
    position: absolute !important; width: 1px !important; height: 1px !important;
    padding: 0 !important; margin: -1px !important; overflow: hidden !important;
    clip: rect(0, 0, 0, 0) !important; white-space: nowrap !important; border: 0 !important; }
[class*="st-key-history-menu-"] button { color: #D92D20 !important; width: 32px !important;
    min-width: 32px !important; height: 32px !important; padding: 4px !important;
    justify-content: center !important; }
[class*="st-key-history-menu-"] button:hover { color: #B42318 !important;
    background: #FDECEC !important; }
[class*="st-key-history-menu-"] button [data-testid="stMarkdownContainer"] {
    position: absolute !important; width: 1px !important; height: 1px !important;
    padding: 0 !important; margin: -1px !important; overflow: hidden !important;
    clip: rect(0, 0, 0, 0) !important; white-space: nowrap !important; border: 0 !important; }
.st-key-sidebar-list [data-testid="stCaptionContainer"] { color: #8FA2BA; }
/* chat column: fills the workspace. The frame is one viewport tall; its scroll area takes
   whatever the chat box (at its current height) leaves, so the box never covers it. */
.st-key-chat-area, .st-key-chat-beside { flex: none; width: 100%; height: calc(100dvh - 4.5rem); }
.st-key-chat-area > :has(> .st-key-chat-scroll),
.st-key-chat-beside > :has(> .st-key-chat-scroll) { flex: 1 1 0; min-height: 0;
                                                    display: flex; flex-direction: column; }
.st-key-chat-scroll { flex: 1 1 0; min-height: 0; overflow-y: auto; overflow-x: hidden;
                      padding: 0 6px 12px 0; }
/* activity: filters stay on the left. The count and page arrows sit together on the right. */
[class*="-activity"] [data-testid="stVerticalBlock"],
[class*="-bar"] { gap: 0.35rem; }
[class*="-bar"] { width: 100%; align-items: center; flex-wrap: nowrap; margin: 0.1rem 0 0.2rem; }
[class*="-pager"] { flex: none; margin-left: auto; align-items: center; gap: 0.15rem; }
[class*="-pager"] [data-testid="stCaptionContainer"] { margin: 0; white-space: nowrap; }
[class*="-pager"] [class*="-prev"],
[class*="-pager"] [class*="-next"] { flex: none; width: auto; }
[class*="-pager"] button { width: auto; min-width: 1.7rem; padding: 0 0.35rem; }
[data-testid="stChatInput"] textarea { line-height: 1.35; padding-top: 0.15rem;
                                       padding-bottom: 0.15rem; max-height: 9rem; }
.st-key-request-bar { width: 100%; align-items: center; flex-wrap: nowrap; }
.st-key-request-bar p { margin: 0; }
.st-key-edit-target { flex: none; margin-left: auto; }
.st-key-edit-target button { width: auto; padding: 2px 8px; }
.st-key-target-actions { width: fit-content; flex-wrap: nowrap; gap: 0.4rem; }
.st-key-target-actions [data-testid="stElementContainer"] { flex: none; width: auto; }
.st-key-target-actions button { width: auto; }
[data-testid="stChatMessage"] { padding: 0.7rem 0.9rem; border-radius: 12px; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: #E8F0FB; }
.st-key-shortlist-head { width: 100%; align-items: center; flex-wrap: nowrap; }
.st-key-shortlist-head [data-testid="stElementContainer"]:has(.card-title),
.st-key-shortlist-head [data-testid="stMarkdown"],
.st-key-shortlist-head [data-testid="stMarkdown"] > div { height: auto; display: flex;
                                                         align-items: center; }
.st-key-shortlist-head [data-testid="stMarkdownContainer"] p { margin: 0; line-height: 1.2; }
.card-title { font-weight: 700; font-size: 1.02rem; margin: 0; }
.st-key-open-full-report { flex: none; margin-left: auto; display: flex; align-items: center; }
.st-key-open-full-report button { width: auto; }
.stage { display: flex; align-items: center; gap: 10px; padding: 4px 0; font-size: 0.9rem; }
.stage-label { flex: 1; }
.stage-time { white-space: nowrap; color: #5C6770; font-variant-numeric: tabular-nums; }
.tick { display: inline-flex; align-items: center; justify-content: center; flex: none;
        width: 18px; height: 18px; border-radius: 50%; font-size: 0.7rem; font-weight: 800; }
.tick-completed { background: #1FA35B; color: #fff; }
.tick-running { background: #1D4E89; color: #fff; }
.tick-failed { background: #B42318; color: #fff; }
.tick-pending { border: 2px solid #C9D1DB; }
/* top-10 shortlist: one button per buyer; two columns unless the shortlist itself is narrow */
.st-key-shortlist-area { container-type: inline-size; }
.st-key-shortlist { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 12px; align-items: stretch; }
@container (max-width: 34rem) { .st-key-shortlist { grid-template-columns: minmax(0, 1fr); } }
.st-key-shortlist [class*="st-key-card-"] { height: 100%; }
.st-key-shortlist [class*="st-key-card-"] > [data-testid="stElementContainer"],
.st-key-shortlist [class*="st-key-card-"] .stButton { height: 100%; }
.st-key-shortlist [class*="st-key-card-"] button { height: 100%; min-height: 0; padding: 12px;
    justify-content: flex-start; align-items: flex-start; text-align: left; white-space: normal;
    background: #fff; border: 1px solid #E3E8EF; border-radius: 10px; color: #1B2430; }
.st-key-shortlist [class*="st-key-card-"] button > div,
.st-key-shortlist [class*="st-key-card-"] button > div > span { justify-content: flex-start;
                                                               width: 100%; min-width: 0; }
.st-key-shortlist [class*="st-key-card-"] button:hover { border-color: #94A3B8; color: #1B2430; }
.st-key-shortlist [class*="st-key-card-"] button:focus-visible { outline: 2px solid #1D4E89;
                                                                 outline-offset: 2px; }
.st-key-shortlist [class*="st-key-card-sel-"] button { background: #E8F0FB; border-color: #1D4E89; }
.st-key-shortlist [class*="st-key-card-"] button p { margin: 0; text-align: left;
    white-space: normal; overflow-wrap: anywhere; line-height: 1.75; font-size: 0.86rem; }
.st-key-shortlist [class*="st-key-card-"] button strong { font-size: 0.95rem; }
.exec-summary h3 { font-size: 1.15rem; margin: 0.4rem 0 0.5rem; padding: 0; }
.shortlist { width: 100%; border-collapse: collapse; margin-bottom: 1rem; }
.shortlist th { text-align: left; color: #5C6770; font-size: 0.8rem; font-weight: 650;
                padding: 6px 8px; border-bottom: 1px solid #E3E8EF; }
.shortlist td { padding: 6px 8px; border-bottom: 1px solid #EEF2F6; }
/* buyer panel: scrolls on its own; below 900px the page scrolls instead */
.st-key-panel { flex: none; height: calc(100dvh - 4.5rem); overflow-y: auto; overflow-x: hidden;
                background: #fff; border: 1px solid #E3E8EF; border-radius: 12px;
                padding: 14px 18px; }
.st-key-panel h2 { font-size: 1.45rem; margin: 0; padding: 0; overflow-wrap: anywhere; }
.st-key-panel-head { flex-direction: row !important; align-items: flex-start; gap: 12px; }
.st-key-panel-head > [data-testid="stElementContainer"]:first-child { flex: 1 1 auto; min-width: 0; }
.st-key-panel-head > [data-testid="stElementContainer"]:last-child { flex: 0 0 auto; width: auto; }
/* The actions row is wider than the two icons. Pack both buttons to its right edge. */
.st-key-panel-actions { flex-direction: row !important; align-items: center;
    justify-content: flex-end !important; gap: 8px; width: auto; }
.st-key-panel-actions [data-testid="stElementContainer"] { width: auto !important; flex: 0 0 auto; }
.st-key-panel-actions button { width: 40px !important; height: 40px !important;
    min-width: 40px !important; min-height: 40px !important; padding: 8px !important;
    justify-content: center; border-radius: 8px; }
.st-key-panel-actions button [data-testid="stMarkdownContainer"] {
    position: absolute !important; width: 1px !important; height: 1px !important;
    padding: 0 !important; margin: -1px !important; overflow: hidden !important;
    clip: rect(0, 0, 0, 0) !important; white-space: nowrap !important; border: 0 !important; }
.st-key-panel-actions button:hover { background: #F1F5F9 !important; }
.st-key-panel-actions button:focus:not(:focus-visible),
.st-key-panel-actions button:focus-visible { outline: 2px solid #1D4E89 !important;
    outline-offset: 2px; box-shadow: 0 0 0 2px #1D4E89 !important; }
/* One buyer row: equal side tracks, the count in the middle, buttons at the edges. */
.st-key-buyer-nav [data-testid="stHorizontalBlock"] { display: grid !important;
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center;
    width: 100%; gap: 12px; }
.st-key-buyer-nav [data-testid="stHorizontalBlock"] > div { width: max-content !important;
    min-width: 0; flex: none !important; }
.st-key-buyer-nav [data-testid="stHorizontalBlock"] > div:nth-child(1) { justify-self: start; }
.st-key-buyer-nav [data-testid="stHorizontalBlock"] > div:nth-child(2) { justify-self: center; }
.st-key-buyer-nav [data-testid="stHorizontalBlock"] > div:nth-child(3) { justify-self: end; }
.st-key-buyer-nav [data-testid="stElementContainer"] { width: auto !important; flex: 0 0 auto; }
.st-key-buyer-nav button { width: auto !important; min-height: 40px; }
.st-key-buyer-nav p { margin: 0; }
.verify { margin: 8px 0 4px; }
.verify-line { margin: 0 0 4px; color: #7A3412; }
.verify.fail .verify-line { color: #8E1B1B; }
.verify-details { margin: 2px 0 8px; }
.verify-details summary { cursor: pointer; color: #1D4E89; font-weight: 650; }
.verify-details ul { margin: 6px 0 0; padding-left: 1.2rem; }
.rank-tag { color: #5C6770; font-size: 1rem; font-weight: 600; }
.report-doc h3 { font-size: 1.05rem; margin: 1.1rem 0 0.35rem; padding: 0; }
.metric-strip { display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0 12px; }
.metric-strip .m { flex: 1 1 8.5rem; background: #F7F9FC; border: 1px solid #E3E8EF;
                   border-radius: 10px; padding: 8px 10px; }
.metric-strip .mv { font-size: 1.1rem; font-weight: 700; }
.metric-strip .ml { color: #5C6770; font-size: 0.78rem; line-height: 1.3; }
button[data-baseweb="tab"] { font-size: 0.86rem; }
h2 { scroll-margin-top: 12px; }
/* [n] stays in the sentence. The source card is one dialog, moved onto the page by script. */
details.cite, details.cite::details-content { display: inline; }
details.cite > .cite-pop { display: none !important; }
.cite-btn { display: inline; margin: 0; padding: 0 1px; border: 0; background: transparent;
            color: #1D4E89; font: inherit; font-weight: 700; font-size: 0.78em;
            line-height: 0; vertical-align: super; cursor: pointer; border-radius: 3px;
            list-style: none; }
.cite-btn::-webkit-details-marker { display: none; }
.cite-btn[aria-expanded="true"] { background: #DCE8F7; }
.cite-btn:focus-visible { outline: 2px solid #1D4E89; outline-offset: 1px; }
#cite-layer { position: fixed; z-index: 100000; box-sizing: border-box; width: 380px;
              height: 320px; display: flex; flex-direction: column; overflow: hidden;
              background: #fff; border: 1px solid #CBD5E1; border-radius: 12px;
              box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18); color: #1B2430; }
#cite-layer[hidden] { display: none !important; }
#cite-layer .cite-pop { display: flex; flex-direction: column; width: 100%; height: 100%;
                        min-height: 0; margin: 0; box-shadow: none; border: 0; }
.cite-head { flex: none; display: flex; align-items: center; justify-content: space-between;
             gap: 8px; padding: 12px 14px 8px; font-weight: 700; }
.cite-title { min-width: 0; }
.cite-close { flex: none; border: 0; background: transparent; color: #5C6770; cursor: pointer;
              font-size: 1.15rem; line-height: 1; padding: 0 2px; }
.cite-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 0 14px 12px;
             font-size: 0.85rem; font-weight: 400; line-height: 1.45; }
.cite-pop dl { display: grid; grid-template-columns: 7.5rem 1fr; gap: 2px 8px; margin: 0; }
.cite-pop dt { color: #5C6770; }
.cite-pop dd { margin: 0; }
.cite-pop ul { margin: 2px 0 4px; padding-left: 1.1rem; }
.cite-sub { font-weight: 650; margin: 8px 0 0; }
.cite-note { color: #5C6770; margin: 8px 0 0; }
.cite-pop ul:has(+ details.cite-more[open]) { display: none; }
.cite-more summary, .cite-tech summary { cursor: pointer; color: #1D4E89; margin-top: 4px; }
.cite-tech code { font-size: 0.75rem; word-break: break-all; }
[data-testid="stHtml"]:has(#wb-cite-boot) { display: none; }
@media (max-width: 900px) {
  .st-key-chat-area, .st-key-chat-beside, .st-key-panel { height: auto; overflow: visible; }
  .st-key-chat-area > :has(> .st-key-chat-scroll),
  .st-key-chat-beside > :has(> .st-key-chat-scroll),
  .st-key-chat-scroll { flex: none; overflow: visible; }
}
</style>
"""

CITE_JS = """
<div id="wb-cite-boot" hidden></div>
<script>
(() => {
  if (window.__wbCite === 2) return;
  window.__wbCite = 2;
  const OPEN_MS = 150;
  const CLOSE_MS = 200;
  const GAP = 12;
  let anchor = null;
  let openTimer = 0;
  let closeTimer = 0;
  let suppress = false;

  const canHover = () => window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const buttonOf = (target) => (target && target.closest ? target.closest(".cite-btn") : null);

  function layer() {
    let node = document.getElementById("cite-layer");
    if (node) return node;
    node = document.createElement("div");
    node.id = "cite-layer";
    node.hidden = true;
    node.setAttribute("role", "dialog");
    document.body.appendChild(node);
    node.addEventListener("mouseenter", cancelClose);
    node.addEventListener("mouseleave", () => { if (canHover()) scheduleClose(); });
    return node;
  }

  function place(btn) {
    const box = layer();
    const rect = btn.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) {
      hide();
      return;
    }
    const width = Math.min(380, Math.max(160, vw - GAP * 2));
    const height = Math.min(320, Math.max(120, vh - GAP * 2));
    box.style.width = width + "px";
    box.style.height = height + "px";
    const above = rect.top - GAP;
    const below = vh - rect.bottom - GAP;
    const right = vw - rect.right - GAP;
    const left = rect.left - GAP;
    let top;
    let x;
    if (above >= height) {
      top = rect.top - GAP - height;
      x = rect.left;
    } else if (below >= height) {
      top = rect.bottom + GAP;
      x = rect.left;
    } else if (right >= width) {
      x = rect.right + GAP;
      top = rect.top;
    } else if (left >= width) {
      x = rect.left - GAP - width;
      top = rect.top;
    } else {
      const room = [["above", above], ["below", below], ["right", right], ["left", left]];
      room.sort((a, b) => b[1] - a[1]);
      const side = room[0][0];
      if (side === "below") { top = rect.bottom + GAP; x = rect.left; }
      else if (side === "right") { x = rect.right + GAP; top = rect.top; }
      else if (side === "left") { x = rect.left - GAP - width; top = rect.top; }
      else { top = rect.top - GAP - height; x = rect.left; }
    }
    box.style.top = Math.min(Math.max(top, GAP), vh - GAP - height) + "px";
    box.style.left = Math.min(Math.max(x, GAP), vw - GAP - width) + "px";
  }

  function hide() {
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
    const box = document.getElementById("cite-layer");
    if (box) {
      box.hidden = true;
      box.replaceChildren();
    }
    if (anchor) anchor.setAttribute("aria-expanded", "false");
    anchor = null;
  }

  function open(btn) {
    const root = btn.closest(".cite");
    const card = root && root.querySelector(".cite-pop");
    if (!card) return;
    clearTimeout(openTimer);
    cancelClose();
    if (anchor && anchor !== btn) anchor.setAttribute("aria-expanded", "false");
    anchor = btn;
    const box = layer();
    box.replaceChildren(card.cloneNode(true));
    const title = box.querySelector(".cite-title");
    box.setAttribute("aria-label", title ? title.textContent : "Source");
    const close = box.querySelector(".cite-close");
    if (close) {
      close.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const back = anchor;
        suppress = true;
        hide();
        if (back) back.focus({preventScroll: true});
        suppress = false;
      });
    }
    box.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    place(btn);
  }

  function scheduleOpen(btn) {
    cancelClose();
    if (anchor === btn) return;
    clearTimeout(openTimer);
    openTimer = setTimeout(() => open(btn), OPEN_MS);
  }

  function cancelClose() { clearTimeout(closeTimer); }

  function scheduleClose() {
    clearTimeout(closeTimer);
    closeTimer = setTimeout(() => {
      const box = document.getElementById("cite-layer");
      const active = document.activeElement;
      const focused = active && (active === anchor || (box && box.contains(active)));
      const hovering = canHover() && ((anchor && anchor.matches(":hover")) || (box && box.matches(":hover")));
      if (focused || hovering) return;
      hide();
    }, CLOSE_MS);
  }

  function focusables(box) {
    return [...box.querySelectorAll("button, summary, a[href], input, textarea, select")]
      .filter((item) => !item.disabled);
  }

  document.addEventListener("mouseover", (event) => {
    if (!canHover()) return;
    const btn = buttonOf(event.target);
    if (btn) scheduleOpen(btn);
  });
  document.addEventListener("mouseout", (event) => {
    if (!canHover()) return;
    const btn = buttonOf(event.target);
    if (!btn) return;
    const next = event.relatedTarget;
    const box = document.getElementById("cite-layer");
    if (next && (btn.contains(next) || (box && box.contains(next)))) return;
    clearTimeout(openTimer);
    scheduleClose();
  });
  document.addEventListener("click", (event) => {
    const btn = buttonOf(event.target);
    if (btn) {
      event.preventDefault();
      if (!canHover()) open(btn);
      return;
    }
    const box = document.getElementById("cite-layer");
    if (!canHover() && box && !box.hidden && !box.contains(event.target)) hide();
  }, true);
  document.addEventListener("focusin", (event) => {
    if (suppress) return;
    const btn = buttonOf(event.target);
    if (btn) {
      cancelClose();
      open(btn);
      return;
    }
    const box = document.getElementById("cite-layer");
    if (box && box.contains(event.target)) cancelClose();
  });
  document.addEventListener("focusout", () => scheduleClose());
  document.addEventListener("keydown", (event) => {
    const box = document.getElementById("cite-layer");
    if (event.key === "Escape" && box && !box.hidden) {
      event.preventDefault();
      const back = anchor;
      suppress = true;
      hide();
      if (back) back.focus({preventScroll: true});
      suppress = false;
      return;
    }
    if (event.key !== "Tab" || !box || box.hidden || !anchor) return;
    const items = focusables(box);
    if (!items.length) return;
    const active = document.activeElement;
    if (active === anchor && !event.shiftKey) {
      event.preventDefault();
      items[0].focus({preventScroll: true});
    } else if (box.contains(active)) {
      const index = items.indexOf(active);
      if (event.shiftKey && index <= 0) {
        event.preventDefault();
        anchor.focus({preventScroll: true});
      } else if (!event.shiftKey && index === items.length - 1) {
        event.preventDefault();
        hide();
      }
    }
  });
  window.addEventListener("scroll", () => { if (anchor && document.body.contains(anchor)) place(anchor); }, true);
  window.addEventListener("resize", () => { if (anchor && document.body.contains(anchor)) place(anchor); });
  new MutationObserver(() => {
    if (anchor && !document.body.contains(anchor)) hide();
  }).observe(document.body, {childList: true, subtree: true});
})();
</script>
"""


def install_cite_popover() -> None:
    """One source dialog for every [n]. Plain HTML, so opening it does not rerun the app."""
    st.html(CITE_JS, unsafe_allow_javascript=True)


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


def show_html(html_text: str) -> None:
    """HTML for the report. `$` becomes an entity so Streamlit does not draw math,
    and the page shows a dollar sign rather than a backslash."""
    st.markdown(html_text.replace("$", "&#36;"), unsafe_allow_html=True)


def _index(options: list, value) -> int:
    try:
        return list(options).index(value)
    except ValueError:
        return 0


def slug(acquirer_key: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in acquirer_key.lower()).strip("-")


def buyer_names(run: dict) -> dict[str, str]:
    return {report.acquirer_key: report.display_name for report in run.get("reports") or []}


def ordered_reports(run: dict):
    return sorted(run.get("reports") or [], key=lambda report: report.rank)


def ranking_entry(run: dict, acquirer_key: str) -> dict | None:
    ranking = run.get("ranking") or {}
    for buyer in ranking.get("buyers") or []:
        if buyer.get("acquirer_key") == acquirer_key:
            return buyer
    return None


def sources_of(run: dict) -> dict:
    sources = run.get("sources") or {}
    return {"numbers": dict(sources.get("numbers") or {}),
            "entries": list(sources.get("entries") or [])}


def deals_analysed() -> int | None:
    """The dataset's stored row count. Not counted again here."""
    quality = getattr(get_client(), "data_quality", None)
    count = quality.get("row_count") if isinstance(quality, dict) else None
    return count if isinstance(count, int) else None


def display_question(run: dict) -> str:
    """The sentence the user typed, or a description of the target when that was not stored."""
    request = (run.get("trace") or {}).get("request")
    if isinstance(request, str) and request.strip():
        return request.strip()
    return target_sentence(run["target"])


def refresh_sources(run: dict) -> None:
    """Read sources.json into the session run. A follow-up appends numbers there."""
    path = Path(run["run_dir"]) / "sources.json"
    if not path.is_file():
        return
    try:
        run["sources"] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return


# ---------- saved runs and session ----------

def init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("excluded", [])
    st.session_state.setdefault("session_runs", [])
    st.session_state.setdefault("followups", [])
    st.session_state.setdefault("stopped_followups", [])
    st.session_state.setdefault("screen", "research")
    st.session_state.setdefault("composing", False)
    st.session_state.setdefault("selected_buyer", None)
    st.session_state.setdefault("panel_open", False)
    st.session_state.setdefault("editing_target", False)
    st.session_state.setdefault("edited_target", None)
    st.session_state.setdefault("pending_delete", None)


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
    refresh_sources(run)
    st.session_state.current_run = run
    st.session_state.followups = read_followups(run["run_dir"])
    st.session_state.excluded = list((run.get("trace") or {}).get("excluded_keys") or [])
    st.session_state.question_run_id = run["run_id"]
    st.session_state.run_question = display_question(run)
    st.session_state.user_request = (run.get("trace") or {}).get("request")
    st.session_state.screen = "research"
    st.session_state.composing = False
    st.session_state.selected_buyer = None
    st.session_state.panel_open = False
    st.session_state.editing_target = False
    st.session_state.edited_target = None
    remember_run(run)


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


def _runs_root() -> Path:
    return config.RUNS_DIR.resolve()


def _plain_run_id(run_id: str) -> str:
    """A run folder name. A path, blank id, or parent reference is rejected."""
    if not isinstance(run_id, str) or run_id != Path(run_id).name or run_id in {".", ".."}:
        raise ValueError("That is not a saved research session.")
    return run_id


def _direct_run_path(run_id: str) -> Path | None:
    """The folder for this id when it is a real directory directly inside the generated runs folder."""
    path = config.RUNS_DIR / run_id
    if path.is_symlink():
        return None
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if resolved.parent != _runs_root():
        return None
    return path


def can_delete_run(path: Path) -> bool:
    """True when this catalog entry is a finished user-generated run, not the sample or a link."""
    if path.is_symlink() or not (path / "reports.json").is_file():
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved.parent == _runs_root()


def _being_written(path: Path) -> bool:
    """A run folder exists before its report file is saved. That window is still being written."""
    return path.is_dir() and not path.is_symlink() and not (path / "reports.json").is_file()


def catalog_path(run_id: str) -> Path | None:
    for _label, path in run_catalog():
        if path.name == run_id:
            return path
    return None


def delete_research_session(run_id: str) -> str:
    """Delete one generated run directory. Returns `deleted` or `missing`.

    The id is a folder name from the catalog, never a path typed into the page.
    Raises ValueError when the delete is refused or the folder cannot be removed.
    The message is safe to show. A refusal leaves the folder where it is.
    """
    run_id = _plain_run_id(run_id)
    path = catalog_path(run_id)
    if path is None:
        candidate = config.RUNS_DIR / run_id
        if candidate.exists() and _direct_run_path(run_id) is None:
            raise ValueError("Only a saved research session can be deleted.")
        direct = _direct_run_path(run_id)
        if direct is not None and direct.exists() and _being_written(direct):
            raise ValueError("This research session is still being written and cannot be deleted.")
        return "missing"
    if not can_delete_run(path) or _being_written(path):
        if _being_written(path):
            raise ValueError("This research session is still being written and cannot be deleted.")
        raise ValueError("Only a saved research session can be deleted.")
    try:
        shutil.rmtree(path)
    except OSError:
        raise ValueError("Could not delete that research session.") from None
    if path.exists():
        raise ValueError("Could not delete that research session.")
    run_target_text.clear()
    return "deleted"


def active_run_id() -> str | None:
    run = st.session_state.get("current_run")
    if not isinstance(run, dict):
        return None
    run_id = run.get("run_id")
    if isinstance(run_id, str) and run_id:
        return run_id
    raw = run.get("run_dir")
    if isinstance(raw, str) and raw:
        return Path(raw).name
    return None


def clear_active_session() -> None:
    """Drop the open session and show the new-research screen. Does not start a run."""
    st.session_state.current_run = None
    st.session_state.followups = []
    st.session_state.selected_buyer = None
    st.session_state.panel_open = False
    st.session_state.editing_target = False
    st.session_state.edited_target = None
    st.session_state.excluded = []
    st.session_state.question_run_id = None
    st.session_state.run_question = None
    st.session_state.user_request = None
    st.session_state.messages = []
    st.session_state.pop("pending_target", None)
    st.session_state.pop("pending_question", None)
    st.session_state.pop("pending_reset_excluded", None)
    st.session_state.pending_open = None
    st.session_state.screen = "research"
    st.session_state.composing = True


def forget_deleted_run(run_id: str) -> None:
    """Refresh the lists after a delete. The open session is cleared only when it was this run."""
    st.session_state.session_runs = [
        item for item in st.session_state.get("session_runs") or []
        if Path(str(item)).name != run_id]
    pending = st.session_state.get("pending_open")
    if isinstance(pending, str) and Path(pending).name == run_id:
        st.session_state.pending_open = None
    stopped = st.session_state.get("stopped_followups") or []
    st.session_state.stopped_followups = [
        item for item in stopped if item.get("run_id") != run_id]
    if active_run_id() == run_id:
        clear_active_session()
    st.session_state.pending_delete = None
    run_target_text.clear()


def session_identity(run_id: str) -> tuple[str, str]:
    """Title and timestamp for the confirmation. A missing folder still shows its id and time."""
    try:
        plain = _plain_run_id(run_id)
    except ValueError:
        return str(run_id), ""
    path = catalog_path(plain)
    if path is None:
        return plain, run_time_text(plain)
    _sector, _size, title = run_target_text(str(path))
    return title or plain, run_time_text(path.name)


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


def queue_open(path_str: str) -> None:
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
    st.session_state.screen = name


def _new_research() -> None:
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


# URL contains identifiers only. It never starts research or accepts file paths.
ROUTE_TABS = {"report": "Report", "transactions": "Transaction Evidence",
              "valuation": "Valuation & Risk", "score": "Score Breakdown"}
ROUTE_KEYS = ("run", "buyer", "tab", "view")


def restore_route() -> None:
    route = {key: st.query_params.get(key) for key in ROUTE_KEYS if st.query_params.get(key)}
    previous = st.session_state.get("_route_seen")
    if route == previous:
        return
    st.session_state._route_seen = route
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


# ---------- sidebar ----------

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
                                  on_click=_go_screen, args=("history",))
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


def run_label(path: Path) -> str:
    """'Healthcare Services · $200.0M · Oct 31, 22:27' from the run's saved target."""
    sector, size, _title = run_target_text(str(path))
    when = run_time_text(path.name)
    return " · ".join(part for part in (sector, size, when) if part)


def sidebar_size_text(size: str) -> str:
    """Whole millions without a trailing `.0`. Sidebar text only."""
    if size.endswith(".0M"):
        return f"{size[:-3]}M"
    return size


def sidebar_run_text(path: Path) -> tuple[str, str]:
    """Two-line recent-run label, plus the full title and time for hover and access.

    Spaces inside the time are non-breaking, so a narrow sidebar wraps before the
    date instead of leaving the clock on a line by itself.
    """
    _sector, size, title = run_target_text(str(path))
    when = run_time_text(path.name)
    shown = sidebar_size_text(size)
    # A plain colon is a markdown marker, so the clock would split into two
    # pieces and wrap in the middle. The backslash keeps it one time.
    stamp = when.replace(":", "\\:").replace(" ", "\u00a0")
    meta = " · ".join(part for part in (shown, stamp) if part)
    label = f"**{title}**  \n{meta}" if meta else f"**{title}**"
    hover = " · ".join(part for part in (title, shown, when.replace(":", "\\:")) if part)
    return as_markdown(label), as_markdown(hover)


@st.cache_data(show_spinner=False)
def run_target_text(path_str: str) -> tuple[str, str, str]:
    """Saved sector, valuation text, and the research title for one run folder.

    The title is the name stored with the run. An older file with no name uses the
    sector fallback. This does not call the model and does not rewrite the file.
    """
    try:
        data = json.loads((Path(path_str) / "reports.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", display_research_title(None, None)
    target = data.get("target") if isinstance(data, dict) else None
    if not isinstance(target, dict):
        return "", "", display_research_title(None, None)
    sector = str(target.get("sector") or "")
    stored = data.get("research_title") if isinstance(data, dict) else None
    return sector, money_text(target.get("ev_mm")), display_research_title(stored, sector)


def run_time_text(run_id: str) -> str:
    try:
        stamp = datetime.strptime(run_id[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return run_id
    return stamp.strftime("%b %d, %H:%M")


# ---------- adjust form ----------

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


# ---------- running the engine (display of progress only) ----------

def event_names(events) -> dict[str, str]:
    """Buyer names from the step messages ("Blackstone: writing the report"), for the live view."""
    return {event["acquirer_key"]: event["message"].split(":", 1)[0] for event in events
            if event.get("acquirer_key") and ":" in str(event.get("message") or "")}


def render_activity(events: list[dict], totals: dict | None, key_prefix: str,
                    names: dict[str, str], *, failed: bool = False,
                    stages: bool = False, run_totals: bool = False, response_totals: bool = False,
                    notes: list[str] | None = None, reports=None) -> None:
    """One collapsed 'Agent activity' row for one response, drawn from its saved events only.

    `key_prefix` names the run and the response, because step numbers restart per follow-up.
    `run_totals` shows the original research totals. `response_totals` shows only
    the saved usage for this follow-up; neither includes other responses.
    """
    label = activity_label(totals, failed=failed)
    icon = ":material/error:" if failed else ":material/check_circle:"
    # `on_change="rerun"` stores open or closed. `expanded=False` is only the first value,
    # so finishing collapses the row once and reopening it survives later redraws.
    activity = st.expander(as_markdown(label), icon=icon, expanded=False, on_change="rerun",
                           key=f"{key_prefix}-activity")
    if not activity.open:
        return
    with activity:
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


def _parse(text: str) -> tuple[Target, list[str], str | None]:
    with st.status("Reading the request…", expanded=False) as status:
        def progress(event: dict) -> None:
            status.write(as_markdown(str(event.get("message") or event.get("name") or "")))

        target, defaulted, title = asyncio.run(engine.parse_target(text, progress=progress))
        status.update(label="Request read", state="complete", expanded=False)
    return target, defaulted, title


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


def _research_status(target: Target, request: str | None,
                     research_title: str | None = None) -> dict | None:
    """Run the engine. The four stages update in place from the engine's own events."""
    # The status box is updated only once, at the end: any `update` while it runs either
    # collapses it or forces it open, overriding what the reader chose. The live line under
    # it says what is happening now.
    status = st.status("Researching buyers…", expanded=True)
    live = LiveSteps(status, event_names, safe, limit=500, grouped=True)
    now = st.empty()
    events = live.events

    def progress(event: dict) -> None:
        live.add(event)
        now.caption(as_markdown(safe(live_label(events, event_names(events)))))

    try:
        run = asyncio.run(engine.run_engine(
            target, use_cache=True, excluded_keys=tuple(st.session_state.excluded),
            progress=progress, request=request, research_title=research_title))
    except Exception as exc:
        message = safe(failure_text(exc, events, during_run=True))
        now.empty()
        live.stop()
        status.update(label=as_markdown(activity_label(None, failed=True)),
                      state="error", expanded=False)
        err(message)
        st.session_state.messages.append(
            {"role": "assistant", "kind": "error", "content": message, "events": list(events)})
        return None
    now.empty()
    trace = run.get("trace") or {}
    status.update(label=as_markdown(activity_label(trace.get("totals"))),
                  state="complete", expanded=False)
    activate_run(run)
    return run


def run_from_text(text: str) -> bool:
    submission = submissions.begin(text)
    # A browser refresh during parsing/research lands on the durable request history.
    st.query_params.from_dict({"view": "history"})
    st.session_state._route_seen = {"view": "history"}
    try:
        completed = _run_submitted_text(text)
    except BaseException:
        submissions.finish(submission, "interrupted")
        raise
    run = st.session_state.get("current_run")
    submissions.finish(submission, "completed" if completed else "interrupted",
                       run["run_id"] if completed and run else None)
    return completed


def _run_submitted_text(text: str) -> bool:
    """Show the user bubble, the parsed target, then the live run."""
    st.session_state.excluded = []
    st.session_state.messages.append({"role": "user", "content": text})
    render_request_card(text)
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        blocked = key_error_text()
        if blocked:
            err(blocked)
            st.session_state.messages.append(
                {"role": "assistant", "kind": "error", "content": blocked})
            return False
        try:
            target, defaulted, title = _parse(text)
        except Exception as exc:
            message = safe(failure_text(exc, [], during_run=False))
            err(message)
            st.session_state.messages.append(
                {"role": "assistant", "kind": "error", "content": message})
            return False
        assumed = assumed_text(target, defaulted)
        if assumed:
            cap(assumed)
        st.session_state.messages.append({
            "role": "assistant", "kind": "parse", "content": assumed, "request": text,
            "target": target.model_dump(), "defaulted": list(defaulted),
        })
        st.session_state.user_request = text
        return _research_status(target, text, title) is not None


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
    note = f"Running with these values: {target_sentence(target)}."
    st.session_state.messages.append({"role": "assistant", "kind": "note", "content": note})
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        md(note)
        return _research_status(target, _stored_request()) is not None


def take_pending_target() -> bool:
    values = st.session_state.pop("pending_target", None)
    reset = st.session_state.pop("pending_reset_excluded", False)
    st.session_state.pop("pending_question", None)
    if not values:
        return False
    if reset:
        st.session_state.excluded = []
    return run_from_target(values)


# ---------- report panel ----------

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


# ---------- chat column ----------

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


# ---------- follow-up chat ----------

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
    with st.chat_message("user"):
        md(question)
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        names = buyer_names(run)
        status = st.status("Answering the follow-up…", expanded=True)
        live = LiveSteps(status, lambda _events: names, safe)
        now = st.empty()

        def progress(event: dict) -> None:
            live.add(event)
            now.caption(as_markdown(safe(live_label(
                live.events, names, default="Answering the follow-up…"))))

        try:
            asyncio.run(engine.run_followup(question, run, use_cache=True, progress=progress))
        except Exception as exc:
            message = safe(failure_text(exc, live.events, during_run=False))
            now.empty()
            live.stop()
            status.update(label=as_markdown(activity_label(None, failed=True)),
                          state="error", expanded=False)
            err(message)
            st.session_state.stopped_followups.append({
                "run_id": run["run_id"], "position": len(st.session_state.followups),
                "question": question, "events": list(live.events), "error": message})
            return
        now.empty()
        refresh_sources(run)
        st.session_state.followups = read_followups(run["run_dir"])
        if not st.session_state.followups:
            status.update(label="Agent activity · Completed", state="complete", expanded=False)
            err("The follow-up finished but it was not saved.")
            return
        latest = _followup_dict(st.session_state.followups[-1])
        with status.expander("Response totals"):
            cap("These totals cover this follow-up only, excluding the original research "
                "and other responses.")
            render_totals(latest.get("totals"))
        status.update(label=as_markdown(activity_label(latest.get("totals"))),
            state="complete", expanded=False)
        followup_body(latest, len(st.session_state.followups) - 1, run, activity=False)


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
    for item in submissions.unfinished():
        if item.get("run_id") == run["run_id"] and not any(
                failure["question"] == item["question"] for failure in stopped):
            with st.chat_message("user"):
                md(item["question"])
            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                cap("Question saved. No finished response has been recorded; processing may "
                    "still be running or may have been interrupted. Refresh to check again. "
                    "You can resubmit the question if needed.")
    if pending and not blocked:
        run_followup_ui(pending, run)


def followup_input(run: dict) -> str | None:
    """The one chat box, under the chat column's scroll area."""
    reports = ordered_reports(run)
    hint = (f'Ask a follow-up question (e.g., "Why is {reports[0].display_name} ranked first?")'
            if reports else "Ask a follow-up question about this run")
    typed = st.chat_input(hint, key="followup_chat", disabled=key_error_text() is not None)
    return typed.strip() if typed and typed.strip() else None


# ---------- pages ----------

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
                cap("Question saved. No finished response has been recorded; processing may "
                    "still be running or may have been interrupted. Refresh to check again. "
                    "This page does not restart requests automatically.")
                if item.get("run_id"):
                    st.button("Open research", key=f"submission-{item['id']}",
                              on_click=queue_open,
                              args=(str(config.RUNS_DIR / item["run_id"]),))
                else:
                    cap("To retry, copy this question into New Research.")
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


def render_chat(run: dict) -> None:
    frame, scroll = chat_frame()
    with frame:
        pending = followup_input(run)
    with scroll:
        if take_pending_target():
            st.rerun()
        render_middle(run, pending)


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


main()
