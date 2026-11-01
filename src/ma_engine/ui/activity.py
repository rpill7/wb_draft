"""Draw the agent activity. Display only: no ranking, no model calls, no tool calls.

One row per step. A `running` event and the later `completed` or `failed` event share a step
number, so the row updates in place. Step numbers restart for each follow-up, so every caller
passes a key prefix naming the run and the response. Full tool output is kept for download;
the on-screen preview is capped so a large result (for example rank_acquirers) does not freeze
the page. Redaction (`scrub`) is applied here, at display and download time; the recorded
events are never changed.
"""

import json
import math
import re

import streamlit as st

PREVIEW_LIMIT = 20_000
PAGE_SIZE = 50

CATEGORY_LABELS = {
    "llm": "LLM",
    "mcp_tool": "MCP",
    "validation": "Validation",
    "calculation": "Calculation",
    "app": "App",
}
# A readable title for steps whose recorded name is internal. MCP tools keep their exact name.
STEP_TITLES = {
    ("llm", "research"): "Choose tools",
    ("llm", "write"): "Write report",
    ("llm", "repair"): "Repair draft",
    ("llm", "followup"): "Write answer",
    ("llm", "committee"): "Committee review",
    ("llm", "parse_request"): "Read request",
    ("validation", "check"): "Check evidence",
    ("app", "run"): "Start run",
    ("app", "mcp_connected"): "Connect to MCP server",
    ("app", "save"): "Save run",
    ("calculation", "ranking"): "Rank buyers",
    ("calculation", "apply_committee"): "Apply committee review",
}
STATUS_WORDS = {"running": "Running", "completed": "Completed", "failed": ":red[Failed]"}
REUSED_NOTE = ("Reused result: the same tool was already called with the same parameters in "
               "this research loop, so the app returned that recorded result instead of calling "
               "the MCP server again.")
CACHED_NOTE = ("Cached response: the LLM client answered from a response saved earlier for an "
               "identical request. No API call was made.")
_DOLLAR = re.compile(r"(?<!\\)\$")


def as_markdown(text) -> str:
    """Keep dollar amounts as text. Streamlit reads `$...$` as math.

    A dollar that is already escaped is left alone, so this is safe to call twice.
    Use this for markdown, captions, write, and expander labels. Dataframe cells
    are not markdown and must not be passed through here.
    """
    if text is None:
        return ""
    return _DOLLAR.sub(r"\\$", str(text))


def json_text(value) -> str:
    """The value as JSON text. A string is kept as-is so we do not add another encoding."""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def preview_json(value, limit: int = PREVIEW_LIMIT) -> tuple[str, str, bool]:
    """Return `(preview, full_text, truncated)`.

    `preview` is at most `limit` characters. When `truncated` is true, show `preview` on the
    page and offer `full_text` only as a download. Do not render `full_text` itself.
    """
    full = json_text(value)
    if len(full) > limit:
        return full[:limit], full, True
    return full, full, False


def clip(text, limit: int = 160) -> str:
    """Shorten text for a row label."""
    if text is None:
        return ""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def merge_steps(events: list[dict]) -> list[dict]:
    """One dict per step. The later event wins, so `completed` replaces `running`."""
    by_step: dict[int, dict] = {}
    order: list[int] = []
    for event in events:
        step = event.get("step")
        if not isinstance(step, int):
            continue
        if step not in by_step:
            order.append(step)
        by_step[step] = event
    return [by_step[step] for step in order]


def order_events(events: list[dict]) -> list[dict]:
    """Group each buyer's steps together, in step order, and leave other steps in place.

    A buyer's block is inserted where that buyer first appears, so rounds stay in order and
    the next step still follows the earlier result. Run-level steps (no buyer) keep their
    place, which puts the start of the run first and the committee and save at the end.
    """
    merged = sorted(merge_steps(events), key=lambda event: event.get("step") or 0)
    blocks: dict[str, list[dict]] = {}
    skeleton: list[tuple[str, object]] = []
    seen: set[str] = set()
    for event in merged:
        key = event.get("acquirer_key") or ""
        if key:
            blocks.setdefault(key, []).append(event)
            if key not in seen:
                seen.add(key)
                skeleton.append(("buyer", key))
        else:
            skeleton.append(("event", event))
    ordered: list[dict] = []
    for kind, value in skeleton:
        if kind == "event":
            ordered.append(value)  # type: ignore[arg-type]
        else:
            ordered.extend(sorted(blocks[value], key=lambda event: event.get("step") or 0))  # type: ignore[index]
    return ordered


