"""Turn a saved run into report HTML. Display only.

Numbers on the page are display strings already stored on the run (or the
dataset's stored row count). A clicked `[n]` shows the run's own registry
record; a statistic's transactions are looked up by id, never recalculated.
Deal fields print with the same formatters the tools use. A stage's time is the clock span of its recorded events, from the first
start to the last finish, so parallel buyers are not added together. When an
event has no timestamp, the recorded durations are added instead.
"""

import html
import re
from datetime import datetime
from types import SimpleNamespace

from ma_engine.agent import RESEARCH_STAGES
from ma_engine.clients.data_client import get_client
from ma_engine.clients.llm_client import scrub_secret
from ma_engine.guardrails import SECTIONS
from ma_engine.models import ANY_REGION
from ma_engine.prepare import fmt_money, fmt_multiple, fmt_pct
from ma_engine.ui_trace import merge_steps

# The six sections a banker reads, in report order.
REPORT_SECTIONS = (
    ("acquirer_overview", "Acquirer Overview", "1. Overview"),
    ("strategic_fit", "Strategic Fit Thesis", "2. Strategic Fit"),
    ("precedent_activity", "Precedent Activity", "3. Precedent Activity"),
    ("valuation_context", "Valuation Context", "4. Valuation Context"),
    ("risks", "Risk Flags", "5. Risk Flags"),
    ("conviction", "Conviction", "6. Conviction"),
)
TRANSACTIONS_SHOWN = 5
STRONGEST_SHOWN = 3
FILTER_NAMES = {"acquirer_key": "Buyer", "size_band": "Size band", "signal": "Ranking signal",
                "deal_type": "Deal type", "financing_type": "Financing"}
FILTER_VALUES = {"sector_only": "target sector only",
                 "sector_plus_adjacent": "target sector and adjacent sectors",
                 "ev_ebitda": "EV/EBITDA", "ev_revenue": "EV/Revenue",
                 "ebitda_margin": "EBITDA margin"}
RANK_METRICS = (
    ("relevant_closed_count", "relevant closed deals"),
    ("direct_closed_count", "closed deals in the target sector"),
    ("near_size_closed_count", "within 2x of the target's size"),
)
PROFILE_METRICS = (
    ("deal-size-median", "median closed deal size"),
    ("ev-ebitda-median", "median EV/EBITDA, closed deals"),
)


def esc(text) -> str:
    """HTML-escape text. Dollar signs are escaped later, once, for Streamlit."""
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


def money_text(value) -> str:
    """The stored amount in the tool display form, such as `$200.0M`."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return fmt_money(value) or ""


def status_text(status: str | None) -> str:
    return {"verified": "Verified", "failed": "Failed"}.get(status or "", "Not verified")


def status_class(status: str | None) -> str:
    return {"verified": "verified", "failed": "failed"}.get(status or "", "not-verified")


def conviction_level(report) -> str:
    """The report's conviction, or the starting level when no report was written."""
    if report.draft is not None:
        return report.draft.conviction.level
    return report.starting_level


def conviction_class(level: str | None) -> str:
    return {"High": "high", "Medium": "medium", "Low": "low"}.get(level or "", "low")


def pill(label, kind: str) -> str:
    return f'<span class="pill pill-{kind}">{esc(label)}</span>'


def source_index(run: dict) -> dict[str, dict]:
    """The run's citation registry by source id: `{id: {number, id, kind, record}}`."""
    entries = ((run or {}).get("sources") or {}).get("entries") or []
    return {entry["id"]: entry for entry in entries if entry.get("id")}


def _deal_lookup(transaction_id: str, sources: dict) -> dict | None:
    entry = sources.get(transaction_id)
    if entry and entry.get("kind") == "deal":
        return entry.get("record")
    return get_client().deal_record(transaction_id)


