"""Draw the agent activity timeline. Display only: no ranking, no model calls.

One row per step. A `running` event and the later `completed` or `failed` event share a step
number, so the row updates in place. Full tool output is kept for download; the on-screen
preview is capped so a large result (for example rank_acquirers) does not freeze the page.
"""

import json
import math
import re

import streamlit as st

PREVIEW_LIMIT = 20_000
PAGE_SIZE = 50

CATEGORY_LABELS = {
    "llm": "LLM",
    "mcp_tool": "MCP tool",
    "validation": "Validation",
    "calculation": "Calculation",
    "app": "App",
}
# Streamlit color badges. App uses the theme navy; calculation stays gray so the two differ.
CATEGORY_BADGES = {
    "llm": ":blue-badge[LLM]",
    "mcp_tool": ":green-badge[MCP tool]",
    "validation": ":orange-badge[Validation]",
    "calculation": ":gray-badge[Calculation]",
    "app": ":primary-badge[App]",
}
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
    """Shorten text for a table cell or a row label."""
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


def _clock(ts) -> str:
    if not isinstance(ts, str) or "T" not in ts:
        return "—"
    return ts.split("T", 1)[1][:8]


def category_label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or "", category or "—")


def category_badge(category: str | None) -> str:
    return CATEGORY_BADGES.get(category or "", f":gray-badge[{category_label(category)}]")


def status_bit(status: str | None) -> str:
    """Completed stays plain text. Failed is a red badge. Running shimmers."""
    if status == "failed":
        return ":red-badge[failed]"
    if status == "running":
        return ":shimmer[running]"
    if status == "completed":
        return "completed"
    return str(status) if status else ""


def row_label(event: dict, names: dict[str, str] | None = None) -> str:
    """One scannable line. Empty buyer and round are left out. Dollars are not escaped yet."""
    names = names or {}
    parts = [f"**#{event.get('step')}**"]
    clock = _clock(event.get("ts"))
    if clock != "—":
        parts.append(clock)
    parts.append(category_badge(event.get("category")))
    if event.get("name"):
        parts.append(str(event["name"]))
    buyer = event.get("acquirer_key")
    if buyer:
        parts.append(str(names.get(buyer, buyer)))
    if event.get("round") is not None:
        parts.append(f"R{event['round']}")
    bit = status_bit(event.get("status"))
    if bit:
        parts.append(bit)
    if event.get("duration_ms") is not None:
        parts.append(f"{event['duration_ms']} ms")
    label = " · ".join(parts)
    decision = clip(event.get("decision") or "", 140)
    if decision:
        label = f"{label} — {decision}"
    return label


def buyer_status_lines(events: list[dict]) -> tuple[str, str]:
    """`(mcp_line, markdown)` for the live status box.

    One line per buyer, replaced as later events for that buyer arrive. The full `output`
    is never included.
    """
    mcp = ""
    lines: dict[str, str] = {}
    for event in order_events(events):
        if event.get("name") == "mcp_connected" and event.get("status") == "completed":
            count = (event.get("output_summary") or {}).get("tools") if isinstance(
                event.get("output_summary"), dict) else None
            mcp = event.get("message") or (
                f"MCP server: {count} tools" if count else "MCP server connected")
        key = event.get("acquirer_key") or f"_{event.get('name')}"
        bits = [f"**{event.get('acquirer_key') or event.get('name')}**"]
        if event.get("round") is not None:
            bits.append(f"R{event['round']}")
        bits.append(category_badge(event.get("category")))
        bits.append(str(event.get("name") or "—"))
        bit = status_bit(event.get("status"))
        if bit:
            bits.append(bit)
        summary = summary_text(event.get("output_summary"))
        if summary:
            bits.append(summary)
        lines[key] = " · ".join(bits)
    body = "\n".join(f"- {line}" for line in lines.values())
    return mcp, body


def live_timeline_md(events: list[dict], question: str | None, names: dict[str, str] | None = None) -> str:
    """Live timeline lines. Summaries only, capped at one page. Dollars are escaped by the caller."""
    rows = order_events(events)
    lines = []
    if question:
        lines.append(f"**Question:** {clip(question, 400)}")
        lines.append("")
    if not rows:
        lines.append("_No steps yet._")
    else:
        lines.extend(f"- {row_label(event, names)}" for event in rows[:PAGE_SIZE])
        if len(rows) > PAGE_SIZE:
            lines.append("")
            lines.append(f"Showing the first {PAGE_SIZE} of {len(rows)} steps while the run is going.")
    return "\n".join(lines)


def _plain(value) -> str:
    return "—" if value is None else str(value)


def render_totals(totals: dict | None) -> None:
    """The run or follow-up totals stored by the engine. Nothing is added up here."""
    if not totals:
        st.caption("No totals were stored for this activity.")
        return
    first = st.columns(3)
    wall = totals.get("wall_time_s")
    first[0].metric("Total time", "—" if wall is None else f"{wall} s")
    first[1].metric("LLM calls", _plain(totals.get("llm_calls")))
    first[2].metric("MCP tool calls", _plain(totals.get("mcp_tool_calls")))
    second = st.columns(3)
    tokens_in, tokens_out = totals.get("tokens_in"), totals.get("tokens_out")
    if tokens_in is None and tokens_out is None:
        token_text = "—"
    else:
        token_text = f"{_plain(tokens_in)} / {_plain(tokens_out)}"
    second[0].metric("Tokens in / out", token_text)
    cost = totals.get("cost_usd")
    second[1].metric("Cost", "—" if cost is None else as_markdown(f"${cost}"))
    second[2].metric("Cache hits", _plain(totals.get("cache_hits")))