def _brief(value) -> str:
    """A short display of a summary field. Nested or very long values are left out."""
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return clip(value, 80)
    return ""


def summary_text(summary) -> str:
    """Counts from `output_summary` only. Never reads the full `output`."""
    if not isinstance(summary, dict):
        return _brief(summary)
    parts = []
    for key, value in summary.items():
        shown = _brief(value)
        if shown:
            parts.append(f"{key} {shown}")
    return ", ".join(parts[:8])


def category_label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or "", category or "—")


def _summary(event: dict) -> dict:
    summary = event.get("output_summary")
    return summary if isinstance(summary, dict) else {}


def is_reused(event: dict) -> bool:
    """An MCP step answered from the loop's earlier result, not a new server call."""
    return event.get("category") == "mcp_tool" and bool(_summary(event).get("repeated"))


def is_cached(event: dict) -> bool:
    """An LLM step answered from the client's saved response, not a new API call."""
    return event.get("category") == "llm" and bool(_summary(event).get("cache_hit"))


def step_name(event: dict) -> str:
    """The exact tool name for MCP steps, a readable title for the others."""
    name = str(event.get("name") or "—")
    if event.get("category") == "mcp_tool":
        return name
    return STEP_TITLES.get((event.get("category"), name), name)


def status_word(event: dict, live: bool = False) -> str:
    """Completed means the step ran. It does not mean a report was verified.

    A step still `running` after the activity ended never finished, so it is not called
    Running or Completed.
    """
    status = event.get("status")
    if status == "running" and not live:
        return ":orange[Not finished]"
    return STATUS_WORDS.get(status, str(status or "—"))


def step_title(event: dict, names: dict[str, str] | None = None, live: bool = False) -> str:
    """One scannable line: name · kind · status · buyer · round · duration · reuse note."""
    names = names or {}
    parts = [step_name(event), category_label(event.get("category")), status_word(event, live)]
    buyer = event.get("acquirer_key")
    if buyer:
        parts.append(str(names.get(buyer, buyer)))
    if event.get("round") is not None:
        parts.append(f"round {event['round']}")
    if event.get("duration_ms") is not None and event.get("status") != "running":
        parts.append(f"{event['duration_ms']} ms")
    if is_reused(event):
        parts.append("reused result")
    if is_cached(event):
        parts.append("cached response")
    return " · ".join(parts)


def activity_label(totals: dict | None, *, failed: bool = False) -> str:
    """The collapsed row: 'Agent activity · Completed · 45.09s'. Status and recorded wall time
    only. The time is for the whole response, never a sum of step durations."""
    parts = ["Agent activity", ":red[Failed]" if failed else "Completed"]
    wall = (totals or {}).get("wall_time_s")
    if wall is not None:
        parts.append(f"{wall}s")
    return " · ".join(parts)


def live_label(events: list[dict], names: dict[str, str] | None = None,
               default: str = "Researching buyers…") -> str:
    """What the agent is doing now, from the latest step still running."""
    names = names or {}
    running = [event for event in merge_steps(events) if event.get("status") == "running"]
    if not running:
        return default
    event = running[-1]
    buyer = event.get("acquirer_key")
    who = f" for {names.get(buyer, buyer)}" if buyer else ""
    if event.get("category") == "mcp_tool":
        return f"Calling {event.get('name')}{who}…"
    return f"{step_name(event)}{who}…"


def _identity(value):
    return value


def _issues(event: dict) -> list:
    output = event.get("output")
    if event.get("category") == "validation" and isinstance(output, list):
        return [item for item in output if str(item).strip()]
    return []