def _filter_text(key: str, value) -> str:
    label = FILTER_NAMES.get(key, key.replace("_", " ").capitalize())
    if key == "closed_only":
        return "Closed deals only" if value else "All outcomes"
    if key == "acquirer_key":
        profile = get_client().acquirer_profiles.get(str(value), {})
        return f"{label}: {profile.get('display_name') or value}"
    if isinstance(value, (list, tuple)):
        return f"{label}: {', '.join(str(item) for item in value) or 'Not recorded'}"
    shown = FILTER_VALUES.get(str(value), str(value).replace("_", " "))
    return f"{label}: {shown}"


def deal_line(record: dict) -> str:
    """'MA-2021-0407 · Peak Medical Management · 2021 · $341.7M · Closed'."""
    bits = [record.get("transaction_id"), record.get("target_company"), record.get("deal_year"),
            money_text(record.get("deal_size_mm")), record.get("outcome")]
    return " · ".join(str(bit) for bit in bits if bit not in (None, ""))


def deal_view_html(record: dict) -> str:
    year, quarter = record.get("deal_year"), record.get("deal_quarter")
    pairs = [
        ("Target", record.get("target_company")),
        ("Buyer", record.get("acquirer")),
        ("Sector", record.get("sector")),
        ("Deal type", record.get("deal_type")),
        ("Geography", record.get("geography")),
        ("Deal size", money_text(record.get("deal_size_mm"))),
        ("Date", " ".join(str(part) for part in (year, quarter) if part not in (None, ""))),
        ("Outcome", record.get("outcome")),
        ("EV/EBITDA", fmt_multiple(record.get("ev_ebitda_multiple"))),
        ("EV/Revenue", fmt_multiple(record.get("ev_revenue_multiple"))),
        ("EBITDA margin", fmt_pct(record.get("ebitda_margin_pct"))),
        ("Ownership", record.get("target_ownership_pre")),
        ("Financing", record.get("financing_type")),
        ("Bidders", record.get("num_bidders")),
        ("Days to close", record.get("days_to_close")),
    ]
    return _pairs_html([(label, "Not recorded" if value in (None, "") else str(value))
                        for label, value in pairs])


def stat_view_html(record: dict, sources: dict) -> str:
    """Calculation, value, filters, sample size, then the transactions behind it."""
    filters = record.get("filters") or {}
    sample = record.get("n")
    calculation = str(record.get("calculation") or "")
    parts = [_pairs_html([
        ("Calculation", calculation[:1].upper() + calculation[1:]),
        ("Value", str(record.get("display") or "")),
        ("Filters", "; ".join(_filter_text(key, value) for key, value in filters.items())),
        ("Records counted" if calculation.startswith("count of") else "Sample size",
         "Not applicable / not recorded" if sample is None else f"n = {sample}"),
    ])]
    ids = list(record.get("source_ids") or [])
    if not ids:
        if str(record.get("stat_id") or "").endswith("-score"):
            parts.append('<p class="cite-note">Blended from the six ranking signals. '
                         "Each signal and its weight is in the Score Breakdown tab.</p>")
        return "".join(parts)
    # Legacy count citations can carry a broader context list. Do not pretend
    # those records are the exact contributing sample, or silently rewrite it.
    legacy_context = calculation.startswith("count of") and sample is not None and sample != len(ids)
    if legacy_context:
        parts.append('<p class="cite-note">This saved citation lists broader evidence, '
                     'not the exact records counted. The saved count is unchanged.</p>')
    lines = [esc(deal_line(_deal_lookup(i, sources) or {"transaction_id": i})) for i in ids]
    shown = "".join(f"<li>{line}</li>" for line in lines[:TRANSACTIONS_SHOWN])
    parts.append(f'<p class="cite-sub">{"Evidence records" if legacy_context else "Transactions used"} ({len(ids)})</p><ul>{shown}</ul>')
    if len(lines) > TRANSACTIONS_SHOWN:
        every = "".join(f"<li>{line}</li>" for line in lines)
        parts.append(f'<details class="cite-more"><summary>Show all {len(ids)} transactions'
                     f"</summary><ul>{every}</ul></details>")
    return "".join(parts)


