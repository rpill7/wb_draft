"""Application CSS and citation-popover browser behavior."""


import streamlit as st


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