def always_shown(event: dict) -> bool:
    """A failed step, or a check that found issues. These stay visible on every filter."""
    if event.get("status") == "failed":
        return True
    summary = event.get("output_summary")
    if isinstance(summary, dict) and summary.get("issue_count"):
        return True
    return bool(_issues(event))


def _show_output(event: dict, key: str | None, scrub) -> None:
    """The recorded output, redacted. Large outputs show a labelled preview and a download.

    Called only when an output was recorded. A missing output is handled by the caller, so this
    never draws an empty box that looks like a response.
    """
    output = event["output"]
    preview, full, truncated = preview_json(output)
    preview, full = scrub(preview), scrub(full)
    if truncated:
        st.caption(f"Preview truncated: the first {PREVIEW_LIMIT:,} of {len(full):,} characters.")
    st.code(preview, language="json" if not isinstance(output, str) else None)
    if truncated and key is None:
        st.caption("The full output can be downloaded when this response has finished.")
    elif truncated:
        st.download_button("Download full output (JSON)", full,
                           file_name=f"{key}-output.json", mime="application/json",
                           key=f"{key}-json", on_click="ignore")


OUTPUT_TITLES = {
    "mcp_tool": ("Raw tool response",
                 "The tool result as the app recorded it (with any repeated-call warning the "
                 "app added), not the MCP wire message."),
    "llm": ("Visible text preview",
            "At most the first 300 characters of the model's visible reply. The full response "
            "is not recorded and private reasoning is never shown."),
    "validation": ("Issues found", ""),
}


def show_step(event: dict, key: str | None, names: dict[str, str] | None = None, scrub=None,
              live: bool = False) -> None:
    """One step as an expandable row. `key` must name the run, the response and the step.

    Rows drawn while a response runs are redrawn in place, so they pass `key=None` and carry
    no widget keys. `live` says the response is still going, so `running` means Running.
    """
    names = names or {}
    scrub = scrub or _identity
    label = as_markdown(scrub(step_title(event, names, live)))
    with st.expander(label, key=None if key is None else f"{key}-row"):
        facts = [f"`{scrub(str(event.get('name') or '—'))}`", category_label(event.get("category")),
                 f"step {event.get('step')}"]
        buyer = event.get("acquirer_key")
        if buyer:
            facts.append(as_markdown(f"buyer {names.get(buyer, buyer)}"))
        st.caption(" · ".join(facts))
        if is_reused(event):
            st.caption(REUSED_NOTE)
        if is_cached(event):
            st.caption(CACHED_NOTE)
        result = event.get("decision") or event.get("message")
        if result:
            st.markdown(as_markdown(f"**Result:** {scrub(str(result))}"))
        counts = summary_text(event.get("output_summary"))
        if counts:
            st.caption(as_markdown(scrub(counts)))
        st.markdown("**Parameters sent**" if event.get("category") == "mcp_tool"
                    else "**Request details**" if event.get("category") == "llm" else "**Input**")
        if event.get("input") is None:
            st.caption("No input recorded.")
        else:
            inputs = event["input"]
            if event.get("category") == "mcp_tool" and isinstance(inputs, dict):
                for field, value in inputs.items():
                    shown = json_text(value) if isinstance(value, (dict, list)) else str(value)
                    st.caption(as_markdown(scrub(f"{field.replace('_', ' ').capitalize()}: {shown}")))
                with st.expander("Input JSON", key=None if key is None else f"{key}-input"):
                    st.code(scrub(json_text(inputs)), language="json")
            else:
                st.code(scrub(json_text(inputs)), language="json")
        if event.get("error"):
            st.error(as_markdown(scrub(str(event["error"]))))
        attempts = event.get("attempts")
        if isinstance(attempts, list) and len(attempts) > 1:
            st.caption(f"Attempts: {len(attempts)}")
            st.code(scrub(json_text(attempts)), language="json")
        usage = [pattern.format(event[field]) for field, pattern in
                 (("tokens_in", "tokens in {}"), ("tokens_out", "tokens out {}"),
                  ("cost_usd", "cost ${}")) if event.get(field) is not None]
        if usage:
            st.caption(as_markdown(" · ".join(usage)))
        if event.get("status") == "running":
            return
        _show_recorded(event, key, scrub)