def source_view_html(kind: str, record: dict, sources: dict, title: str) -> str:
    if str(record.get("stat_id", "")).startswith("ST-RUN-"):
        import json
        body = '<p>Saved run metadata; not evidence of a transaction.</p><pre>' + esc(
            json.dumps(record.get("details") or {}, ensure_ascii=False, indent=2)) + '</pre>'
        technical = ''
    elif kind == "deal":
        body = deal_view_html(record)
        technical = ('<details class="cite-tech"><summary>Source identifier</summary>'
                     f'<code>{esc(record.get("transaction_id"))}</code></details>')
    else:
        body = stat_view_html(record, sources)
        technical = (f'<details class="cite-tech"><summary>Technical details</summary>'
                     f'<code>{esc(record.get("stat_id"))}</code></details>')
    noun = "Transaction" if kind == "deal" else "Statistic"
    return (f'<div class="cite-pop"><div class="cite-head"><span class="cite-title">'
            f'{esc(title)} · {noun}</span>'
            f'<button type="button" class="cite-close" aria-label="Close">×</button></div>'
            f'<div class="cite-body">{body}{technical}</div></div>')


def cite_html(entry: dict, sources: dict) -> str:
    """A `[n]` control. One shared popover, anchored to this number, shows the source."""
    number = int(entry["number"])
    view = source_view_html(entry.get("kind"), entry.get("record") or {}, sources,
                            f"Source [{number}]")
    return (f'<details class="cite" data-source="{esc(entry["id"])}">'
            f'<summary class="cite-btn" aria-haspopup="dialog" aria-expanded="false">'
            f'[{number}]</summary>{view}</details>')


def cite_links(source_ids, sources: dict) -> str:
    """`[n]` for ids in the run's registry. An id with no registry entry is shown as text."""
    links = []
    seen = set()
    for source_id in source_ids or []:
        if source_id in seen:
            continue
        seen.add(source_id)
        entry = (sources or {}).get(source_id)
        if entry is None:
            links.append(f'<span class="cite-raw">[{esc(source_id)}]</span>')
        else:
            links.append(cite_html(entry, sources))
    return "".join(links)


def _with_links(text: str, source_ids, sources: dict) -> str:
    links = cite_links(source_ids, sources)
    # Remove parenthetical technical IDs only when a resolved citation replaces
    # them. Keep IDs embedded in legacy sentence grammar rather than corrupting
    # the sentence by deleting its subject or object.
    for sid in source_ids or []:
        if sid in sources:
            text = re.sub(r"\s*\(" + re.escape(sid) + r"\)", "", str(text))
    text = re.sub(r"[ \t]{2,}", " ", text)
    body = esc(text)
    return f"{body}{links}" if links else body


def statement_html(statement, sources: dict) -> str:
    """Facts plain, interpretation underlined with a hover label, unknowns named as such."""
    kind = getattr(statement, "kind", None)
    text = statement.text
    if kind == "unknown":
        text = f"Not in the dataset: {text}"
    body = _with_links(text, statement.source_ids, sources)
    if kind == "interpretation":
        return f'<span class="interp" title="Interpretation of the cited facts">{body}</span>'
    return body


def paragraph_html(statements, sources: dict) -> str:
    if not statements:
        return esc("No statements in this section.")
    return " ".join(statement_html(statement, sources) for statement in statements)


def section_html(draft, which: str, sources: dict) -> str:
    """One section as paragraphs. `which` is a report field or `risks` / `conviction`."""
    if draft is None:
        return ""
    if which == "risks":
        if not draft.risk_flags:
            return f'<div class="para">{esc("No risk flags.")}</div>'
        parts = []
        for flag in draft.risk_flags:
            lead = SimpleNamespace(text=flag.explanation, kind=flag.kind,
                                   source_ids=flag.source_ids)
            parts.append(f'<div class="para"><strong>{esc(flag.risk)}.</strong> '
                         f"{statement_html(lead, sources)}</div>")
        return "".join(parts)
    if which == "conviction":
        conviction = draft.conviction
        return (f'<div class="para"><strong>{esc(conviction.level)}.</strong> '
                f"{_with_links(conviction.reason, conviction.source_ids, sources)}</div>")
    return f'<div class="para">{paragraph_html(getattr(draft, which), sources)}</div>'