def _show_output(event: dict, prefix: str, scrub) -> None:
    st.markdown("**Output summary**")
    summary = event.get("output_summary")
    if summary is None:
        st.caption("No output summary.")
    else:
        st.json(summary, expanded=False)
    st.markdown("**Full output**")
    output = event.get("output")
    if output is None:
        st.caption("No output recorded.")
        return
    preview, full, truncated = preview_json(output)
    if scrub:
        preview = scrub(preview)
        full = scrub(full)
    if truncated:
        st.caption(as_markdown(
            f"This output is {len(full):,} characters. "
            f"The box below is the first {PREVIEW_LIMIT:,} characters."
        ))
        st.code(preview, language="json")
        st.download_button(
            "Download full JSON",
            full,
            file_name=f"step-{event.get('step')}-output.json",
            mime="application/json",
            key=f"{prefix}-json-{event.get('step')}",
        )
        return
    st.json(output, expanded=False)


def _show_row(event: dict, prefix: str, names: dict[str, str], scrub) -> None:
    with st.expander(as_markdown(row_label(event, names)), expanded=False):
        st.markdown(category_badge(event.get("category")))
        if event.get("decision"):
            decision = scrub(event["decision"]) if scrub else event["decision"]
            st.markdown(as_markdown(f"**Decision:** {decision}"))
        elif event.get("message"):
            message = scrub(event["message"]) if scrub else event["message"]
            st.caption(as_markdown(message))
        st.markdown("**Input**")
        if event.get("input") is None:
            st.caption("No input recorded.")
        else:
            st.json(event["input"], expanded=True)
        _show_output(event, prefix, scrub)
        if event.get("error"):
            text = str(event["error"])
            st.error(as_markdown(scrub(text) if scrub else text))
        attempts = event.get("attempts")
        if isinstance(attempts, list) and attempts:
            st.markdown(f"**Attempts:** {len(attempts)}")
            st.json(attempts, expanded=False)
        usage = []
        if event.get("tokens_in") is not None:
            usage.append(f"tokens in {event['tokens_in']}")
        if event.get("tokens_out") is not None:
            usage.append(f"tokens out {event['tokens_out']}")
        if event.get("cost_usd") is not None:
            usage.append(f"cost ${event['cost_usd']}")
        if usage:
            st.caption(as_markdown(" · ".join(usage)))


def render_timeline(events: list[dict], *, question: str | None, totals: dict | None,
                    key_prefix: str, names: dict[str, str] | None = None,
                    buyer: str | None = None, scrub=None) -> None:
    """Interactive timeline for a finished run or follow-up, live or reopened.

    Pass `buyer` to lock the list to one acquirer (the buyer-detail tab). Otherwise the
    reader can filter by buyer and by category. At most `PAGE_SIZE` rows are drawn.
    """
    names = names or {}
    if question:
        st.markdown(as_markdown(f"**Question:** {question}"))
    render_totals(totals)
    ordered = order_events(events)
    buyers = list(dict.fromkeys(
        event["acquirer_key"] for event in ordered if event.get("acquirer_key")))
    if buyer:
        st.caption(as_markdown(f"Buyer: {names.get(buyer, buyer)}"))
        chosen_buyer = buyer
    else:
        chosen_buyer = st.selectbox(
            "Buyer",
            ["all", *buyers],
            format_func=lambda key: "All buyers" if key == "all" else names.get(key, key),
            key=f"{key_prefix}-buyer",
        )
    chosen_category = st.selectbox(
        "Category",
        ["all", *CATEGORY_LABELS],
        format_func=lambda key: "All categories" if key == "all" else CATEGORY_LABELS[key],
        key=f"{key_prefix}-category",
    )
    rows = [
        event for event in ordered
        if (chosen_buyer == "all" or event.get("acquirer_key") == chosen_buyer)
        and (chosen_category == "all" or event.get("category") == chosen_category)
    ]
    if not rows:
        st.caption("No steps match these filters.")
        return
    pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page_key = f"{key_prefix}-page-{chosen_buyer}-{chosen_category}"
    page = min(int(st.session_state.get(page_key, 0)), pages - 1)
    start = page * PAGE_SIZE
    chunk = rows[start:start + PAGE_SIZE]
    st.caption(f"Showing steps {start + 1}–{start + len(chunk)} of {len(rows)}.")
    if pages > 1:
        previous, _middle, nxt = st.columns([1, 2, 1])
        if previous.button("Previous", key=f"{key_prefix}-prev", disabled=page <= 0):
            st.session_state[page_key] = page - 1
            st.rerun()
        if nxt.button("Next", key=f"{key_prefix}-next", disabled=page >= pages - 1):
            st.session_state[page_key] = page + 1
            st.rerun()
    st.caption(
        "Each buyer is grouped together and that buyer's rounds run in order. "
        "The step number is when it happened, so you can see what the agent did next."
    )
    with st.container(height=560):
        for event in chunk:
            _show_row(event, key_prefix, names, scrub)