def _show_recorded(event: dict, key: str | None, scrub) -> None:
    """The output section. An empty app event gets nothing; a tool with no output says so."""
    category = event.get("category")
    if category == "validation":
        issues = _issues(event)
        if not issues:
            st.caption("No issues found.")
            return
        with st.expander("Issues found", key=None if key is None else f"{key}-out"):
            for issue in issues:
                st.markdown(as_markdown(f"- {scrub(str(issue))}"))
        return
    if event.get("output") is None:
        if category == "mcp_tool":
            st.caption("No output recorded.")
        return
    title, note = OUTPUT_TITLES.get(category, ("Recorded output", ""))
    with st.expander(title, key=None if key is None else f"{key}-out"):
        if note:
            st.caption(note)
        _show_output(event, key, scrub)


def stage_groups(events):
    """Partition saved steps once; unknown/setup events remain inspectable."""
    from ma_engine.research.agent import RESEARCH_STAGES
    groups = [{**stage, "steps": []} for stage in RESEARCH_STAGES]
    technical = {"id": "technical", "label": "Technical event log", "steps": []}
    for event in order_events(events):
        pair = (event.get("category"), event.get("name"))
        group = next((g for g in groups if pair in g["events"]), technical)
        group["steps"].append(event)
    return [*groups, technical]


def stage_result(group, reports=None):
    """Observable outcomes only; no model interpretation or replayed calls."""
    steps = group["steps"]
    if not steps:
        return "No steps recorded."
    failures = sum(e.get("status") == "failed" for e in steps)
    checks = [e for e in steps if e.get("category") == "validation"]
    latest = {e.get("acquirer_key"): e for e in checks}
    unresolved = sum(bool(_issues(e) or (e.get("output_summary") or {}).get("issue_count"))
                     for e in latest.values())
    if group["id"] == "reports" and reports is not None:
        verified = sum(r.status == "verified" for r in reports)
        body = f"{len(reports)} reports · {verified} verified · {len(reports)-verified} need review"
    elif group["id"] == "identify":
        summary = next((e.get("output_summary") or {} for e in reversed(steps)
                        if e.get("name") == "ranking"), {})
        body = (f"{summary['candidates']} candidates evaluated · {summary['shortlist']} shortlisted"
                if "candidates" in summary and "shortlist" in summary else f"{len(steps)} recorded steps")
    else:
        calls = sum(e.get("category") == "mcp_tool" for e in steps)
        body = f"{calls} recorded MCP calls" if calls else f"{len(steps)} recorded steps"
        buyers = {e.get("acquirer_key") for e in steps if e.get("acquirer_key")}
        if buyers:
            body += f" · {len(buyers)} buyers"
    if failures:
        body += f" · {failures} failed steps"
    if unresolved:
        body += f" · {unresolved} latest checks need review"
    elif any(always_shown(e) for e in checks):
        body += " · Earlier check issues recorded; latest checks passed"
    warnings = sum(bool((e.get("output_summary") or {}).get("warnings")) for e in steps)
    if warnings:
        body += f" · {warnings} steps with warnings"
    return body