def report_body_html(draft, sources: dict) -> str:
    """The summary, then the six sections as one continuous document."""
    parts = [f'<div class="para lead">{statement_html(draft.summary, sources)}</div>']
    for key, title, _tab in REPORT_SECTIONS:
        parts.append(f"<h3>{esc(title)}</h3>{section_html(draft, key, sources)}")
    return f'<div class="report-doc">{"".join(parts)}</div>'


def metric_items(entry: dict | None, profile_records, sources: dict) -> list[dict]:
    """Strip figures for one buyer: ranking counts, then its closed-deal medians.

    Each item keeps the stored display string and the record it came from. A cited
    statistic uses the run's registry record; otherwise the ranking entry or the
    buyer profile record is shown as the source.
    """
    items = []
    entry = entry or {}
    for field, label in RANK_METRICS:
        stat = entry.get(field)
        if not isinstance(stat, dict) or not stat.get("display"):
            continue
        record = (sources.get(stat.get("stat_id")) or {}).get("record") or {
            "stat_id": stat.get("stat_id"), "calculation": stat.get("calculation"),
            "filters": {"acquirer_key": entry.get("acquirer_key"), "signal": field},
            "n": stat.get("value"), "display": stat.get("display"),
            "source_ids": stat.get("source_ids", entry.get("supporting_closed_ids") or [])}
        items.append({"label": label, "display": stat["display"], "record": record})
    profile = {record.get("stat_id", "").rsplit("-closed-", 1)[-1]: record
               for record in profile_records or []}
    for suffix, label in PROFILE_METRICS:
        record = profile.get(suffix)
        if record and record.get("display"):
            items.append({"label": label, "display": record["display"], "record": record})
    return items


def metrics_strip_html(items: list[dict], sources: dict) -> str:
    if not items:
        return ""
    cells = []
    for item in items:
        record = item["record"]
        entry = sources.get(record.get("stat_id"))
        if entry is not None:
            link = cite_html(entry, sources)
        else:
            view = source_view_html("statistic", record, sources, "Source")
            link = ('<details class="cite"><summary class="cite-btn" aria-haspopup="dialog" '
                    'aria-expanded="false" title="Source">ⓘ</summary>'
                    f'{view}</details>')
        cells.append(f'<div class="m"><div class="mv">{esc(item["display"])}{link}</div>'
                     f'<div class="ml">{esc(item["label"])}</div></div>')
    return f'<div class="metric-strip">{"".join(cells)}</div>'