def render_stages(events, key_prefix, names=None, scrub=None, reports=None):
    scrub = scrub or _identity
    merged = merge_steps(events)
    buyers = list(dict.fromkeys(e.get("acquirer_key") for e in merged if e.get("acquirer_key")))
    if len(buyers) > 1:
        chosen = st.selectbox("Buyer", ["all", *buyers],
                              format_func=lambda key: "All buyers" if key == "all" else (names or {}).get(key, key),
                              key=f"{key_prefix}-buyer")
        if chosen != "all":
            events = [e for e in events if e.get("acquirer_key") == chosen]
            merged = merge_steps(events)
            if reports is not None:
                reports = [r for r in reports if r.acquirer_key == chosen]
    calls = sum(e.get("category") == "mcp_tool" for e in merged)
    cached = sum(is_cached(e) for e in merged if e.get("category") == "llm")
    st.caption(f"{calls} recorded MCP calls · {cached} cached model responses")
    for group in stage_groups(events):
        rows = group["steps"]
        attention = any(always_shown(e) or (e.get("output_summary") or {}).get("warnings")
                        for e in rows)
        if group["id"] == "reports" and reports is not None:
            attention = attention or any(r.status != "verified" for r in reports)
        status = ("Failed" if any(e.get("status") == "failed" for e in rows) else
                  "Not finished" if any(e.get("status") == "running" for e in rows) else
                  "Review notes" if attention else "Completed" if rows else "Not recorded")
        label = group["label"] if group["id"] == "technical" else f"{group['label']} · {status}"
        with st.expander(label, key=f"{key_prefix}-stage-{group['id']}"):
            st.caption(as_markdown(scrub(stage_result(group, reports))))
            if group["id"] == "comps":
                seen = set()
                for event in rows:
                    output = event.get("output")
                    data = output.get("data", {}) if isinstance(output, dict) else {}
                    median = (data.get("ev_ebitda") or {}).get("median") or {}
                    if not median.get("display"):
                        continue
                    line = (f"{data.get('sector', 'Not recorded')} · "
                            f"{str(data.get('scope', 'Not recorded')).replace('_', ' ')} · "
                            f"{data.get('size_band') or 'All sizes'} · "
                            f"Median EV/EBITDA {median['display']} (n={median.get('n', 'not recorded')})")
                    if line not in seen:
                        st.caption(as_markdown(scrub(line)))
                        seen.add(line)
            for event in rows:
                show_step(event, f"{key_prefix}-stage-{group['id']}-{event.get('step')}", names, scrub)


class LiveSteps:
    """Steps drawn while a response runs. Each step has its own slot, so the `completed`
    event for a step replaces that step's `running` row instead of adding a second one."""

    def __init__(self, container, names_of=None, scrub=None, limit: int = PAGE_SIZE, grouped=False):
        self.container, self.names_of, self.scrub, self.limit = container, names_of, scrub, limit
        self.events: list[dict] = []
        self.slots: dict[int, object] = {}
        self.more = None
        self.groups = {}
        if grouped:
            for group in stage_groups([]):
                panel = container.expander(group["label"])
                self.groups[group["id"]] = (panel, panel.empty())

    def add(self, event: dict) -> None:
        self.events.append(event)
        step = event.get("step")
        if not isinstance(step, int):
            return
        if step not in self.slots:
            if len(self.slots) >= self.limit:
                if self.more is None:
                    self.more = self.container.empty()
                self.more.caption(f"Showing the first {self.limit} steps while this runs. "
                                  "Every step is listed when it finishes.")
                return
            parent = self.container
            if self.groups:
                group = next(g for g in stage_groups([event]) if g["steps"])
                parent = self.groups[group["id"]][0]
            self.slots[step] = parent.empty()
        self._draw(step, event, live=True)
        for group in stage_groups(self.events) if self.groups else []:
            self.groups[group["id"]][1].caption(as_markdown(
                (self.scrub or _identity)(stage_result(group))))

    def _draw(self, step: int, event: dict, live: bool) -> None:
        names = self.names_of(self.events) if self.names_of else {}
        with self.slots[step].container():
            show_step(event, None, names, self.scrub, live=live)

    def stop(self) -> None:
        """The response ended with an error: steps still running are redrawn as Not finished."""
        for event in merge_steps(self.events):
            if event.get("status") == "running" and event.get("step") in self.slots:
                self._draw(event["step"], event, live=False)


def render_totals(totals: dict | None) -> None:
    """The run or follow-up totals stored by the engine. Nothing is added up here."""
    if not totals:
        st.caption("No totals were stored for this activity.")
        return

    def plain(value) -> str:
        return "—" if value is None else str(value)

    first = st.columns(3)
    wall = totals.get("wall_time_s")
    first[0].metric("Total time", "—" if wall is None else f"{wall} s")
    first[1].metric("LLM calls", plain(totals.get("llm_calls")))
    first[2].metric("MCP tool calls", plain(totals.get("mcp_tool_calls")))
    second = st.columns(3)
    tokens_in, tokens_out = totals.get("tokens_in"), totals.get("tokens_out")
    token_text = "—" if tokens_in is None and tokens_out is None else f"{plain(tokens_in)} / {plain(tokens_out)}"
    second[0].metric("Tokens in / out", token_text)
    cost = totals.get("cost_usd")
    second[1].metric("Cost", "—" if cost is None else as_markdown(f"${cost}"))
    second[2].metric("Cache hits", plain(totals.get("cache_hits")))


STEP_FILTERS = ("MCP calls", "All steps")


def _kept(event: dict, mode: str, buyer: str) -> bool:
    """MCP calls by default. All steps adds the rest. Failures and failed checks always stay."""
    if buyer != "all" and (event.get("acquirer_key") or "") != buyer:
        return False
    if mode == "All steps" or always_shown(event):
        return True
    return event.get("category") == "mcp_tool"


def render_timeline(events: list[dict], *, key_prefix: str, names: dict[str, str] | None = None,
                    buyer: str | None = None, scrub=None) -> None:
    """The recorded steps of one finished (or stopped) response, read from saved events only.

    The list opens on MCP calls. "All steps" adds LLM, validation, calculation and app events.
    A failed step, and a check that found issues, stay on screen either way. Pass `buyer` to
    lock the list to one acquirer. At most `PAGE_SIZE` rows are drawn at a time.
    """
    names = names or {}
    ordered = order_events(events)
    buyers = list(dict.fromkeys(
        event["acquirer_key"] for event in ordered if event.get("acquirer_key")))
    with st.container(horizontal=True, vertical_alignment="center", gap="small",
                      key=f"{key_prefix}-bar"):
        mode = st.segmented_control(
            "Steps", STEP_FILTERS, default="MCP calls", key=f"{key_prefix}-filter",
            label_visibility="collapsed", width="content",
            help="MCP calls, or every recorded step. Failed steps and checks that found "
                 "issues stay visible either way.")
        chosen_buyer = buyer or "all"
        if not buyer and len(buyers) > 1:
            chosen_buyer = st.selectbox(
                "Buyer", ["all", "", *buyers],
                format_func=lambda key: ("All buyers" if key == "all" else "Run-level steps"
                                         if key == "" else names.get(key, key)),
                key=f"{key_prefix}-buyer", label_visibility="collapsed", width=220)
        shown = mode or "MCP calls"
        rows = [event for event in ordered if _kept(event, shown, chosen_buyer)]
        pages = max(1, math.ceil(len(rows) / PAGE_SIZE)) if rows else 1
        page_key = f"{key_prefix}-page-{chosen_buyer}-{shown}"
        page = min(int(st.session_state.get(page_key, 0)), pages - 1)
        start = page * PAGE_SIZE
        chunk = rows[start:start + PAGE_SIZE]
        if rows:
            count = (f"{start + 1}–{start + len(chunk)} of {len(rows)}"
                     if pages > 1 else f"{len(rows)} steps")
            with st.container(horizontal=True, vertical_alignment="center", gap="xxsmall",
                              key=f"{key_prefix}-pager"):
                st.caption(count)
                if pages > 1:
                    if st.button("‹", key=f"{key_prefix}-prev", help="Previous page",
                                 type="tertiary", disabled=page <= 0):
                        st.session_state[page_key] = page - 1
                        st.rerun()
                    if st.button("›", key=f"{key_prefix}-next", help="Next page",
                                 type="tertiary", disabled=page >= pages - 1):
                        st.session_state[page_key] = page + 1
                        st.rerun()
    if not ordered:
        st.caption("No steps were recorded.")
        return
    if not rows:
        st.caption("No steps match these filters.")
        return
    if shown != "All steps" and not any(event.get("category") == "mcp_tool" for event in rows):
        st.caption("No MCP calls were recorded.")
    for event in chunk:
        show_step(event, f"{key_prefix}-{event.get('step')}", names, scrub)