# A phrase from a saved check, then a short sentence. The first match wins.
# The original issue text is still shown in full under Verification details.
_ISSUE_LINES = (
    ("is not the sample size", "A reported sample size does not match its cited evidence."),
    ("has no sample size", "A valuation statement does not state its sample size."),
    ("does not appear in the tool results", "A number in the report does not appear in the tool results."),
    ("is not backed by a cited statistic", "A number is not backed by a cited statistic."),
    ("is a comps or other-buyer figure",
     "A comps figure is used where this buyer's own figures are required."),
    ("is a hand-written assumption", "A number is an assumption, not a figure from a cited deal."),
    ("is not the year of any cited deal", "A year in the report is not the year of a cited deal."),
    ("but uses completion word", "A deal that did not close is described as completed."),
    ("must cite closed deals only", "A valuation statement cites a deal that did not close."),
    ("but no cited deal is a ", "A deal type in the text does not match the cited deals."),
    ("but no cited deal is ", "A deal outcome in the text does not match the cited deals."),
    ("in a statement that says", "A named deal is described with another deal's outcome."),
    ("does not exist in the dataset", "A citation names an ID that is not in the dataset."),
    ("is not a transaction ID", "A citation is not a transaction ID."),
    ("which is not in the tool results for this buyer",
     "The report names a company that is not in this buyer's tool results."),
    ("not in the tool results", "A citation uses an ID that was not in this buyer's tool results."),
    ("source_ids does not", "The text names an ID that is not in its citation list."),
    ("cites no transaction ID", "A statement cites no transaction ID."),
    ("not this buyer's deal", "A statement cites a deal that is not this buyer's."),
    ("own statistic", "A statement cites a statistic that is not this buyer's."),
    ("cites only comps", "A statement cites other buyers' deals instead of this buyer's."),
    ("is not part of any cited deal", "The report names a company that is not part of a cited deal."),
    ("is the target of", "The report names a company from a deal it does not cite."),
    ("is the buyer in", "The report names a company from a deal it does not cite."),
    ("unknown statements must not contain numbers", "An unknown statement includes a number."),
    ("unknown statements must not cite", "An unknown statement cites a transaction ID."),
    ("risk_flags: has", "The report does not list enough different risks."),
    ("repeats the risk", "Two risk flags repeat the same risk."),
    ("steps from the starting", "The conviction moves more than one step from the starting level."),
    ("starting level", "The starting conviction does not match the ranking."),
    ("but the evidence is for", "The report is not for the buyer that was researched."),
    ("is empty", "A required section or statement is empty."),
)


def explain_issue(issue: str) -> str:
    """A short sentence for one saved check. Unknown checks keep their own text."""
    text = scrub_secret(str(issue or "")).strip()
    if not text:
        return ""
    for phrase, sentence in _ISSUE_LINES:
        if phrase in text:
            return sentence
    return text


def report_issue_texts(report) -> list[str]:
    """The saved issues, plus the error when it is not already one of them."""
    issues = [scrub_secret(str(issue)) for issue in (report.issues or [])]
    error = scrub_secret(str(report.error)) if report.error else ""
    if error and error not in issues:
        issues.append(error)
    return [issue for issue in issues if issue]


def verification_lines(report) -> list[str]:
    """Plain-language lines for this report. Empty when it passed with no checks to show."""
    if report.status == "failed":
        text = scrub_secret(str(report.error)).strip() if report.error else ""
        return [text or "Research failed before a report was written."]
    lines = []
    for issue in report.issues or []:
        line = explain_issue(issue)
        if line and line not in lines:
            lines.append(line)
    if not lines and report.status != "verified" and report.error:
        lines.append(scrub_secret(str(report.error)).strip())
    return lines


def verification_html(report) -> str:
    """The status explanation under a buyer's name, with the original checks collapsed.

    A verified report returns nothing, so there is no empty details box.
    """
    lines = verification_lines(report)
    if not lines:
        return ""
    originals = report_issue_texts(report)
    if report.status == "failed" and report.error:
        shown = scrub_secret(str(report.error)).strip()
        if shown and shown not in originals:
            originals.insert(0, shown)
    body = "".join(f'<p class="verify-line">{esc(line)}</p>' for line in lines)
    if originals:
        items = "".join(f"<li>{esc(item)}</li>" for item in originals)
        body += ('<details class="verify-details"><summary>Verification details</summary>'
                 f"<ul>{items}</ul></details>")
    kind = "fail" if report.status == "failed" else "warn"
    return f'<div class="verify {kind}">{body}</div>'


def buyer_source_ids(report) -> list[str]:
    """Citation ids in the order the report uses them. Empty when there is no draft."""
    draft = report.draft
    if draft is None:
        return []
    found = list(draft.summary.source_ids)
    for section in SECTIONS:
        for statement in getattr(draft, section):
            found.extend(statement.source_ids)
    for flag in draft.risk_flags:
        found.extend(flag.source_ids)
    found.extend(draft.conviction.source_ids)
    ordered = []
    for source_id in found:
        if source_id not in ordered:
            ordered.append(source_id)
    return ordered


def _target_data(target) -> dict:
    if hasattr(target, "model_dump"):
        return target.model_dump()
    return dict(target or {})


def target_sentence(target) -> str:
    data = _target_data(target)
    shown = money_text(data.get("ev_mm")) or data.get("ev_mm")
    return (f"{data.get('sector')}, enterprise value {shown}, {data.get('geography')}, "
            f"{data.get('ownership')}, {data.get('margin_profile')} EBITDA margins")


def target_phrase(target) -> str:
    """'a Private Healthcare Services company valued at $200.0M EV, with strong EBITDA margins'."""
    data = _target_data(target)
    shown = money_text(data.get("ev_mm")) or data.get("ev_mm")
    text = f"a {data.get('ownership')} {data.get('sector')} company valued at {shown} EV"
    if data.get("geography") and data.get("geography") != ANY_REGION:
        text += f" in the {data.get('geography')}"
    if data.get("margin_profile") == "strong":
        text += ", with EBITDA margins above its sector median"
    return text


def _names(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _target_comp(run: dict, sources: dict) -> dict | None:
    """The cited closed-deal median EV/EBITDA for the target's own sector, if a report used it."""
    sector = _target_data(run.get("target")).get("sector")
    for entry in sorted(sources.values(), key=lambda item: int(item["number"])):
        record = entry.get("record") or {}
        filters = record.get("filters") or {}
        if (entry.get("kind") == "statistic" and filters.get("sector") == sector
                and filters.get("scope") == "sector_only" and filters.get("metric") == "ev_ebitda"
                and str(record.get("calculation") or "").startswith("median")
                and record.get("display")):
            return entry
    return None


def strongest_reports(reports) -> list:
    """Ranking and verification are independent: never silently skip rank two."""
    return sorted(reports, key=lambda report: report.rank)[:STRONGEST_SHOWN]


def buyer_evidence_line(entry: dict | None) -> str:
    """A short comparison using saved ranking displays, not model-invented counts."""
    entry = entry or {}
    direct = (entry.get("direct_closed_count") or {}).get("display")
    near = (entry.get("near_size_closed_count") or {}).get("display")
    if direct is None or near is None:
        return "Open report to review the supporting evidence."
    return (f"{direct} direct-sector {'deal' if str(direct) == '1' else 'deals'} · "
            f"{near} relevant {'deal' if str(near) == '1' else 'deals'} within 2× target size")


def executive_summary_html(run: dict, sources: dict, deals: int | None) -> str:
    """Rank-preserving comparison from saved evidence and the LLM's buyer theses."""
    reports = sorted(run.get("reports") or [], key=lambda report: report.rank)
    ranking = run.get("ranking") or {}
    entries = {item["acquirer_key"]: item for item in ranking.get("buyers") or []}
    parts = [f'<div class="para">Research for {esc(target_phrase(run.get("target")))}. '
             'Prioritize the evidence below; ranking is not a probability of an offer.</div>']
    for report in strongest_reports(reports):
        entry = entries.get(report.acquirer_key)
        status = (f' · {esc(status_text(report.status))}' if report.status != "verified" else "")
        if entry:
            ids = [(entry.get(field) or {}).get("stat_id") for field, _ in RANK_METRICS[1:]]
            body = esc(buyer_evidence_line(entry)) + cite_links([sid for sid in ids if sid], sources)
        elif report.draft is not None:
            body = statement_html(report.draft.summary, sources)
        else:
            body = "No report was generated; inspect the failure details."
        if report.draft and report.status == "verified" and entry:
            thesis = report.draft.summary
            # Legacy drafts often lead with numeric scores/rank. Keep those in
            # the report rather than repeating them in the decision summary.
            if not re.search(r"\b(score|ranks?|ranked)\b", thesis.text, re.I):
                body += '<br>' + statement_html(thesis, sources)
        parts.append(f'<div class="para"><strong>{report.rank}. {esc(report.display_name)}</strong>'
                     f' · {esc(conviction_level(report))} conviction{status}<br>{body}</div>')
    if ranking.get("promoted_keys"):
        parts.append('<div class="para">This saved shortlist uses a buyer-type coverage rule: '
                     'some strategic buyers were included ahead of higher-scoring candidates. '
                     'Their report selection notes identify those exceptions.</div>')
    comp = _target_comp(run, sources)
    if comp:
        r = comp["record"]
        parts.append(f'<div class="para"><strong>Valuation reference.</strong> Closed '
                     f'{esc(r["filters"]["sector"])} deals in {esc(r["filters"].get("size_band"))}: '
                     f'median EV/EBITDA {esc(r["display"])} (n={esc(r.get("n"))}).'
                     f'{cite_html(comp, sources)} Historical context, not a target price.</div>')
    unverified = [r.display_name for r in reports if r.status != "verified"]
    if unverified:
        parts.append(f'<div class="para"><strong>Review required:</strong> '
                     f'{esc(_names(unverified))}. Ranking is unchanged; verification details '
                     'appear with each affected report.</div>')
    parts.append('<div class="para meta">Historical dataset only. Current appetite, funding '
                 'capacity and a live acquisition structure are not established. '
                 '“Verified” means the automated checks passed, not independent due diligence.</div>')
    return '<div class="exec-summary"><h3>Executive Summary</h3>' + ''.join(parts) + '</div>'


def shortlist_table_html(reports) -> str:
    """Rank, buyer, type, conviction and report status for the combined report."""
    rows = ["<tr><th>Rank</th><th>Buyer</th><th>Buyer Type</th><th>Conviction</th>"
            "<th>Report Status</th></tr>"]
    for report in reports:
        level = conviction_level(report)
        rows.append(
            f"<tr><td>{esc(report.rank)}</td><td>{esc(report.display_name)}</td>"
            f"<td>{esc(report.acquirer_type)}</td><td>{pill(level, conviction_class(level))}</td>"
            f"<td>{pill(status_text(report.status), status_class(report.status))}</td></tr>")
    return f'<table class="shortlist">{"".join(rows)}</table>'


def buyer_block_html(report, sources: dict) -> str:
    """One buyer's heading, badges, warning, and the six-section document."""
    level = conviction_level(report)
    parts = [
        f'<h2 id="buyer-{int(report.rank)}">{esc(report.rank)}. {esc(report.display_name)} '
        f'{pill(level, conviction_class(level))} '
        f'{pill(status_text(report.status), status_class(report.status))}</h2>',
        (f'<p class="meta">{esc(report.acquirer_type)} · Score {esc(report.score)} '
         f"(rank {esc(report.rank)})</p>"),
    ]
    if report.draft is None:
        parts.append('<p class="meta">Conviction shown is the starting level.</p>')
    parts.append(verification_html(report))
    if report.draft is not None:
        parts.append(report_body_html(report.draft, sources))
    return f'<div class="buyer-block">{"".join(parts)}</div>'


def focus_header_html(report) -> str:
    """Name and rank, then type, conviction and verification on one line."""
    level = conviction_level(report)
    starting = " (starting level)" if report.draft is None else ""
    return (
        f'<h2>{esc(report.display_name)} <span class="rank-tag">#{esc(report.rank)}</span></h2>'
        f'<p class="meta">{esc(report.acquirer_type)} · '
        f'{pill(f"{level} conviction{starting}", conviction_class(level))}'
        f'{pill(status_text(report.status), status_class(report.status))}</p>'
    )


def _pairs_html(pairs: list[tuple[str, str]]) -> str:
    rows = []
    for label, value in pairs:
        shown = esc(value) if value else "—"
        rows.append(f"<dt>{esc(label)}</dt><dd>{shown}</dd>")
    return f"<dl>{''.join(rows)}</dl>"


def deal_pairs(record: dict) -> list[tuple[str, str]]:
    """The deal card fields, in the tool's display form. Empty when the field is blank."""
    tags = record.get("rationale_tags") or []
    if isinstance(tags, list):
        rationale = ", ".join(str(tag) for tag in tags if tag)
    else:
        rationale = str(tags or "")
    year, quarter = record.get("deal_year"), record.get("deal_quarter")
    date = " ".join(str(part) for part in (year, quarter) if part not in (None, ""))
    values = [
        ("ID", record.get("transaction_id")),
        ("Date", date),
        ("Acquirer", record.get("acquirer")),
        ("Target", record.get("target_company")),
        ("Sector", record.get("sector")),
        ("Deal type", record.get("deal_type")),
        ("Geography", record.get("geography")),
        ("Value", money_text(record.get("deal_size_mm"))),
        ("EV/EBITDA", fmt_multiple(record.get("ev_ebitda_multiple")) or ""),
        ("EV/Revenue", fmt_multiple(record.get("ev_revenue_multiple")) or ""),
        ("Status", record.get("outcome")),
        ("Deal type", record.get("deal_type")),
        ("Rationale", rationale),
    ]
    return [(label, "" if value is None else str(value)) for label, value in values]


def deal_rows_for(entries, source_ids: list[str]) -> list[dict]:
    """Cited deals, in citation order, as display strings."""
    by_id = {entry["id"]: entry for entry in entries or [] if entry.get("kind") == "deal"}
    rows = []
    seen = set()
    for source_id in source_ids:
        if source_id in seen or source_id not in by_id:
            continue
        seen.add(source_id)
        pairs = dict(deal_pairs(by_id[source_id]["record"]))
        rows.append({
            "ID": pairs["ID"], "Date": pairs["Date"], "Acquirer": pairs["Acquirer"],
            "Target": pairs["Target"], "Sector": pairs["Sector"], "Value": pairs["Value"],
            "EV/EBITDA": pairs["EV/EBITDA"], "EV/Revenue": pairs["EV/Revenue"],
            "Status": pairs["Status"], "Deal type": pairs["Deal type"],
            "Rationale": pairs["Rationale"],
        })
    return rows


def format_duration(total_ms: int | None) -> str:
    """A recorded duration for the progress card. Blank when the stage has no times."""
    if total_ms is None:
        return "—"
    if total_ms >= 1000:
        return f"{total_ms / 1000:.1f}s"
    return f"{int(total_ms)} ms"


def _duration_ms(event: dict) -> int | None:
    value = event.get("duration_ms")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _end_ms(event: dict) -> int | None:
    """The event's recorded clock time, in milliseconds. None when it has no timestamp."""
    ts = event.get("ts")
    if not isinstance(ts, str):
        return None
    try:
        stamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(stamp.timestamp() * 1000)


def _stage_ms(matched: list[dict]) -> int | None:
    """Clock span of the stage. Falls back to the sum of durations when no clock exists."""
    ends = []
    starts = []
    for event in matched:
        end = _end_ms(event)
        if end is None:
            continue
        ends.append(end)
        duration = _duration_ms(event)
        starts.append(end if duration is None else end - duration)
    if ends:
        return max(0, max(ends) - (min(starts) if starts else min(ends)))
    durations = [duration for event in matched if (duration := _duration_ms(event)) is not None]
    return int(sum(durations)) if durations else None


def stage_rows(events: list[dict] | None) -> list[dict]:
    """The four research stages, with status and one recorded time each."""
    merged = merge_steps(events or [])
    rows = []
    for stage in RESEARCH_STAGES:
        pairs = {tuple(pair) for pair in stage["events"]}
        matched = [event for event in merged
                   if (event.get("category"), event.get("name")) in pairs]
        statuses = [event.get("status") for event in matched]
        if any(status == "running" for status in statuses):
            status = "running"
        elif any(status == "failed" for status in statuses):
            status = "failed"
        elif matched:
            status = "completed"
        else:
            status = "pending"
        rows.append({"id": stage["id"], "label": stage["label"], "note": stage["note"],
                     "status": status, "duration": format_duration(_stage_ms(matched))})
    return rows
